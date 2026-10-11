"""Best-effort live copy of the text a model is writing.

A model call writes plain text, and only the end of the call says what it was:

* the call ended with tool calls: the text was a note about what the model is about to do ("I'll search for the
  official announcement"). It is saved as an `assistant.message` event, so it keeps its place between the tool steps,
  and the live copy is cleared;
* the call ended without tool calls: the text is the answer. It is shown as the answer until the saved message takes over.

The text is kept in one small table row, throttled, so the SSE endpoint can forward it. The row says which kind of
text it holds (`progress` or `answer`) and carries a `revision` that changes whenever the text is replaced rather than
extended, so a reader can tell "more text" from "different text". Every clear is written, as empty text with a newer
revision: a reader is never left showing words that were saved elsewhere or thrown away. The saved message and the
saved events are authoritative: every failure of the live copy is swallowed and streaming simply switches off for the
run, so it can never fail or slow a task.
"""
import logging
import time
from collections.abc import Callable

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.contracts.tasks import MAX_ANSWER_CHARS
from agentforge.database.phase3 import TaskStream

log = logging.getLogger(__name__)
FLUSH_SECONDS = 0.12      # at most about eight writes per second
KIND_PROGRESS, KIND_ANSWER = "progress", "answer"


class ReplyStream:
    def __init__(self, sessions, task_id, *, emit: Callable[[str, dict], None] | None = None, clock=time.monotonic) -> None:
        self.sessions, self.task_id, self._clock = sessions, task_id, clock
        self.emit = emit
        self.narrates = emit is not None
        self.disabled = False
        self._parts: list[str] = []     # the text so far, as chunks: joined only when it is written (see `_text`)
        self._length = 0
        self._answer = False           # the call ended without tool calls: the text is the answer
        self._epoch: int | None = None  # the live segment this stream owns, once it has shown anything
        self._revision = 0
        self._shown = ""                # what the row holds for this stream: the most a reader can have
        self._shown_kind = KIND_PROGRESS
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
        self._text, self._answer = "", False
        self._sync(force=True)
        self._epoch, self._shown, self._shown_kind = None, "", KIND_PROGRESS

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

    def end(self) -> None:
        """The model call finished: publish whatever is still buffered."""
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
        self._text, self._answer = "", False
        self._sync(force=True)

    # --- the live row -------------------------------------------------------------------------
    def _wanted(self) -> tuple[str, str]:
        if not self._text.strip():
            return "", KIND_PROGRESS
        return self._text, KIND_ANSWER if self._answer else KIND_PROGRESS

    def _sync(self, force: bool = False, new_epoch: bool = False) -> None:
        """Bring the row in line with the buffer. Growth is throttled; any other change is written at once."""
        if self.disabled:
            return
        if not force and not new_epoch and self._epoch is not None and self._clock() - self._last < FLUSH_SECONDS:
            # Unforced syncs come only from new text: plain growth of what is shown, held back inside the throttle
            # window. Returning before the text is joined keeps each chunk's cost independent of the reply's length.
            return
        text, kind = self._wanted()
        if self._epoch is None:
            if text:
                self._open(text, kind)
            return
        if new_epoch and text:
            self._open(text, kind)
            return
        if text == self._shown and kind == self._shown_kind:
            return
        if kind == self._shown_kind and text.startswith(self._shown):
            if force or self._clock() - self._last >= FLUSH_SECONDS:
                self._write(text, kind, self._revision)
            return
        # Replaced, not extended (a clear, or a note becoming the answer): a newer revision, written immediately.
        self._write(text, kind, self._revision + 1)

    def _open(self, text: str, kind: str) -> None:
        """Start a new epoch, already holding `text`, so a reader never sees an empty frame in between."""
        try:
            with self.sessions() as session, session.begin():
                statement = insert(TaskStream).values(task_id=self.task_id, epoch=1, revision=0, kind=kind, text=text)
                statement = statement.on_conflict_do_update(index_elements=[TaskStream.task_id],
                    set_={"epoch": TaskStream.epoch + 1, "revision": TaskStream.revision + 1, "kind": statement.excluded.kind,
                          "text": statement.excluded.text, "updated_at": func.now()}
                ).returning(TaskStream.epoch, TaskStream.revision)
                row = session.execute(statement).one()
            self._epoch, self._revision = row.epoch, row.revision
            self._shown, self._shown_kind, self._last = text, kind, self._clock()
        except Exception:
            self._switch_off()

    def _write(self, text: str, kind: str, revision: int) -> None:
        try:
            with self.sessions() as session, session.begin():
                # Fenced by epoch and revision: a newer attempt, a newer clear, or the cleanup after the task ends
                # makes this a no-op, so an older writer can never bring back text that was cleared.
                result = session.execute(update(TaskStream).where(TaskStream.task_id == self.task_id,
                    TaskStream.epoch == self._epoch, TaskStream.revision <= revision)
                    .values(text=text, kind=kind, revision=revision, updated_at=func.now()))
            if result.rowcount == 0:
                log.debug("live text was superseded; this stream stops publishing")
                self.disabled = True
                return
            self._shown, self._shown_kind, self._revision, self._last = text, kind, revision, self._clock()
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
                .values(text="", kind=KIND_PROGRESS, revision=TaskStream.revision + 1, updated_at=func.now()))
    except Exception:
        log.debug("could not clear live reply copy", exc_info=True)
