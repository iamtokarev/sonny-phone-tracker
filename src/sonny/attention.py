"""Attention rules: per-frame detections in, Attention State events out (spec §4).

Pure and clock-injected: no camera, robot or threads. The vision worker calls `observe` once per frame.
"""

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from sonny.config import Config
from sonny.events import Event, EventType


class AttentionState(StrEnum):
    FOCUSED = "focused"
    PHONE_USE = "phone_use"
    AWAY = "away"


@dataclass(frozen=True)
class Box:
    """One detection: confidence and corners in frame pixels."""

    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def share_inside(self, other: "Box") -> float:
        """The share of this box's area that lies inside `other`."""
        w = max(0.0, min(self.x1, other.x1) - max(self.x0, other.x0))
        h = max(0.0, min(self.y1, other.y1) - max(self.y0, other.y0))
        area = (self.x1 - self.x0) * (self.y1 - self.y0)
        return w * h / area if area > 0 else 0.0


@dataclass(frozen=True)
class Detections:
    """One frame from the vision worker: its best `cell phone` and `person` boxes, if any."""

    frame_width: int
    phone: Box | None = None
    person: Box | None = None
    head_moving: bool = False


@dataclass
class Episode:
    """One Distraction Episode, with the counters code keeps for it (spec §4)."""

    number: int
    started_at: float
    kind: AttentionState  # the episode's current kind; it moves between Phone Use and Away
    included_away: bool = False
    nudges: list[str] = field(default_factory=list)  # intensity of each Nudge given
    away_nudges: int = 0
    closed_at: float | None = None


@dataclass(frozen=True)
class _Counted:
    """A frame that counts toward the windows: not taken during a head move."""

    t: float
    evidence: bool
    present: bool


