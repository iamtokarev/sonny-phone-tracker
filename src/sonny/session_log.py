"""JSON Lines session log (spec §10): one file per Focus Session, one for idle conversations.

Never sent anywhere; holds no frames and no audio. Record types are added by later tickets;
every record carries `ts` (epoch seconds) and `type`.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sonny.events import EndReason, Event, EventType


class LogWriter:
    """Appends one JSON object per line and flushes it, so a crash loses nothing written."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, type: str, **fields: Any) -> None:
        line = json.dumps({"ts": time.time(), "type": str(type), **fields}, default=str)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def write_event(self, event: Event) -> None:
        self.write(event.type, **event.data)

    def close(self) -> None:
        with self._lock:
            self._file.close()


class SessionLog:
    def __init__(self, log_dir: Path | str) -> None:
        self.dir = Path(log_dir)
        self.session: LogWriter | None = None
        self._idle: LogWriter | None = None

    @property
    def idle(self) -> LogWriter:
        if self._idle is None:
            self._idle = LogWriter(self.dir / "idle.jsonl")
        return self._idle

    def start_session(self) -> LogWriter:
        if self.session is not None:
            raise RuntimeError("a session log is already open")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.session = LogWriter(self.dir / f"session-{stamp}.jsonl")
        return self.session

    def end_session(self, reason: EndReason) -> None:
        """Log `session.ended` with `reason` and close the file; a no-op with no session open."""
        if self.session is None:
            return
        self.session.write(EventType.SESSION_ENDED, reason=reason)
        self.session.close()
        self.session = None

    def close(self) -> None:
        if self._idle is not None:
            self._idle.close()
            self._idle = None
