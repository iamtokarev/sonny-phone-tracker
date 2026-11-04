"""JSON Lines session log (spec §10): one file per Focus Session, one for idle conversations.

Never sent off the Mac; holds no frames and no audio. Record types are added by later tickets;
every record carries `ts` (epoch seconds) and `type`. Each record also goes to `on_record` when one is set:
the Dashboard's state feed (`sonny.dashboard.feed`), so the log and the page can never disagree.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sonny.events import EndReason, Event, EventType

logger = logging.getLogger(__name__)

Tap = Callable[[dict[str, Any]], None]


class LogWriter:
    """Appends one JSON object per line and flushes it, so a crash loses nothing written."""

    def __init__(self, path: Path, on_record: Tap | None = None) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._on_record = on_record

    def write(self, type: str, **fields: Any) -> None:
        line = json.dumps({"ts": time.time(), "type": str(type), **fields}, default=str)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()
            if self._on_record is not None:  # under the lock, so the tap sees records in file order
                try:
                    self._on_record(json.loads(line))  # the record as the file holds it
                except Exception:
                    logger.exception("session log tap failed")

    def write_event(self, event: Event) -> None:
        self.write(event.type, **event.data)

    def close(self) -> None:
        with self._lock:
            self._file.close()


class SessionLog:
    def __init__(self, log_dir: Path | str, on_record: Tap | None = None) -> None:
        self.dir = Path(log_dir)
        self.on_record = on_record  # every record any of this log's files gets; may be set later
        self.session: LogWriter | None = None
        self._idle: LogWriter | None = None
        self._session_ended = False  # `session.ended` is written; the file stays open for an extend

    def _tap(self, record: dict[str, Any]) -> None:
        if self.on_record is not None:
            self.on_record(record)

    @property
    def idle(self) -> LogWriter:
        if self._idle is None:
            self._idle = LogWriter(self.dir / "idle.jsonl", self._tap)
        return self._idle

    def start_session(self) -> LogWriter:
        if self.session is not None:
            raise RuntimeError("a session log is already open")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.session = LogWriter(self.dir / f"session-{stamp}.jsonl", self._tap)
        self._session_ended = False
        return self.session

    def end_session(self, reason: EndReason, keep_open: bool = False) -> None:
        """Log `session.ended` with `reason`, unless it is already logged, and close the file; a no-op with
        no session open. `keep_open` leaves the file open for what follows a time's-up (an extend)."""
        if self.session is None:
            return
        if not self._session_ended:
            self.session.write(EventType.SESSION_ENDED, reason=reason)
            self._session_ended = True
        if not keep_open:
            self.session.close()
            self.session = None

    def reopen_session(self) -> None:
        """An extend after time's up continues the session in the same file; its next end is logged."""
        self._session_ended = False

    def close(self) -> None:
        if self._idle is not None:
            self._idle.close()
            self._idle = None