class AttentionRules:
    """The Attention State of one Focus Session; created at session start.

    Windows and timers run on session-watching time: the clock minus every stretch spent paused or Blind, so both
    freeze them without resetting. Events and episodes carry the clock's own time.
    """

    def __init__(self, cfg: Config, clock: Callable[[], float] = time.time) -> None:
        # Wall time, like `Event.t`: the vision worker and the session clock share it.
        self.cfg, self._clock = cfg, clock
        away, pu = cfg.away, cfg.phone_use
        self._longest_window_s = max(away.start_s, away.end_presence_s, pu.start_window_s, pu.end_window_s)
        self.episodes: list[Episode] = []
        self.episode: Episode | None = None  # the open one
        self._excused = self._paused = self._blind = False
        self._frozen_s = 0.0  # time spent paused or Blind so far
        self._frozen_at: float | None = None
        self.reset()

    def reset(self) -> None:
        """Start over from Focused with empty windows, closing any open episode (after Mac sleep).

        Away then counts from now, as at session start."""
        self._close_episode()
        self.state = AttentionState.FOCUSED
        self._frames: deque[_Counted] = deque()
        self._phone_track: deque[tuple[float, float, float]] = deque()  # (t, centre x, centre y) of sure phones
        self._last_present = self._now()
        self._present_since: float | None = None  # start of the current run of presence frames

    def set_paused(self, paused: bool) -> None:
        """A Pause stops evaluation; windows and timers pick up where they were on resume."""
        self._paused = paused
        self._freeze()

    def set_blind(self, blind: bool) -> None:
        """While Blind, nothing changes and windows and timers are frozen, not reset."""
        self._blind = blind
        self._freeze()

    def observe(self, d: Detections) -> list[Event]:
        """Take one frame from the vision worker; return the attention events it causes."""
        if self._frozen_at is not None:
            return []
        now = self._now()
        if d.head_moving:  # left out of every window, but the Away timer runs through a sweep
            return [self._start(AttentionState.AWAY)] if self._away_due(now) else []
        present = self._present(d)
        self._count(_Counted(now, self._evidence(now, d), present))
        if present:
            self._last_present = now
            if self._present_since is None:
                self._present_since = now
        else:
            self._present_since = None

        away, pu = self.cfg.away, self.cfg.phone_use
        if self.state == AttentionState.AWAY:
            if self._present_since is None or now - self._present_since < away.end_presence_s:
                return []
            if (back := self._window(now, away.end_presence_s)) is None:
                return []
            if _evidence_share(back) >= pu.start_share:  # back on the phone: same episode
                return [self._start(AttentionState.PHONE_USE)]
            return [self._resume()]
        if self._away_due(now):
            return [self._start(AttentionState.AWAY)]
        if self.state == AttentionState.FOCUSED:
            share = self._share(now, pu.start_window_s)
            if share is not None and share >= pu.start_share:
                return [self._start(AttentionState.PHONE_USE)]
        elif present:  # Phone Use ends only with someone in frame, judged on frames with someone in them
            back = self._window(now, pu.end_window_s, present_only=True)
            if back is not None and _evidence_share(back) < pu.end_share:
                return [self._resume()]
        return []

    def _away_due(self, now: float) -> bool:
        if self.state == AttentionState.AWAY or now - self._last_present < self.cfg.away.start_s:
            return False
        return self._window(now, self.cfg.away.start_s) is not None

    def start_excuse(self) -> None:
        """From now on, attention events are kept but marked as waking no one."""
        self._excused = True

    def end_excuse(self) -> list[Event]:
        """Excuse expiry: an ongoing episode closes and a fresh one starts at once, with its `.started` event."""
        self._excused = False
        if self.episode is None:
            return []
        self._close_episode()
        return [self._start(self.state)]

    def record_nudge(self, intensity: str) -> None:
        """Count a Nudge Sonny gave in the open episode."""
        if self.episode is None:
            return
        self.episode.nudges.append(intensity)
        if self.episode.kind == AttentionState.AWAY:
            self.episode.away_nudges += 1

    def _now(self) -> float:
        """Session-watching time: the clock less every paused or Blind stretch."""
        return (self._frozen_at if self._frozen_at is not None else self._clock()) - self._frozen_s

    def _freeze(self) -> None:
        frozen = self._paused or self._blind
        if frozen and self._frozen_at is None:
            self._frozen_at = self._clock()
        elif not frozen and self._frozen_at is not None:
            self._frozen_s += self._clock() - self._frozen_at
            self._frozen_at = None

    def _count(self, frame: _Counted) -> None:
        self._frames.append(frame)
        while self._frames[0].t <= frame.t - self._longest_window_s:
            self._frames.popleft()

    def _start(self, kind: AttentionState) -> Event:
        if self.episode is None:
            self.episode = Episode(len(self.episodes) + 1, self._clock(), kind)
            self.episodes.append(self.episode)
        self.episode.kind = self.state = kind
        self.episode.included_away |= kind == AttentionState.AWAY
        started = EventType.PHONE_USE_STARTED if kind == AttentionState.PHONE_USE else EventType.AWAY_STARTED
        return self._event(started, wakes=True)

    def _resume(self) -> Event:
        assert self.episode is not None
        event = self._event(EventType.FOCUSED_RESUMED, wakes=bool(self.episode.nudges) or self.episode.included_away)
        self._close_episode()
        self.state = AttentionState.FOCUSED
        return event

    def _close_episode(self) -> None:
        if self.episode is not None:
            self.episode.closed_at, self.episode = self._clock(), None

    def _event(self, kind: EventType, wakes: bool) -> Event:
        assert self.episode is not None
        quiet = self._excused or self._frozen_at is not None  # an Excuse, Pause or Blind wakes no one
        data = {"episode": self.episode.number, "wakes": wakes and not quiet, "excused": self._excused}
        return Event(kind, data, self._clock())

    def _present(self, d: Detections) -> bool:
        return d.person is not None and d.person.conf >= self.cfg.person.min_conf

    def _evidence(self, now: float, d: Detections) -> bool:
        phone, person = d.phone, d.person
        if phone is None or phone.conf < self.cfg.phone.min_conf:
            return False
        moving = self._track(now, phone, d.frame_width)
        return (
            person is not None
            and self._present(d)
            and phone.share_inside(person) >= self.cfg.phone.min_inside_person
            and moving
        )

    def _track(self, now: float, phone: Box, frame_width: int) -> bool:
        """Track a sure phone's centre; False while it is parked (it moved too little over the parked window)."""
        track = self._phone_track
        track.append((now, *phone.centre))
        while track[0][0] < now - self.cfg.phone.parked_window_s:
            track.popleft()
        xs, ys = [x for _, x, _ in track], [y for _, _, y in track]
        spread = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        return spread >= self.cfg.phone.parked_motion * frame_width

    def _window(self, now: float, window_s: float, present_only: bool = False) -> list[_Counted] | None:
        """The counted frames of the last `window_s`, or None when there are too few to judge by."""
        recent = [f for f in self._frames if f.t > now - window_s and (f.present or not present_only)]
        enough = self.cfg.window.min_frame_share * window_s * self.cfg.window.expected_fps
        return recent if len(recent) >= enough else None

    def _share(self, now: float, window_s: float) -> float | None:
        """The share of the last `window_s` frames with phone evidence, or None when too few frames came."""
        recent = self._window(now, window_s)
        return None if recent is None else _evidence_share(recent)


def _evidence_share(frames: list[_Counted]) -> float:
    return sum(f.evidence for f in frames) / len(frames)
