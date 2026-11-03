"""The session clock (spec §2 "Session clock", §3, §7 "Tools", "Limits and guards"): session time, Pause,
Excuse expiry and hook timers. The agent never keeps time; it reads the snapshot.

Every bound and default lives here, in code, and comes from config. A call that breaks a bound raises
`Refused` with the reason the agent gets back. Session time stops during a Pause; Excuses and hooks run on
session time. `tick()` publishes what fell due since the last tick.

Follow-up hooks (`phone_use.continuing`, `away.continuing`) belong to the open Distraction Episode: at least
`followup.min_delay_s` out, one per episode (a new one replaces the old), outside the hook cap, cancelled when
the episode closes, and fired only while it is still open, of their kind and not Excused. The session's
`NudgeGuard` follows the episode and the Nudges given in it.

Blind (spec §4, §9; `sonny.focus.failures` says when): session time runs on, but no hook fires; a hook that fell
due fires once Blind ends, and a follow-up's time is frozen with its episode (its due time moves by the spell,
and at least until the windows have had fresh frames to end the episode by). If the end time passes while
Blind, the session ends with `ended_while_blind` and no time's up. Session time runs through Mac sleep too
(`awake_and_asleep`), so a session whose end passed during sleep ends `ended_during_sleep`.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sonny.config import Config
from sonny.events import EndReason, Event, EventBus, EventType
from sonny.focus.nudges import FOLLOWUPS, NudgeGuard
from sonny.session_log import SessionLog

AGENT_HOOKS = (EventType.TIMER, EventType.SESSION_REMAINING, *FOLLOWUPS)


def awake_and_asleep() -> float:
    """Monotonic seconds that keep counting while the Mac sleeps (`time.monotonic` stops on macOS)."""
    return time.clock_gettime(time.CLOCK_MONOTONIC)


class Refused(Exception):
    """A tool call the code won't carry out; the message is the reason the agent gets."""


@dataclass
class Hook:
    id: int
    event: EventType
    note: str
    due_s: float | None = None  # session time it fires at (`timer`)
    at_remaining_s: float | None = None  # remaining time it fires at (`session.remaining`)
    episode: int | None = None  # the Distraction Episode a follow-up belongs to


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
    blind: str | None = None  # why the session is Blind, if it is
    blind_since: float | None = None  # clock time the spell started


