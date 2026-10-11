"""Best-effort live copy of what an agent is writing or running.

A model call writes plain text, and only the end of the call says what it was:

* the call ended with tool calls: the text was a note about what the model is about to do ("I'll search for the
  official announcement"). It is saved as an `assistant.message` event, so it keeps its place between the tool steps,
  and the live copy is cleared;
* the call ended without tool calls: the text is the answer. It is shown as the answer until the saved message takes over.

A tool call is shown live too, in two phases, so a step never appears all at once:

* while the model writes it. Calls whose arguments are long (see `DRAFTS`) are shown as soon as they are named, with
  the command or file content as it arrives. The note written before them is saved first, so the call shows after it;
* while it runs. A command's output is shown as it arrives (`output`), until the call settles.

The text is kept in one small table row, throttled, so the SSE endpoint can forward it. The row says which kind of
thing it holds (`progress`, `answer`, `tool` or `output`) and carries a `revision` that changes whenever the text is replaced
rather than extended, so a reader can tell "more text" from "different text". Every clear is written, as empty text with
a newer revision: a reader is never left showing words that were saved elsewhere or thrown away. The saved message and the
saved events are authoritative: every failure of the live copy is swallowed and streaming simply switches off for the
run, so it can never fail or slow a task.
"""
import logging
import time
from collections.abc import Callable

from jiter import from_json
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.database.phase3 import TaskStream
from agentforge.integrations.e2b_sandbox import redact_output

log = logging.getLogger(__name__)
FLUSH_SECONDS = 0.12      # at most about eight writes per second
KIND_PROGRESS, KIND_ANSWER, KIND_TOOL, KIND_OUTPUT = "progress", "answer", "tool", "output"
# The calls shown while the model is still writing them, each with the argument that is its text and the one that says
# what it is about. The other calls have short arguments: they show the moment they run.
DRAFTS = {
    "bash": ("command", None), "write": ("content", "path"), "edit": ("new_string", "path"),
    "create_pull_request": ("body", "title"), "create_task_artifact": ("content", "title"), "delegate_task": ("prompt", "title"),
}
DRAFT_CHARS = 20_000      # the most of a call's text shown while it is written (the start of it)
OUTPUT_CHARS = 6_000      # the most of a running command's output shown (the end of it)
DETAIL_CHARS = 120
NO_TOOL = (None, None, None)


class _Call:
    """A tool call shown live: written by the model (`index` is its place in the reply) or running (no index)."""

    def __init__(self, tool: str, call_id: str, index: int | None = None) -> None:
        self.tool, self.call_id, self.index = tool, call_id, index
        self.parts: list[str] = []
        self.size = 0

    def add(self, piece: str) -> None:
        if not piece:
            return
        self.parts.append(piece)
        self.size += len(piece)
        if self.index is None and self.size > 4 * OUTPUT_CHARS:
            # Only the end of a running command's output is shown, so a long run never holds more than that.
            kept = "".join(self.parts)[-2 * OUTPUT_CHARS:]
            self.parts, self.size = [kept], len(kept)

    def view(self) -> tuple[str, str | None]:
        """(the text to show, what the call is about)."""
        raw = "".join(self.parts)
        if self.index is None:
            text = redact_output(raw)
            if len(text) > OUTPUT_CHARS:
                text = text[-OUTPUT_CHARS:]
                text = "…\n" + text[text.find("\n") + 1:]   # whole lines only
            return text, None
        try:
            arguments = from_json(raw.encode(), partial_mode="trailing-strings")
        except Exception:
            arguments = {}
        text_key, detail_key = DRAFTS[self.tool]
        text = arguments.get(text_key) if isinstance(arguments, dict) else None
        detail = arguments.get(detail_key) if isinstance(arguments, dict) and detail_key else None
        detail = " ".join(detail.split())[:DETAIL_CHARS] if isinstance(detail, str) else None
        return (text[:DRAFT_CHARS] if isinstance(text, str) else ""), detail or None


