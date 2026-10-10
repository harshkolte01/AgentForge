"""Best-effort live copy of the reply a model is writing.

A reply is written by the model inside a JSON envelope. This keeps the person-facing field
(`response` or `summary`) in a small table as it grows, throttled, so the SSE endpoint can
forward it. The saved message stays authoritative: every failure here is swallowed and
streaming simply switches off for the run, so it can never fail or slow a task.
"""
import logging
import time

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert

from agentforge.database.phase3 import TaskStream
from agentforge.llm.streaming import JsonStringFieldStream

log = logging.getLogger(__name__)
MAX_CHARS = 8000          # the saved message is capped the same way
FLUSH_SECONDS = 0.12      # at most about eight writes per second


class ReplyStream:
    def __init__(self, sessions, task_id, field: str, *, clock=time.monotonic) -> None:
        self.sessions, self.task_id, self.field, self._clock = sessions, task_id, field, clock
        self.disabled = False
        self._reader = JsonStringFieldStream(field)
        self._text = ""
        self._epoch: int | None = None
        self._flushed = 0
        self._last = 0.0

    def begin(self) -> None:
        """A new model call starts: forget the previous attempt's text (its epoch ends at the first new token)."""
        self._reader = JsonStringFieldStream(self.field)
        self._text, self._epoch, self._flushed = "", None, 0

    def feed(self, chunk: str) -> None:
        if self.disabled:
            return
        piece = self._reader.feed(chunk)
        if not piece:
            return
        self._text = (self._text + piece)[:MAX_CHARS]
        if self._epoch is None:
            self._start()
            if self.disabled:
                return
        if self._clock() - self._last >= FLUSH_SECONDS:
            self._write()

    def end(self) -> None:
        """The model call finished: publish whatever is still buffered."""
        if not self.disabled and self._epoch is not None and len(self._text) > self._flushed:
            self._write()

    def _start(self) -> None:
        try:
            with self.sessions() as session, session.begin():
                statement = insert(TaskStream).values(task_id=self.task_id, epoch=1, text="")
                statement = statement.on_conflict_do_update(index_elements=[TaskStream.task_id],
                    set_={"epoch": TaskStream.epoch + 1, "text": "", "updated_at": func.now()}).returning(TaskStream.epoch)
                self._epoch = session.execute(statement).scalar_one()
            self._last = float("-inf")  # the first text after opening an epoch is published immediately
        except Exception:
            self._switch_off()

    def _write(self) -> None:
        try:
            with self.sessions() as session, session.begin():
                # Fenced by epoch: a newer attempt, or the cleanup after the task ends, makes this a no-op.
                session.execute(update(TaskStream).where(TaskStream.task_id == self.task_id, TaskStream.epoch == self._epoch)
                    .values(text=self._text))
            self._flushed, self._last = len(self._text), self._clock()
        except Exception:
            self._switch_off()

    def _switch_off(self) -> None:
        if not self.disabled:
            log.warning("live reply streaming disabled for this task", exc_info=True)
        self.disabled = True


def clear_stream(sessions, task_id) -> None:
    """Drop the transient copy once the task ends. Never raises."""
    try:
        with sessions() as session, session.begin():
            session.execute(delete(TaskStream).where(TaskStream.task_id == task_id))
    except Exception:
        log.debug("could not clear live reply copy", exc_info=True)
