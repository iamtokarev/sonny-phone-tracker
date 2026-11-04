"""The Dashboard's state feed (spec §4): one `UiFeed` turns everything Sonny logs or publishes into envelopes.

An envelope is `{seq, t, type, data}`: `type` is the log record's or `EventType`'s string verbatim, `t` its epoch
time, `data` the rest of the record; `seq` rises by one per envelope for the run. Sources:

- every session log record (`SessionLog.on_record`), so the log and the page never disagree;
- every `EventBus` event, including any that is never logged. Today every source publishes an event and then
  logs the same one; the feed sends such an event once, as its log record;
- a `health` envelope every `health_every_s` (`run_health`): link state, camera fps, detect ms, stream lag,
  last LLM latency, reconnects and Blind spells.

`snapshot()` is what a page applies on connect: the session (raw times, so the page ticks the timer itself),
the Attention State, the open Distraction Episode, the agent phase, the latest health, and the last
`recent_max` envelopes. Health ticks are not kept in `recent`; the snapshot carries the latest one.
"""

import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sonny.config import Dashboard
from sonny.events import EventBus

logger = logging.getLogger(__name__)

Envelope = dict[str, Any]

# A bus event not matched by its log record within this long is taken as never logged.
_MATCH_S = 5.0


@dataclass(frozen=True)
class Readings:
    """What the health tick reads live from Sonny (all optional: the dev replayer has no robot)."""

    link_down: str | None = None  # `LinkWatch.down`
    frames: int | None = None  # `MediaHub.frames`, the camera frames received so far
    frame_t: float | None = None  # epoch time of the newest camera frame


