"""Failures on the conductor's side (spec §4 "Blind", §6, §9): Blind spells, `link.restored`, waking from Mac
sleep, and the no-idle-sleep hold. `step()` runs on every conductor tick, before the session clock's.

- **Blind** is a running session with the robot link down (`sonny.link.LinkWatch`): a video freeze, a media
  drop, or a daemon that is unreachable or in error. While Blind the attention rules are frozen (the vision worker
  ignores frames), no hook fires and no Nudge event wakes the agent; the session clock runs on. Each spell goes
  to the log and the terminal (`blind.started`, `blind.ended`). A spell over `blind.restored_event_after_s` ends
  with `link.restored`, which wakes the agent; a shorter one is log-only. If the end time passes while Blind, the
  clock ends the session with `ended_while_blind`.
- **Mac sleep** shows as a jump in slept time (`asleep`) between two ticks. On wake: the link watch asks the
  daemon at once (and reconnects if the media session didn't survive); a session whose end time passed ends
  `ended_during_sleep`, silently; otherwise the attention windows start over and the open episode closes.
- **No idle sleep** while a session is open: `caffeinate -i`, tied to the app's pid so it never outlives it.
  Closing the lid still sleeps.
"""

import os
import subprocess
import time
from collections.abc import Callable
from typing import Any, Protocol

from sonny.config import Config
from sonny.events import Event, EventBus, EventType
from sonny.focus.clock import FocusClock, awake_and_asleep
from sonny.session_log import SessionLog


class Link(Protocol):
    """The link watch (`sonny.link.LinkWatch`)."""

    @property
    def down(self) -> str | None: ...
    def recheck(self) -> None: ...


class Watcher(Protocol):
    """The vision worker (`sonny.vision.VisionWorker`)."""

    def set_blind(self, blind: bool) -> None: ...
    def reset_attention(self) -> None: ...


class Awake(Protocol):
    def hold(self, on: bool) -> None: ...


def slept_s() -> float:
    """Seconds the Mac has slept since boot: time that counts through sleep less time that doesn't."""
    return awake_and_asleep() - time.clock_gettime(time.CLOCK_UPTIME_RAW)


class NoSleep:
    """A no-idle-sleep power assertion while held: `caffeinate -i -w <pid>`, which ends with the app."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, pid: int | None = None) -> None:
        self._popen, self._pid = popen, pid or os.getpid()
        self._proc: Any = None

    def hold(self, on: bool) -> None:
        if on and self._proc is None:
            args = ["caffeinate", "-i", "-w", str(self._pid)]
            self._proc = self._popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not on and self._proc is not None:
            self._proc.terminate()
            self._proc.wait(timeout=2)
            self._proc = None


class Failures:
    def __init__(
        self,
        cfg: Config,
        clock: FocusClock,
        link: Link,
        vision: Watcher,
        bus: EventBus,
        log: SessionLog,
        now: Callable[[], float] = awake_and_asleep,  # the session clock's time
        asleep: Callable[[], float] = slept_s,
        awake: Awake | None = None,
    ) -> None:
        self._c = cfg.blind
        self._clock, self._link, self._vision, self._bus, self._log = clock, link, vision, bus, log
        self._now, self._asleep = now, asleep
        self._awake = awake or NoSleep()
        self._slept = asleep()
        self._spell: tuple[int, str, float] | None = None  # the session, the reason and the start of a spell

    def step(self) -> None:
        slept, self._slept = self._asleep() - self._slept, self._asleep()
        if slept >= self._c.sleep_detect_s:
            self._woke(slept)
        self._follow_link()
        self._awake.hold(self._clock.number is not None)

    def release(self) -> None:
        """The app quits: let the Mac sleep again."""
        self._awake.hold(False)

    def _woke(self, slept: float) -> None:
        (self._log.session or self._log.idle).write("mac_woke", slept_s=round(slept, 1))
        self._link.recheck()
        if self._clock.number is None or self._clock.woke():
            return
        self._vision.reset_attention()
        self._clock.nudges.close_episode()

    def _follow_link(self) -> None:
        spell = self._spell
        if spell is not None and (self._clock.number != spell[0] or not self._clock.blind):
            self._spell = None  # the session ended during the spell; the clock logged its end
            self._vision.set_blind(False)
            spell = None
        down = self._link.down if self._clock.running else None
        if spell is None and down is not None:
            assert self._clock.number is not None
            self._spell = (self._clock.number, down, self._now())
            self._clock.set_blind(down)
            self._vision.set_blind(True)
            self._emit(EventType.BLIND_STARTED, reason=down)
        elif spell is not None and down is None:
            self._spell = None
            seconds = round(self._clock.end_blind(), 1)
            self._vision.set_blind(False)
            self._emit(EventType.BLIND_ENDED, reason=spell[1], seconds=seconds)
            if seconds > self._c.restored_event_after_s:
                self._emit(EventType.LINK_RESTORED, seconds=seconds)

    def _emit(self, type: EventType, **data: Any) -> None:
        event = Event(type, data)
        self._bus.publish(event)
        (self._log.session or self._log.idle).write_event(event)