class ReplyStream:
    def __init__(self, sessions, task_id, *, emit: Callable[[str, dict], None] | None = None, clock=time.monotonic) -> None:
        self.sessions, self.task_id, self._clock = sessions, task_id, clock
        self.emit = emit
        self.narrates = emit is not None
        self.disabled = False
        self._parts: list[str] = []     # the text so far, as chunks: joined only when it is written (see `_text`)
        self._length = 0
        self._answer = False           # the call ended without tool calls: the text is the answer
        self._call: _Call | None = None  # the tool call shown instead of the text, while there is one
        self._deferred: Exception | None = None
        self._epoch: int | None = None  # the live segment this stream owns, once it has shown anything
        self._revision = 0
        self._shown = ""                # what the row holds for this stream: the most a reader can have
        self._shown_kind = KIND_PROGRESS
        self._shown_tool = NO_TOOL
        self._last = 0.0

    @property
    def _text(self) -> str:
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""

    @_text.setter
    def _text(self, value: str) -> None:
        self._parts, self._length = ([value] if value else []), len(value)

    # --- one model call -----------------------------------------------------------------------
    def begin(self) -> None:
        """A new model call starts: the previous attempt's text is void, so clear it if it is still on screen.

        Its epoch ends here; the next text opens a new one.
        """
        self._text, self._answer, self._call = "", False, None
        self._sync(force=True)
        self._epoch, self._shown, self._shown_kind, self._shown_tool = None, "", KIND_PROGRESS, NO_TOOL

    def feed(self, chunk: str) -> None:
        """Text the model is writing."""
        if not chunk or self._length >= MAX_ANSWER_CHARS:
            return
        # Appended, not concatenated: a long reply arrives in thousands of chunks.
        chunk = chunk[:MAX_ANSWER_CHARS - self._length]
        self._parts.append(chunk)
        self._length += len(chunk)
        self._sync()

    feed_narration = feed

    def feed_tool(self, index: int, call_id: str, name: str, piece: str) -> None:
        """A tool call the model is writing: `piece` is the next part of its JSON arguments.

        A call in `DRAFTS` is shown as soon as it is named, and its text grows as it arrives. The note written before
        it is saved first, so the call shows after it, in order. Calls not in `DRAFTS` are not shown until they run.
        """
        if name not in DRAFTS or not call_id:
            return
        call = self._call
        if call is not None and call.index == index and call.call_id == call_id:
            call.add(piece)
            self._sync()
            return
        self._call = call = _Call(name, call_id, index)
        call.add(piece)
        try:
            self.commit_narration()
        except Exception as exc:
            # A model's sink swallows what it raises, but an event that could not be saved (a stopped run, a lost lease)
            # is the run's own signal: it is raised again when the call ends (`end`).
            self._deferred = exc

    def end(self) -> None:
        """The model call finished: publish whatever is still buffered."""
        self._sync(force=True)
        if self._deferred is not None:
            exc, self._deferred = self._deferred, None
            raise exc

    # --- a running call -----------------------------------------------------------------------
    def output(self, call_id: str, tool: str) -> Callable[[str], None]:
        """Shows the call `call_id` (the id of its tool.started event) running; returns where its output goes."""
        self._call = call = _Call(tool, call_id)
        self._sync(force=True, new_epoch=True)

        def sink(chunk: str) -> None:
            if self._call is call:
                call.add(chunk)
                self._sync()
        return sink

    def settle(self, call_id: str | None = None) -> None:
        """The tool call shown live is over (it settled, or its row has taken over): clear it. With a `call_id`, only
        if that call is the one shown."""
        call = self._call
        if call is None or (call_id is not None and call.call_id != call_id):
            return
        self._call = None
        self._sync(force=True)

    # --- what the text turned out to be -------------------------------------------------------
    def commit_narration(self) -> None:
        """The call ended with tool calls, so its text was a note: save it as an event, then clear the live copy.

        The event is written first so the same words are never missing from both places. Errors from the event
        (a cancelled run, a lost lease) are not swallowed: they are the run's own signals.
        """
        text = self._text.strip()
        self._text = ""
        if not text or self.emit is None:
            self._sync(force=True)
            return
        epoch = self._epoch
        self.emit("assistant.message", {"text": text, **({"epoch": epoch} if epoch is not None else {})})
        # Words already shown under this epoch were saved as an event; text that follows begins a new epoch
        # so a reader that has taken the event never ignores it.
        self._sync(force=True, new_epoch=True)

    def commit_reply(self) -> None:
        """The call ended without tool calls, so its text is the answer: show it as such."""
        self._answer = True
        self._sync(force=True)

    def discard(self) -> None:
        """Whatever is shown live is no longer wanted (an attempt that was thrown away, or a run that failed)."""
        self._text, self._answer, self._call = "", False, None
        self._sync(force=True)

    # --- the live row -------------------------------------------------------------------------
    def _wanted(self) -> tuple[str, str, tuple]:
        """(text, kind, (tool, call id, detail)) the row should hold."""
        if self._call is not None:
            text, detail = self._call.view()
            return text, KIND_TOOL if self._call.index is not None else KIND_OUTPUT, (self._call.tool, self._call.call_id, detail)
        if not self._text.strip():
            return "", KIND_PROGRESS, NO_TOOL
        return self._text, KIND_ANSWER if self._answer else KIND_PROGRESS, NO_TOOL

    def _sync(self, force: bool = False, new_epoch: bool = False) -> None:
        """Bring the row in line with the buffer. Growth is throttled; any other change is written at once."""
        if self.disabled:
            return
        if not force and not new_epoch and self._epoch is not None and self._clock() - self._last < FLUSH_SECONDS:
            # Unforced syncs come only from new text: plain growth of what is shown, held back inside the throttle
            # window. Returning before the text is joined keeps each chunk's cost independent of the reply's length.
            return
        text, kind, tool = self._wanted()
        # A tool call is shown from the moment it is named, even before it has any text.
        shows = bool(text) or kind in (KIND_TOOL, KIND_OUTPUT)
        if self._epoch is None:
            if shows:
                self._open(text, kind, tool)
            return
        if new_epoch and shows:
            self._open(text, kind, tool)
            return
        if text == self._shown and kind == self._shown_kind and tool == self._shown_tool:
            return
        if kind == self._shown_kind and tool == self._shown_tool and text.startswith(self._shown):
            if force or self._clock() - self._last >= FLUSH_SECONDS:
                self._write(text, kind, tool, self._revision)
            return
        # Replaced, not extended (a clear, a note becoming the answer, a call's output scrolling): a newer revision,
        # written immediately.
        self._write(text, kind, tool, self._revision + 1)

    def _open(self, text: str, kind: str, tool: tuple) -> None:
        """Start a new epoch, already holding `text`, so a reader never sees an empty frame in between."""
        try:
            with self.sessions() as session, session.begin():
                statement = insert(TaskStream).values(task_id=self.task_id, epoch=1, revision=0, kind=kind, text=text,
                                                      tool=tool[0], call_id=tool[1], detail=tool[2])
                statement = statement.on_conflict_do_update(index_elements=[TaskStream.task_id],
                    set_={"epoch": TaskStream.epoch + 1, "revision": TaskStream.revision + 1, "kind": statement.excluded.kind,
                          "text": statement.excluded.text, "tool": statement.excluded.tool, "call_id": statement.excluded.call_id,
                          "detail": statement.excluded.detail, "updated_at": func.now()}
                ).returning(TaskStream.epoch, TaskStream.revision)
                row = session.execute(statement).one()
            self._epoch, self._revision = row.epoch, row.revision
            self._shown, self._shown_kind, self._shown_tool, self._last = text, kind, tool, self._clock()
        except Exception:
            self._switch_off()

    def _write(self, text: str, kind: str, tool: tuple, revision: int) -> None:
        try:
            with self.sessions() as session, session.begin():
                # Fenced by epoch and revision: a newer attempt, a newer clear, or the cleanup after the task ends
                # makes this a no-op, so an older writer can never bring back text that was cleared.
                result = session.execute(update(TaskStream).where(TaskStream.task_id == self.task_id,
                    TaskStream.epoch == self._epoch, TaskStream.revision <= revision)
                    .values(text=text, kind=kind, revision=revision, tool=tool[0], call_id=tool[1], detail=tool[2],
                            updated_at=func.now()))
            if result.rowcount == 0:
                log.debug("live text was superseded; this stream stops publishing")
                self.disabled = True
                return
            self._shown, self._shown_kind, self._shown_tool, self._revision, self._last = text, kind, tool, revision, self._clock()
        except Exception:
            self._switch_off()

    def _switch_off(self) -> None:
        if not self.disabled:
            log.warning("live reply streaming disabled for this task", exc_info=True)
        self.disabled = True


def clear_stream(sessions, task_id) -> None:
    """Clear the live copy once the task ends. Never raises.

    The row stays, emptied and one revision newer: a reader sees the text go (and a late writer is fenced out)
    instead of being left with the last words, and the row disappears with the task.
    """
    try:
        with sessions() as session, session.begin():
            session.execute(update(TaskStream).where(TaskStream.task_id == task_id)
                .values(text="", kind=KIND_PROGRESS, tool=None, call_id=None, detail=None, revision=TaskStream.revision + 1,
                        updated_at=func.now()))
    except Exception:
        log.debug("could not clear live reply copy", exc_info=True)
