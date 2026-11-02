"""Nudge limits (spec §6 wake column, §7 "Limits and guards"): the Distraction Episode as the conductor sees it,
the Nudges Sonny gives in it, and what that allows.

- **A Nudge** is a reaction to Phone Use or Away (GLOSSARY): a wake-up for `phone_use.started`, `away.started` or
  their follow-ups in which Sonny speaks or plays a move. A line and a move in one wake-up are one Nudge. Replies
  to the owner, and lines for other events (an Excuse ending, a hook, time's up), are not Nudges and are never
  limited.
- **The gap.** At least `nudge.min_gap_s` between Nudges: a Nudge event due inside it is held until it passes, and
  a `play_emotion` that would start a Nudge inside it is refused. Held events let other events go first.
- **Away cap.** At most `away.max_nudges_per_episode` Away Nudges per episode; after that Away events wake no one.
- **Stale events.** An event that waited in the queue is checked again when it is delivered: a `.started` or
  follow-up whose episode has closed, or that an Excuse or Pause now covers, is dropped. `focused.resumed` wakes
  only if Sonny Nudged in the episode or the episode included Away, judged then, so a Nudge given while the
  owner was already putting the phone down still gets its warm return.

The episode is followed from the attention events on the bus, in the order the conductor sees them; the vision
worker keeps its own copy for the log. Single-threaded: everything here runs on the conductor's tick.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from sonny.config import Config
from sonny.events import Event, EventType

STARTED = {EventType.PHONE_USE_STARTED: "phone_use", EventType.AWAY_STARTED: "away"}
FOLLOWUPS = {EventType.PHONE_USE_CONTINUING: "phone_use", EventType.AWAY_CONTINUING: "away"}
NUDGE_WAKES = set(STARTED) | set(FOLLOWUPS)  # the wake-ups whose line or move is a Nudge
AWAY_WAKES = {EventType.AWAY_STARTED, EventType.AWAY_CONTINUING}
SPOKEN = "spoken"  # a Nudge's intensity when it had a line and no move


class Delivery(Enum):
    DELIVER = "deliver"
    HOLD = "hold"
    DROP = "drop"


@dataclass
class Episode:
    """One Distraction Episode with the Nudges given in it."""

    number: int
    kind: str  # "phone_use" or "away": what it is now
    started_at: float
    included_away: bool = False
    nudges: list[str] = field(default_factory=list)  # intensity of each Nudge: a move's, or "spoken"
    away_nudges: int = 0
    closed: bool = False

    def view(self, now: float) -> dict:
        return {
            "number": self.number,
            "kind": self.kind,
            "lasted_s": round(now - self.started_at),
            "nudges": list(self.nudges),
            "away_nudges": self.away_nudges,
        }


class NudgeGuard:
    def __init__(
        self,
        cfg: Config,
        now: Callable[[], float] = time.monotonic,
        *,
        quiet: Callable[[], bool] = lambda: False,  # an Excuse or Pause is on
        on_close: Callable[[int], None] = lambda number: None,
        on_nudge: Callable[[int, str], None] = lambda number, intensity: None,
        write: Callable[..., None] = lambda type, **fields: None,
    ) -> None:
        self._gap_s, self._away_cap = cfg.nudge.min_gap_s, cfg.away.max_nudges_per_episode
        self._now, self._quiet, self._on_close, self._on_nudge, self._write = now, quiet, on_close, on_nudge, write
        self.reset()

    def reset(self) -> None:
        """A new session: no episode, no Nudge yet."""
        self.episode: Episode | None = None  # the open one
        self._episodes: dict[int, Episode] = {}
        self._last_nudge = -float("inf")
        self._wakeup: EventType | None = None  # what the running wake-up is for
        self._for: Episode | None = None  # the episode open when it began: its Nudge counts there
        self._nudged = False  # the running wake-up has Nudged

    # ---------------------------------------------------------------- episodes

    def observe(self, event: Event) -> None:
        """Follow the episode from an attention event (any other event is ignored)."""
        if event.type in STARTED:
            number = event.data["episode"]
            if self.episode is not None and self.episode.number != number:
                self._close(self.episode)  # an Excuse ended mid-episode: a fresh one starts at once
            if self.episode is None:
                self.episode = self._episodes[number] = Episode(number, STARTED[event.type], self._now())
            self.episode.kind = STARTED[event.type]
            self.episode.included_away |= event.type is EventType.AWAY_STARTED
        elif event.type is EventType.FOCUSED_RESUMED:
            if self.episode is not None and self.episode.number == event.data["episode"]:
                self._close(self.episode)

    def _close(self, episode: Episode) -> None:
        episode.closed, self.episode = True, None
        self._on_close(episode.number)

    @property
    def away_capped(self) -> bool:
        return self.episode is not None and self.episode.away_nudges >= self._away_cap

    def snapshot(self) -> dict:
        now = self._now()
        return {
            "attention": self.episode.kind if self.episode is not None else "focused",
            "episode": self.episode.view(now) if self.episode is not None else None,
        }

    def closed_view(self, number: int) -> dict | None:
        """A closed episode as `focused.resumed` reports it."""
        e = self._episodes.get(number)
        return e.view(self._now()) if e is not None else None

    # ---------------------------------------------------------------- wake-ups

    def gap_left_s(self) -> float:
        return max(0.0, self._last_nudge + self._gap_s - self._now())

    def begin(self, kind: EventType | str) -> None:
        """A wake-up starts: an owner turn or a conductor event."""
        self._wakeup, self._for, self._nudged = EventType(kind), self.episode, False

    def move_refusal(self) -> str | None:
        """Why `play_emotion` may not play now, or None."""
        if self._wakeup not in NUDGE_WAKES or self._nudged:
            return None  # a reply, another event's wake-up, or the move of a Nudge already given
        if (left := self.gap_left_s()) > 0:
            return f"at least {self._gap_s:g} s between Nudges; {left:.0f} s to go"
        if self._wakeup in AWAY_WAKES and self._for is not None and self._for.away_nudges >= self._away_cap:
            return f"at most {self._away_cap} Away Nudges per episode; this one has had them"
        return None

    def moved(self, intensity: str) -> None:
        self._nudge(intensity)

    def spoke(self) -> None:
        self._nudge(SPOKEN)

    def _nudge(self, intensity: str) -> None:
        if self._wakeup not in NUDGE_WAKES or self._nudged:
            return
        self._nudged, self._last_nudge = True, self._now()
        e = self._for  # even if the owner was back before the line ended: it still earns the warm return
        if e is not None:
            e.nudges.append(intensity)
            e.away_nudges += self._wakeup in AWAY_WAKES
            self._on_nudge(e.number, intensity)
        self._write("nudge", wakeup=self._wakeup, intensity=intensity, episode=e.number if e else None)

    # ---------------------------------------------------------------- the conductor

    def delivery(self, kind: EventType, data: dict) -> Delivery:
        """What to do with a queued wake-up of `kind` (its latest event's `data`) now."""
        if kind is EventType.FOCUSED_RESUMED:
            e = self._episodes.get(data["episode"])
            if data.get("excused") or e is None or not (e.nudges or e.included_away):
                return self._drop(kind, "no Nudge and no Away in the episode")
            return Delivery.DELIVER
        if kind not in NUDGE_WAKES:
            return Delivery.DELIVER
        if self.episode is None or self.episode.number != data.get("episode"):
            return self._drop(kind, "the episode is over")
        if self.episode.kind != {**STARTED, **FOLLOWUPS}[kind]:
            return self._drop(kind, "the episode moved on")
        if self._quiet():
            return self._drop(kind, "an Excuse or Pause")
        if kind in AWAY_WAKES and self.away_capped:
            return self._drop(kind, "Away Nudge cap")
        if self.gap_left_s() > 0:
            return Delivery.HOLD
        return Delivery.DELIVER

    def _drop(self, kind: EventType, reason: str) -> Delivery:
        self._write("wakeup_dropped", event=kind, reason=reason)
        return Delivery.DROP