class FocusClock:
    def __init__(
        self,
        cfg: Config,
        log: SessionLog,
        bus: EventBus,
        now: Callable[[], float] = awake_and_asleep,
        on_nudge: Callable[[int, str], None] = lambda number, intensity: None,
    ):
        self.cfg = cfg
        self._log, self._bus, self._now = log, bus, now
        self._session: Session | None = None
        self._started = 0
        self.nudges = NudgeGuard(
            cfg,
            now,
            quiet=lambda: self.excused or self.paused or self.blind,
            on_close=self._end_followups,
            on_nudge=on_nudge,  # the episode's Nudge, for the vision worker's copy of it
            write=self._write,
        )

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

    @property
    def blind(self) -> bool:
        s = self._session
        return s is not None and s.blind is not None

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
            "blind": s.blind is not None,
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
            **self.nudges.snapshot(),
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
        self.nudges.reset()
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
            raise Refused(f"event must be one of {', '.join(AGENT_HOOKS)}")
        event = EventType(event)
        if event in FOLLOWUPS:
            return self._set_followup(s, event, note, after_seconds)
        if sum(h.episode is None for h in s.hooks) >= c.max_active:
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

    def _set_followup(self, s: Session, event: EventType, note: str, after_seconds: float | None) -> dict:
        episode, kind = self.nudges.episode, FOLLOWUPS[event]
        if episode is None:
            raise Refused("no Distraction Episode is going on: the owner is focused")
        if episode.kind != kind:
            other = next(e for e, k in FOLLOWUPS.items() if k == episode.kind)
            raise Refused(f"the episode is {episode.kind} now; use {other}")
        if s.excuse_until_s is not None:
            raise Refused("no follow-ups during an Excuse")
        if event is EventType.AWAY_CONTINUING and self.nudges.away_capped:
            cap = self.cfg.away.max_nudges_per_episode
            raise Refused(f"at most {cap} Away Nudges per episode, and this one has had them")
        least = self.cfg.followup.min_delay_s
        if after_seconds is None or after_seconds < least:
            raise Refused(f"a follow-up needs after_seconds of at least {least:g}")
        result: dict = {}
        for old in [h for h in s.hooks if h.episode is not None]:
            s.hooks.remove(old)
            self._write("hook_replaced", id=old.id, by=s.next_hook)
            result["replaced"] = old.id
        hook = Hook(s.next_hook, event, note, due_s=self._elapsed_s(s) + after_seconds, episode=episode.number)
        s.hooks.append(hook)
        s.next_hook += 1
        self._write("hook_set", **self._hook_view(hook))
        return {"hook_id": hook.id, **result}

    def _end_followups(self, episode: int) -> None:
        """The episode closed: its follow-up goes with it."""
        s = self._session
        if s is None:
            return
        for hook in [h for h in s.hooks if h.episode == episode]:
            s.hooks.remove(hook)
            self._write("hook_cancelled", id=hook.id, reason="episode_closed")

    def _followup_void(self, s: Session, hook: Hook) -> str | None:
        """Why a due follow-up won't fire: it fires only while its episode is open, of its kind, not Excused, and
        below the Away cap."""
        e = self.nudges.episode
        if e is None or e.number != hook.episode:
            return "episode_closed"
        if e.kind != FOLLOWUPS[hook.event]:
            return "episode_changed_kind"
        if s.excuse_until_s is not None:
            return "excused"
        if hook.event is EventType.AWAY_CONTINUING and self.nudges.away_capped:
            return "away_cap"
        return None

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

    # ---------------------------------------------------------------- failures

    def set_blind(self, reason: str) -> None:
        """A Blind spell starts in the running session."""
        s = self._need_session()
        s.blind, s.blind_since = reason, self._now()

    def end_blind(self) -> float:
        """The spell is over: follow-ups move on by its length, which is returned."""
        s = self._session
        if s is None or s.blind_since is None:
            return 0.0
        spell = self._now() - s.blind_since
        s.blind = s.blind_since = None
        fresh = self._elapsed_s(s) + max(self.cfg.phone_use.end_window_s, self.cfg.away.end_presence_s)
        for hook in s.hooks:
            if hook.episode is not None and hook.due_s is not None:
                hook.due_s = max(hook.due_s + spell, fresh)  # judged on frames seen after the spell
        return spell

    def woke(self) -> bool:
        """The Mac woke from sleep. A running session whose end time passed ends, silently; say whether it did."""
        s = self._session
        if s is None or s.time_up_at is not None or s.paused_since is not None or self._remaining_s(s) > 0:
            return False
        self._end(EndReason.ENDED_DURING_SLEEP)
        return True

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
        if remaining <= 0 and s.blind is not None:
            self._end(EndReason.ENDED_WHILE_BLIND)  # no time's up: Sonny couldn't see how it went
            return
        for hook in [] if s.blind is not None else list(s.hooks):
            if (hook.due_s is not None and elapsed >= hook.due_s) or (
                hook.at_remaining_s is not None and remaining <= hook.at_remaining_s
            ):
                s.hooks.remove(hook)
                if hook.episode is not None and (void := self._followup_void(s, hook)) is not None:
                    self._write("hook_expired", id=hook.id, reason=void)
                    continue
                self._write("hook_fired", id=hook.id)
                data = {"hook_id": hook.id, "note": hook.note}
                self._emit(Event(hook.event, data if hook.episode is None else {**data, "episode": hook.episode}))
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
        if s.blind_since is not None:
            self._write(
                "blind.ended", reason=s.blind, seconds=round(self._now() - s.blind_since, 1), session_ended=True
            )
            s.blind = s.blind_since = None
        for hook in s.hooks:
            self._write("hook_expired", id=hook.id)
        s.hooks.clear()
        s.excuse_until_s = None
        self.nudges.reset()
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
