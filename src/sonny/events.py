"""The typed in-process event bus every source publishes to (spec §6)."""

import queue
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    OWNER_SPOKE = "owner.spoke"
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    PHONE_USE_STARTED = "phone_use.started"
    AWAY_STARTED = "away.started"
    FOCUSED_RESUMED = "focused.resumed"
    EXCUSE_EXPIRED = "excuse.expired"
    PAUSE_EXPIRED = "pause.expired"
    TIMER = "timer"
    SESSION_REMAINING = "session.remaining"
    PHONE_USE_CONTINUING = "phone_use.continuing"
    AWAY_CONTINUING = "away.continuing"
    LINK_RESTORED = "link.restored"
    BLIND_STARTED = "blind.started"
    BLIND_ENDED = "blind.ended"
    WAKE = "wake"


class EndReason(StrEnum):
    """Why a Focus Session ended; carried as `reason` on `session.ended`."""

    COMPLETED = "completed"
    ENDED_BY_OWNER = "ended_by_owner"
    PAUSE_TIMEOUT = "pause_timeout"
    ENDED_WHILE_BLIND = "ended_while_blind"
    ENDED_DURING_SLEEP = "ended_during_sleep"
    APP_QUIT = "app_quit"


@dataclass(frozen=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    t: float = field(default_factory=time.time)


class EventBus:
    """Fans each published event out to every matching subscriber's queue. Thread-safe."""

    def __init__(self) -> None:
        self._subs: list[tuple[queue.Queue[Event], frozenset[EventType] | None]] = []
        self._lock = threading.Lock()

    def subscribe(self, types: Iterable[EventType] | None = None) -> queue.Queue[Event]:
        """A new queue receiving events of `types`, or all events when `types` is None."""
        q: queue.Queue[Event] = queue.Queue()
        with self._lock:
            self._subs.append((q, None if types is None else frozenset(types)))
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            self._subs = [(s, t) for s, t in self._subs if s is not q]

    def publish(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for q, types in subs:
            if types is None or event.type in types:
                q.put_nowait(event)
