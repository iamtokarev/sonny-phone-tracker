"""The conductor (spec §6, §7 "Conductor and history"): which events wake the agent, in what order, and
the JSON wake-up message each one becomes.

- System hooks are fixed here: an event outside `WAKES` is logged only. Attention events wake the agent only
  outside an Excuse, Pause or Blind (they say so in their `wakes`), within the Nudge limits (`NudgeGuard`):
  a Nudge event inside the gap waits while others go ahead, and a stale one is dropped when its turn comes.
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
from sonny.focus.nudges import STARTED, Delivery, NudgeGuard

WAKES = {
    EventType.SESSION_STARTED,
    EventType.SESSION_ENDED,
    EventType.EXCUSE_EXPIRED,
    EventType.TIMER,
    EventType.SESSION_REMAINING,
    EventType.PHONE_USE_CONTINUING,
    EventType.AWAY_CONTINUING,
    EventType.FOCUSED_RESUMED,
    *STARTED,
}
ATTENTION = {*STARTED, EventType.FOCUSED_RESUMED}
WAKING_ENDS = {EndReason.COMPLETED, EndReason.PAUSE_TIMEOUT}
HOOK_EVENTS = set(AGENT_HOOKS)
SESSION_SCOPED = HOOK_EVENTS | ATTENTION | {EventType.EXCUSE_EXPIRED}  # stale once their session ends
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
    def __init__(self, snapshot: Callable[[], dict], nudges: NudgeGuard | None = None) -> None:
        self._snapshot = snapshot
        self._nudges = nudges  # without one, attention events wake no one
        self._queue: list[tuple[EventType, list[dict]]] = []  # kind, and the data of each collapsed event

    @property
    def pending(self) -> int:
        return len(self._queue)

    def offer(self, event: Event) -> bool:
        """Queue `event` if a system hook wakes the agent for it; say whether it did."""
        if event.type in ATTENTION:
            if self._nudges is None:
                return False
            self._nudges.observe(event)
            if event.data["excused"] or (event.type in STARTED and not event.data["wakes"]):
                return False  # an Excuse, Pause or Blind; `focused.resumed` is judged when it is delivered
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
        """The next wake-up message, or None with nothing queued (or nothing that may wake the agent yet)."""
        i = 0
        while i < len(self._queue):
            kind, data = self._queue[i]
            delivery = Delivery.DELIVER if self._nudges is None else self._nudges.delivery(kind, data[-1])
            if delivery is Delivery.HOLD:
                i += 1
                continue
            del self._queue[i]
            if delivery is Delivery.DELIVER:
                return wakeup_message({"event": str(kind), **self._fields(kind, data), "state": self._snapshot()})
        return None

    def _fields(self, kind: EventType, data: list[dict]) -> dict:
        if kind in HOOK_EVENTS:
            return {"hooks": [{"id": d["hook_id"], "note": d["note"]} for d in data]}
        fields = dict(data[-1])  # the latest of a collapsed kind
        if kind in ATTENTION:
            del fields["wakes"], fields["excused"]  # the conductor's to read, not the agent's
            if kind is EventType.FOCUSED_RESUMED and self._nudges is not None:
                fields["episode"] = self._nudges.closed_view(fields["episode"])  # the one just over
        return fields

    def owner_wakeup(self, text: str) -> LLMStandardMessage:
        return wakeup_message({"event": str(EventType.OWNER_SPOKE), "text": text, "state": self._snapshot()})
