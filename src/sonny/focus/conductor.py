"""The conductor (spec §6, §7 "Conductor and history"): which events wake the agent, in what order, and
the JSON wake-up message each one becomes.

- System hooks are fixed here: an event outside `WAKES` is logged only.
- One wake-up at a time: events wait in a queue, and same-kind events collapse into one wake-up.
- Owner turns don't queue: they reach the LLM through the voice pipeline, so they always go ahead of
  queued events. They become the same JSON message (`owner_wakeup`).
- The state snapshot is read when a wake-up is delivered, so it is never stale.

When the agent is free is the voice pipeline's to say (`AgentActivity`); this module holds no clock.
"""

import json
from collections.abc import Callable

from pipecat.processors.aggregators.llm_context import LLMStandardMessage

from sonny.events import EndReason, Event, EventType
from sonny.focus.clock import AGENT_HOOKS

WAKES = {
    EventType.SESSION_STARTED,
    EventType.SESSION_ENDED,
    EventType.EXCUSE_EXPIRED,
    EventType.TIMER,
    EventType.SESSION_REMAINING,
}
WAKING_ENDS = {EndReason.COMPLETED, EndReason.PAUSE_TIMEOUT}
HOOK_EVENTS = set(AGENT_HOOKS)
SESSION_SCOPED = HOOK_EVENTS | {EventType.EXCUSE_EXPIRED}  # stale once their session ends
_PREFIX = '{"event": '


def wakeup_message(payload: dict) -> LLMStandardMessage:
    """`payload` starts with its "event" key, which is what marks a wake-up in the context."""
    return {"role": "user", "content": json.dumps(payload)}


def is_wakeup(message: object) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(_PREFIX)
    )


def wakeup_payload(message: object) -> dict | None:
    if isinstance(message, dict) and is_wakeup(message):
        return json.loads(message["content"])
    return None


class Conductor:
    def __init__(self, snapshot: Callable[[], dict]) -> None:
        self._snapshot = snapshot
        self._queue: list[tuple[EventType, list[dict]]] = []  # kind, and the data of each collapsed event

    @property
    def pending(self) -> int:
        return len(self._queue)

    def offer(self, event: Event) -> bool:
        """Queue `event` if a system hook wakes the agent for it; say whether it did."""
        if event.type not in WAKES:
            return False
        if event.type is EventType.SESSION_ENDED:
            self._queue = [(kind, data) for kind, data in self._queue if kind not in SESSION_SCOPED]
            if event.data.get("reason") not in WAKING_ENDS:
                return False
        for kind, data in self._queue:
            if kind is event.type:
                data.append(event.data)
                return True
        self._queue.append((event.type, [event.data]))
        return True

    def next_wakeup(self) -> LLMStandardMessage | None:
        """The next wake-up message, or None with nothing queued."""
        if not self._queue:
            return None
        kind, data = self._queue.pop(0)
        if kind in HOOK_EVENTS:
            fields: dict = {"hooks": [{"id": d["hook_id"], "note": d["note"]} for d in data]}
        else:
            fields = dict(data[-1])  # the latest of a collapsed kind
        return wakeup_message({"event": str(kind), **fields, "state": self._snapshot()})

    def owner_wakeup(self, text: str) -> LLMStandardMessage:
        return wakeup_message({"event": str(EventType.OWNER_SPOKE), "text": text, "state": self._snapshot()})
