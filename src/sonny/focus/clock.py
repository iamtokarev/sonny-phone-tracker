"""The session clock (spec §2 "Session clock", §3, §7 "Tools", "Limits and guards"): session time, Pause,
Excuse expiry and hook timers. The agent never keeps time; it reads the snapshot.

Every bound and default lives here, in code, and comes from config. A call that breaks a bound raises
`Refused` with the reason the agent gets back. Session time stops during a Pause; Excuses and hooks run on
session time. `tick()` publishes what fell due since the last tick.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sonny.config import Config
from sonny.events import EndReason, Event, EventBus, EventType
from sonny.session_log import SessionLog

AGENT_HOOKS = (EventType.TIMER, EventType.SESSION_REMAINING)


class Refused(Exception):
    """A tool call the code won't carry out; the message is the reason the agent gets."""


@dataclass
class Hook:
    id: int
    event: EventType
    note: str
    due_s: float | None = None  # session time it fires at (`timer`)
    at_remaining_s: float | None = None  # remaining time it fires at (`session.remaining`)


@dataclass
class Session:
    task: str
    duration_s: float
    started: float  # clock time
    paused_total_s: float = 0.0
    paused_since: float | None = None
    time_up_at: float | None = None  # clock time of time's up; the session is in its extend grace
    excuse_until_s: float | None = None  # session time the current Excuse ends
    excuse_reason: str = ""
    excuses: list[dict] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)
    next_hook: int = 1


