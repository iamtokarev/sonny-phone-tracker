"""Framing rules (spec §5 "In a Focus Session"): per-frame detections in, the head move to ask for out.

Pure and clock-injected, like the attention rules: the vision worker calls `observe` once per frame and hands any
move to the head owner, which plays it under its one-move-at-a-time rule.

- **Re-framing.** The `person` box centre stays more than `reframe.offcentre` of the frame width off-centre for
  `reframe.hold_s`: one yaw move of offset ÷ `reframe.gain_per_deg` degrees centres it. Yaw only.
- **Search sweep.** No `person` for `sweep.after_no_person_s`, or nobody at session start: one sweep. It isn't
  asked for again until someone is seen. The first person seen after a sweep is framed at once, with no hold:
  the sweep stops as they enter the frame edge, past centre.

Frames taken during a head move (`Detections.head_moving`) count for nothing and restart the hold. The session
start counts from the first still frame, after the glide to the framing pose.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sonny.attention import Detections
from sonny.config import Config


@dataclass(frozen=True)
class Reframe:
    yaw_deg: float  # relative to the current framing yaw; positive turns the head left, moving the box right


@dataclass(frozen=True)
class Sweep:
    pass


class Framing:
    """The framing of one Focus Session; created at session start."""

    def __init__(self, cfg: Config, clock: Callable[[], float] = time.time) -> None:
        self.cfg, self._clock = cfg, clock
        self._looking_since: float | None = None  # the first still frame: session start, as far as framing goes
        self._last_person: float | None = None
        self._off_since: float | None = None  # start of the current off-centre run
        self._swept = False  # a sweep was asked for and nobody has been seen since

    def observe(self, d: Detections) -> Reframe | Sweep | None:
        """Take one frame; return the head move it calls for, if any."""
        if d.head_moving:
            self._off_since = None
            return None
        now = self._clock()
        if self._looking_since is None:
            self._looking_since = now
        person = d.person
        if person is None or person.conf < self.cfg.person.min_conf:
            self._off_since = None
            return self._sweep_due(now)
        self._last_person = now
        offset = person.centre[0] / d.frame_width - 0.5
        reframe = Reframe(-offset / self.cfg.reframe.gain_per_deg)
        if self._swept:
            self._swept = False
            return reframe if abs(reframe.yaw_deg) >= 1 else None  # under a degree is no move
        if abs(offset) <= self.cfg.reframe.offcentre:
            self._off_since = None
            return None
        if self._off_since is None:
            self._off_since = now
        if now - self._off_since < self.cfg.reframe.hold_s:
            return None
        self._off_since = None
        return reframe

    def declined(self, move: Reframe | Sweep) -> None:
        """The head owner didn't play `move` (another move was playing): ask again when it is due again."""
        if isinstance(move, Sweep):
            self._swept = False

    def _sweep_due(self, now: float) -> Sweep | None:
        if self._swept:
            return None
        if self._last_person is None:
            assert self._looking_since is not None
            due = now - self._looking_since >= self.cfg.sweep.start_look_s
        else:
            due = now - self._last_person >= self.cfg.sweep.after_no_person_s
        if due:
            self._swept = True
            return Sweep()
        return None