class UiFeed:
    def __init__(self, cfg: Dashboard, bus: EventBus | None = None, now: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self._now = now
        self._lock = threading.RLock()
        self._seq = 0
        self._recent: deque[Envelope] = deque(maxlen=cfg.recent_max)
        self._listeners: list[Callable[[Envelope], None]] = []
        self._events = bus.subscribe() if bus is not None else None
        self._sent_from_bus: deque[tuple[float, tuple[str, str]]] = deque()  # waiting for their log record
        self._session: dict[str, Any] | None = None
        self._attention: str | None = None
        self._episode: dict[str, Any] | None = None
        self._phase = "idle"
        self._link: dict[str, Any] = {
            "down": None,
            "camera_fps": None,
            "detect_ms": None,
            "stream_lag_s": None,
            "llm_latency_s": None,
            "reconnects": 0,
            "blind_spells": 0,
        }
        self._frames: tuple[float, int] | None = None  # (when, MediaHub.frames) at the last tick

    # ---------------------------------------------------------------- inputs

    def listen(self, send: Callable[[Envelope], None]) -> None:
        """Call `send` with every new envelope, in `seq` order (under the feed's lock: keep it quick)."""
        with self._lock:
            self._listeners.append(send)

    def on_record(self, record: dict[str, Any]) -> None:
        """One session log record (`{ts, type, ...}`), as the file holds it."""
        type = record["type"]
        data = {k: v for k, v in record.items() if k not in ("ts", "type")}
        key = (type, _canonical(data))
        with self._lock:
            self._pump(logged=key)
            if self._take_sent(key):
                return  # this event already went out from the bus
            self._send(record["ts"], type, data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._pump()
            return {
                "seq": self._seq,
                "server_now": self._now(),
                "session": None if self._session is None else dict(self._session),
                "attention": self._attention,
                "episode": None if self._episode is None else {**self._episode, "nudges": [*self._episode["nudges"]]},
                "agent": {"phase": self._phase},
                "link": dict(self._link),
                "recent": list(self._recent),
            }

    def tick(self, readings: Readings) -> None:
        """One health tick: send a `health` envelope (and any bus event still waiting)."""
        now = self._now()
        with self._lock:
            self._pump()
            self._link["down"] = readings.link_down
            if readings.frames is not None:
                if self._frames is not None and now > self._frames[0]:
                    self._link["camera_fps"] = round((readings.frames - self._frames[1]) / (now - self._frames[0]), 1)
                self._frames = (now, readings.frames)
            if readings.frame_t:
                self._link["stream_lag_s"] = round(max(0.0, now - readings.frame_t), 3)
            self._send(now, "health", dict(self._link), keep=False)

    def run_health(self, stop: threading.Event, read: Callable[[], Readings]) -> threading.Thread:
        """Tick every `health_every_s` in a daemon thread until `stop` is set."""

        def loop() -> None:
            while not stop.wait(self.cfg.health_every_s):
                try:
                    self.tick(read())
                except Exception:
                    logger.exception("dashboard health tick failed")

        thread = threading.Thread(target=loop, name="sonny-dashboard-health", daemon=True)
        thread.start()
        return thread

    # ---------------------------------------------------------------- internals

    def _pump(self, logged: tuple[str, str] | None = None) -> None:
        """Send the bus events waiting in the queue. A source publishes before it logs, so an event whose log
        record is arriving now (`logged`) is left to that record; any other one goes out now, and its record,
        if one follows, is dropped."""
        if self._events is None:
            return
        now = self._now()
        while self._sent_from_bus and now - self._sent_from_bus[0][0] > _MATCH_S:
            self._sent_from_bus.popleft()
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            data = json.loads(json.dumps(event.data, default=str))
            key = (str(event.type), _canonical(data))
            if key == logged:
                logged = None  # the record is this event; it goes out as the record
                continue
            self._sent_from_bus.append((now, key))
            self._send(event.t, str(event.type), data)

    def _take_sent(self, key: tuple[str, str]) -> bool:
        for i, (_, sent) in enumerate(self._sent_from_bus):
            if sent == key:
                del self._sent_from_bus[i]
                return True
        return False

    def _send(self, t: float, type: str, data: dict[str, Any], keep: bool = True) -> None:
        self._seq += 1
        envelope = {"seq": self._seq, "t": t, "type": type, "data": data}
        self._apply(envelope)
        if keep:
            self._recent.append(envelope)
        for send in self._listeners:
            try:
                send(envelope)
            except Exception:
                logger.exception("dashboard listener failed")

    def _apply(self, e: Envelope) -> None:
        """Follow the state the snapshot carries."""
        t, type, data = e["t"], e["type"], e["data"]
        s = self._session
        if type == "session.started":
            self._session = {
                "task": data.get("task", ""),
                "started_at": t,
                "duration_s": float(data.get("duration_min", 0)) * 60,
                "paused_since": None,
                "paused_total_s": 0.0,
                "time_up_at": None,
                "blind": None,
            }
            self._attention, self._episode = "focused", None
        elif type == "session.ended":
            if data.get("reason") == "completed" and s is not None:
                s["time_up_at"] = t  # the extend grace: the session stays open
                s["blind"] = None
                self._attention, self._episode = None, None
            else:
                self._session, self._attention, self._episode = None, None, None
        elif type == "tool_call" and s is not None:
            self._apply_tool(s, t, data.get("name"), data.get("result") or {})
        elif type == "attention" and s is not None:
            self._attention = data.get("state")
            if self._episode is not None and self._attention != "focused":
                self._episode["kind"] = self._attention
        elif type == "episode_opened":
            self._episode = {"number": data.get("number"), "kind": data.get("kind"), "started_at": t, "nudges": []}
        elif type == "episode_closed":
            self._episode = None
        elif type == "nudge":
            if self._episode is not None and data.get("episode") == self._episode["number"]:
                self._episode["nudges"].append(data.get("intensity"))
        elif type == "blind.started":
            self._link["blind_spells"] += 1
            if s is not None:
                s["blind"] = data.get("reason")
        elif type == "blind.ended" and s is not None:
            s["blind"] = None
        elif type == "reconnect":
            self._link["reconnects"] += 1
        elif type == "detections":
            self._link["detect_ms"] = data.get("detect_ms")
        elif type == "llm_call":
            self._link["llm_latency_s"] = data.get("latency_s")
        elif type == "agent.phase":
            self._phase = data.get("phase", self._phase)

    @staticmethod
    def _apply_tool(s: dict[str, Any], t: float, name: str | None, result: dict[str, Any]) -> None:
        if name == "pause" and result.get("paused"):
            s["paused_since"] = t
        elif name == "resume" and result.get("resumed") and s["paused_since"] is not None:
            s["paused_total_s"] += t - s["paused_since"]
            s["paused_since"] = None
        elif name == "extend" and "extended_by_min" in result:
            if result.get("continued") and s["time_up_at"] is not None:
                # As the session clock does: elapsed time stays at the old Duration.
                s["paused_total_s"] = t - s["started_at"] - s["duration_s"]
                s["time_up_at"] = None
            s["duration_s"] += float(result["extended_by_min"]) * 60


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, default=str)