class FocusClock:
    def __init__(self, cfg: Config, log: SessionLog, bus: EventBus, now: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self._log, self._bus, self._now = log, bus, now
        self._session: Session | None = None
        self._started = 0

    # ---------------------------------------------------------------- state

    @property
    def number(self) -> int | None:
        """Which session is open (1 for the app's first), or None."""
        return self._started if self._session is not None else None

    @property
    def running(self) -> bool:
        """A session is open and its time isn't up (the grace after time's up doesn't count)."""
        return self._session is not None and self._session.time_up_at is None

    @property
    def paused(self) -> bool:
        s = self._session  # read once: other threads read these while the conductor ticks
        return s is not None and s.paused_since is not None

    @property
    def excused(self) -> bool:
        s = self._session
        return s is not None and s.excuse_until_s is not None

    def _elapsed_s(self, s: Session) -> float:
        until = s.paused_since if s.paused_since is not None else self._now()
        return min(s.duration_s, until - s.started - s.paused_total_s)

    def _remaining_s(self, s: Session) -> float:
        return s.duration_s - self._elapsed_s(s)

    def _need_session(self) -> Session:
        if self._session is None or self._session.time_up_at is not None:
            raise Refused("no Focus Session is running")
        return self._session

    def snapshot(self) -> dict:
        s = self._session
        if s is None:
            return {"session": None}
        session = {
            "task": s.task,
            "duration_min": round(s.duration_s / 60, 1),
            "elapsed_min": round(self._elapsed_s(s) / 60, 1),
            "remaining_min": round(self._remaining_s(s) / 60, 1),
            "paused": s.paused_since is not None,
            "time_up": s.time_up_at is not None,
        }
        if s.time_up_at is not None:
            session["can_still_extend"] = True  # until the grace closes the session
        excuse = None
        if s.excuse_until_s is not None:
            left = (s.excuse_until_s - self._elapsed_s(s)) / 60
            excuse = {"reason": s.excuse_reason, "minutes_left": round(left, 1)}
        return {
            "session": session,
            "excuse": excuse,
            "excuses_granted": list(s.excuses),
            "active_hooks": [self._hook_view(h) for h in s.hooks],
        }

    def _hook_view(self, h: Hook) -> dict:
        view: dict = {"id": h.id, "event": str(h.event), "note": h.note}
        if h.due_s is not None and self._session is not None:
            view["fires_in_s"] = round(h.due_s - self._elapsed_s(self._session))
        if h.at_remaining_s is not None:
            view["at_remaining_min"] = round(h.at_remaining_s / 60, 1)
        return view

    # ---------------------------------------------------------------- tools

    def start(self, task: str, duration_minutes: float | None = None) -> dict:
        """Start a session; during one, this is a Task switch: the old one ends and a new one starts."""
        c = self.cfg.session
        minutes = c.duration_default_min if duration_minutes is None else duration_minutes
        minutes = max(c.duration_min_min, min(c.duration_max_min, round(minutes)))
        result: dict = {"started": True, "task": task, "duration_min": minutes}
        if self._session is not None:
            if self._session.time_up_at is None:
                result["switched_from"] = self._session.task
                self._end(EndReason.ENDED_BY_OWNER)
            else:
                self._close()  # a new Task in the grace after time's up
        self._log.start_session()
        self._session = Session(task, minutes * 60.0, self._now())
        self._started += 1
        self._emit(Event(EventType.SESSION_STARTED, {"task": task, "duration_min": minutes}))
        return result

    def extend(self, minutes: float | None = None) -> dict:
        """Add time; in the grace after time's up, this continues the same session."""
        c = self.cfg.session
        s = self._session
        if s is None:
            raise Refused("no Focus Session is running, and the time to extend after time's up has passed")
        minutes = c.extend_default_min if minutes is None else round(minutes)
        if minutes < 1:
            raise Refused("extend needs at least 1 minute")
        room = c.duration_max_min - s.duration_s / 60
        if room < 1:
            raise Refused(f"the session is already {c.duration_max_min} min, the longest a session can be")
        minutes = min(minutes, room)
        result: dict = {"extended_by_min": round(minutes, 1)}
        if s.time_up_at is not None:
            # Elapsed time stays at the old Duration; the extension is all that is left.
            s.paused_total_s = self._now() - s.started - s.duration_s
            s.time_up_at = None
            self._log.reopen_session()
            result["continued"] = True
        s.duration_s += minutes * 60
        result["remaining_min"] = round(self._remaining_s(s) / 60, 1)
        return result

    def pause(self) -> dict:
        s = self._need_session()
        if s.paused_since is not None:
            raise Refused("already paused")
        s.paused_since = self._now()
        return {"paused": True, "ends_session_after_min": self.cfg.session.pause_max_min}

    def resume(self) -> dict:
        s = self._need_session()
        if s.paused_since is None:
            raise Refused("not paused")
        s.paused_total_s += self._now() - s.paused_since
        s.paused_since = None
        return {"resumed": True, "remaining_min": round(self._remaining_s(s) / 60, 1)}

    def end(self) -> dict:
        self._need_session()
        self._end(EndReason.ENDED_BY_OWNER)
        return {"ended": True}

    def grant_excuse(self, reason: str, minutes: float | None = None) -> dict:
        """Always granted while a session runs; a nonsense length gets the default."""
        s = self._need_session()
        default = self.cfg.excuse.default_min
        minutes = default if minutes is None or round(minutes) < 1 else round(minutes)
        s.excuse_until_s = self._elapsed_s(s) + minutes * 60
        s.excuse_reason = reason
        s.excuses.append({"reason": reason, "minutes": minutes})
        return {"granted": True, "minutes": minutes}

    def set_hook(
        self,
        event: EventType | str,
        note: str,
        after_seconds: float | None = None,
        remaining_minutes: float | None = None,
    ) -> dict:
        s = self._need_session()
        c = self.cfg.hooks
        if event not in AGENT_HOOKS:
            raise Refused("event must be timer or session.remaining")
        event = EventType(event)
        if len(s.hooks) >= c.max_active:
            raise Refused(f"at most {c.max_active} hooks can be active; cancel one first")
        hook = Hook(s.next_hook, event, note)
        if event is EventType.TIMER:
            if after_seconds is None:
                raise Refused("a timer hook needs after_seconds")
            fires_in = after_seconds
            hook.due_s = self._elapsed_s(s) + after_seconds
        else:
            if remaining_minutes is None:
                raise Refused("a session.remaining hook needs remaining_minutes")
            if remaining_minutes < 1:
                raise Refused("remaining_minutes must be at least 1; the session's end is time's up")
            remaining = self._remaining_s(s)
            if remaining_minutes * 60 >= remaining:
                raise Refused(f"only {remaining / 60:.1f} min remain")
            fires_in = remaining - remaining_minutes * 60
            hook.at_remaining_s = remaining_minutes * 60.0
        if fires_in < c.min_refire_s:
            raise Refused(f"a hook may wake you at most once a minute; it must fire {c.min_refire_s:g} s or more out")
        s.hooks.append(hook)
        s.next_hook += 1
        self._write("hook_set", **self._hook_view(hook))
        return {"hook_id": hook.id}

    def list_hooks(self) -> dict:
        s = self._session
        return {"hooks": [self._hook_view(h) for h in s.hooks] if s else []}

    def cancel_hook(self, id: int) -> dict:
        s = self._need_session()
        hook = next((h for h in s.hooks if h.id == id), None)
        if hook is None:
            raise Refused(f"no hook {id}")
        s.hooks.remove(hook)
        self._write("hook_cancelled", id=id)
        return {"cancelled": id}

    # ---------------------------------------------------------------- time

    def tick(self) -> None:
        """Publish what fell due: hooks, Excuse expiry, time's up, Pause timeout; close a passed grace."""
        s = self._session
        if s is None:
            return
        if s.time_up_at is not None:
            if self._grace_left_s(s) <= 0:
                self._close()
            return
        if s.paused_since is not None:
            if self._now() - s.paused_since >= self.cfg.session.pause_max_min * 60:
                self._emit(Event(EventType.PAUSE_EXPIRED, {}))
                self._end(EndReason.PAUSE_TIMEOUT)
            return
        elapsed, remaining = self._elapsed_s(s), self._remaining_s(s)
        for hook in list(s.hooks):
            if (hook.due_s is not None and elapsed >= hook.due_s) or (
                hook.at_remaining_s is not None and remaining <= hook.at_remaining_s
            ):
                s.hooks.remove(hook)
                self._write("hook_fired", id=hook.id)
                self._emit(Event(hook.event, {"hook_id": hook.id, "note": hook.note}))
        if s.excuse_until_s is not None and elapsed >= s.excuse_until_s:
            s.excuse_until_s = None
            self._emit(Event(EventType.EXCUSE_EXPIRED, {"reason": s.excuse_reason}))
        if remaining <= 0:
            self._end(EndReason.COMPLETED)

    def _grace_left_s(self, s: Session) -> float:
        assert s.time_up_at is not None
        return s.time_up_at + self.cfg.session.extend_grace_min * 60 - self._now()

    def _end(self, reason: EndReason) -> None:
        s = self._session
        assert s is not None
        for hook in s.hooks:
            self._write("hook_expired", id=hook.id)
        s.hooks.clear()
        s.excuse_until_s = None
        self._bus.publish(Event(EventType.SESSION_ENDED, {"reason": reason}))  # the log writes its own record
        if reason is EndReason.COMPLETED:
            s.time_up_at = self._now()  # the extend grace: the session and its log stay open
            self._log.end_session(reason, keep_open=True)
        else:
            self._log.end_session(reason)
            self._session = None

    def _close(self) -> None:
        self._log.end_session(EndReason.COMPLETED)  # already logged at time's up; this closes the file
        self._session = None

    def _write(self, type: str, **fields) -> None:
        (self._log.session or self._log.idle).write(type, **fields)

    def _emit(self, event: Event) -> None:
        self._bus.publish(event)
        self._write(event.type, **event.data)
