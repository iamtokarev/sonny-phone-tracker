import math
from collections.abc import Callable
from dataclasses import replace

import pytest

from sonny.attention import AttentionRules, AttentionState, Box, Detections
from sonny.config import load_config
from sonny.events import Event, EventType

CFG = load_config()
FPS = CFG.window.expected_fps
W = 1280

PERSON = Box(0.9, 400, 100, 900, 720)
NOBODY = None


def held_phone(t: float) -> Box:
    """A phone in the owner's hands, inside the person box, moving a few pixels like a held phone does."""
    x = 600 + 20 * math.sin(t * 6)
    return Box(0.8, x, 400, x + 80, 560)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


Scene = Callable[[float], Detections]


def focused(t: float) -> Detections:
    return Detections(W, person=PERSON)


def on_phone(t: float) -> Detections:
    return Detections(W, phone=held_phone(t), person=PERSON)


def gone(t: float) -> Detections:
    return Detections(W)


class Run:
    """Feeds scenes frame by frame at the expected rate and keeps every event with its session time."""

    def __init__(self, cfg=CFG) -> None:
        self.clock = Clock()
        self.start = self.clock.t
        self.rules = AttentionRules(cfg, self.clock)
        self.events: list[tuple[float, Event]] = []

    @property
    def now(self) -> float:
        return self.clock.t - self.start

    def play(self, scene: Scene, seconds: float) -> "Run":
        for _ in range(round(seconds * FPS)):
            self.clock.t += 1 / FPS
            self.record(self.rules.observe(scene(self.now)))
        return self

    def record(self, events: list[Event]) -> None:
        self.events += [(round(self.now, 2), e) for e in events]

    def types(self) -> list[EventType]:
        return [e.type for _, e in self.events]

    def first(self, kind: EventType) -> float:
        return next(t for t, e in self.events if e.type == kind)


def test_sustained_phone_use_starts_phone_use():
    run = Run().play(focused, 10).play(on_phone, 10)
    assert run.types() == [EventType.PHONE_USE_STARTED]
    assert run.rules.state == AttentionState.PHONE_USE


def burst(start: float, seconds: float) -> Scene:
    return lambda t: on_phone(t) if start <= t < start + seconds else focused(t)


@pytest.mark.parametrize(
    ("label", "burst_s", "starts"),
    [
        ("raised mug", 0.5, False),
        ("eye-rubbing", 1.5, False),
        ("just under 60% of 8 s", 4.6, False),
        ("a little over 60% of 8 s", 5.2, True),
    ],
)
def test_phone_use_starts_only_on_sixty_percent_of_eight_seconds(label, burst_s, starts):
    run = Run().play(burst(10, burst_s), 30)
    assert (EventType.PHONE_USE_STARTED in run.types()) is starts, label


def test_phone_use_starts_once_evidence_fills_sixty_percent_of_the_window():
    run = Run().play(focused, 10).play(on_phone, 10)
    assert run.first(EventType.PHONE_USE_STARTED) == pytest.approx(
        10 + 0.6 * 8, abs=0.15
    )  # a picked-up phone counts once it has moved


def test_two_short_bursts_in_one_window_do_not_start_phone_use():
    run = Run().play(focused, 10).play(on_phone, 1.5).play(focused, 2).play(on_phone, 1.5).play(focused, 10)
    assert run.types() == []


def phone_scene(conf: float = 0.8, dx: float = 0, person: Box | None = PERSON, moving: bool = True) -> Scene:
    def scene(t: float) -> Detections:
        p = held_phone(t) if moving else Box(0.8, 600, 400, 680, 560)
        return Detections(W, phone=Box(conf, p.x0 + dx, p.y0, p.x1 + dx, p.y1), person=person)

    return scene


# PERSON spans x 400..900; a phone 80 px wide starting at x0 has (900 - x0) / 80 inside once x0 > 820.
@pytest.mark.parametrize(
    ("label", "scene", "starts"),
    [
        ("confidence just under 0.5", phone_scene(conf=0.49), False),
        ("confidence 0.5", phone_scene(conf=0.5), True),
        ("29% inside the person box", phone_scene(dx=900 - 0.29 * 80 - 600 + 20), False),
        ("31% inside the person box", phone_scene(dx=900 - 0.31 * 80 - 600 - 20), True),
        ("phone with nobody in frame", phone_scene(person=None), False),
        ("unsure person box", phone_scene(person=Box(0.3, 400, 100, 900, 720)), False),
        ("parked phone inside the person box", phone_scene(moving=False), False),
    ],
)
def test_phone_evidence(label, scene, starts):
    run = Run().play(focused, 10).play(scene, 30)
    assert (EventType.PHONE_USE_STARTED in run.types()) is starts, label


def test_phone_picked_up_after_lying_still_counts_once_it_moves():
    lying = phone_scene(moving=False)
    run = Run().play(lying, 20).play(on_phone, 10)
    assert run.first(EventType.PHONE_USE_STARTED) == pytest.approx(20 + 0.6 * 8, abs=0.5)


def flicker(on_s: float) -> Scene:
    """Phone evidence for `on_s` of every second, the way a phone at the ear drops in and out."""
    return lambda t: on_phone(t) if t % 1 < on_s else focused(t)


def test_phone_use_ends_once_evidence_drops_under_twenty_percent_of_five_seconds():
    run = Run().play(on_phone, 15).play(focused, 10)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.FOCUSED_RESUMED]
    assert run.first(EventType.FOCUSED_RESUMED) == pytest.approx(15 + 0.8 * 5, abs=2 / FPS)
    assert run.rules.state == AttentionState.FOCUSED


@pytest.mark.parametrize(("on_s", "ends"), [(0.25, False), (0.15, True)])
def test_phone_use_end_needs_under_twenty_percent(on_s, ends):
    run = Run().play(on_phone, 15).play(flicker(on_s), 20)
    assert (EventType.FOCUSED_RESUMED in run.types()) is ends


def test_phone_use_holds_while_nobody_is_present_and_ends_when_they_return():
    run = Run().play(on_phone, 15).play(gone, 30)
    assert run.types() == [EventType.PHONE_USE_STARTED]
    run.play(focused, 1)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.FOCUSED_RESUMED]


def test_each_distraction_episode_gets_the_next_number():
    run = Run().play(focused, 10).play(on_phone, 15).play(focused, 10).play(on_phone, 15).play(focused, 10)
    assert [e.data["episode"] for _, e in run.events] == [1, 1, 2, 2]
    first, _ = run.rules.episodes
    assert (first.number, first.kind, first.started_at) == (
        1,
        AttentionState.PHONE_USE,
        pytest.approx(run.start + 14.8, abs=0.15),
    )
    assert first.closed_at == pytest.approx(run.start + 29, abs=0.15)
    assert run.rules.episode is None


def head_moving(scene: Scene, every: int = 1) -> Scene:
    """`scene`, with the head moving in one of every `every` frames (1: all of them)."""
    return lambda t: replace(scene(t), head_moving=round(t * FPS) % every == 0)


def test_a_window_with_under_half_its_frames_changes_nothing():
    run = Run().play(on_phone, 10)
    assert run.first(EventType.PHONE_USE_STARTED) == pytest.approx(0.5 * 8, abs=2 / FPS)


@pytest.mark.parametrize(
    ("label", "scene", "starts"),
    [
        ("head moving throughout", head_moving(on_phone), False),
        (
            "two thirds of frames during head moves",
            lambda t: replace(on_phone(t), head_moving=round(t * FPS) % 3 != 0),
            False,
        ),
        ("one third of frames during head moves", head_moving(on_phone, every=3), True),
    ],
)
def test_head_move_frames_do_not_count(label, scene, starts):
    run = Run().play(focused, 10).play(scene, 20)
    assert (EventType.PHONE_USE_STARTED in run.types()) is starts, label


def test_phone_use_does_not_end_on_frames_taken_during_a_head_move():
    run = Run().play(on_phone, 15).play(head_moving(focused), 10)
    assert run.types() == [EventType.PHONE_USE_STARTED]


def stray(scene: Scene, every_s: float = 2) -> Scene:
    """`scene` with nobody in frame except one stray `person` frame every `every_s`."""
    return lambda t: focused(t) if round(t * FPS) % round(every_s * FPS) == 0 else gone(t)


def test_away_starts_after_sixty_seconds_with_nobody():
    run = Run().play(focused, 10).play(gone, 70)
    assert run.types() == [EventType.AWAY_STARTED]
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(10 + 60, abs=2 / FPS)
    assert run.rules.state == AttentionState.AWAY
    assert run.rules.episode is not None and run.rules.episode.kind == AttentionState.AWAY


def test_back_before_sixty_seconds_is_not_away():
    run = Run().play(focused, 10).play(gone, 59).play(focused, 10).play(gone, 59)
    assert run.types() == []


def test_away_counts_from_session_start_when_nobody_was_there():
    run = Run().play(gone, 61)
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(60, abs=2 / FPS)


def test_away_timer_runs_through_a_search_sweep():
    run = Run().play(focused, 10).play(gone, 10).play(head_moving(gone), 15).play(gone, 40)
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(10 + 60, abs=2 / FPS)


@pytest.mark.parametrize(
    ("label", "back", "ends"),
    [
        ("3 s of presence", [(focused, 3.1)], True),
        ("2.9 s of presence", [(focused, 2.9), (gone, 5)], False),
        ("stray person frames", [(stray(gone), 20)], False),
        ("presence broken by one empty frame", [(focused, 2), (gone, 1 / FPS), (focused, 2), (gone, 5)], False),
    ],
)
def test_away_ends_after_three_seconds_of_continuous_presence(label, back, ends):
    run = Run().play(focused, 10).play(gone, 61)
    for scene, seconds in back:
        run.play(scene, seconds)
    assert (EventType.FOCUSED_RESUMED in run.types()) is ends, label


def test_away_end_closes_the_episode():
    run = Run().play(focused, 10).play(gone, 61).play(focused, 4)
    assert run.types() == [EventType.AWAY_STARTED, EventType.FOCUSED_RESUMED]
    assert run.first(EventType.FOCUSED_RESUMED) == pytest.approx(71 + 3, abs=3 / FPS)
    assert run.rules.episode is None and run.rules.state == AttentionState.FOCUSED


def test_away_overrides_phone_use_in_the_same_episode():
    run = Run().play(on_phone, 15).play(gone, 61)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.AWAY_STARTED]
    assert [e.data["episode"] for _, e in run.events] == [1, 1]
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(15 + 60, abs=2 / FPS)


def test_returning_on_the_phone_is_phone_use_in_the_same_episode():
    run = Run().play(focused, 10).play(gone, 61).play(on_phone, 5)
    assert run.types() == [EventType.AWAY_STARTED, EventType.PHONE_USE_STARTED]
    assert [e.data["episode"] for _, e in run.events] == [1, 1]
    assert run.first(EventType.PHONE_USE_STARTED) == pytest.approx(71 + 3, abs=0.15)
    assert run.rules.episode is not None and run.rules.episode.kind == AttentionState.PHONE_USE


def test_attention_events_wake_the_agent_outside_an_excuse():
    run = Run().play(on_phone, 15).play(gone, 61)
    assert [e.data["wakes"] for _, e in run.events] == [True, True]


def test_during_an_excuse_events_are_kept_but_wake_no_one():
    run = Run()
    run.rules.start_excuse()
    run.play(on_phone, 15).play(gone, 61).play(focused, 4)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.AWAY_STARTED, EventType.FOCUSED_RESUMED]
    assert [(e.data["wakes"], e.data["excused"]) for _, e in run.events] == [(False, True)] * 3


@pytest.mark.parametrize(
    ("label", "scene", "fires"),
    [
        ("Phone Use still going", on_phone, EventType.PHONE_USE_STARTED),
        ("Away still going", gone, EventType.AWAY_STARTED),
    ],
)
def test_excuse_expiry_opens_a_fresh_episode_and_fires_at_once(label, scene, fires):
    run = Run().play(focused, 10)
    run.rules.start_excuse()
    run.play(scene, 70)
    old = run.rules.episode
    assert old is not None and old.number == 1
    run.record(run.rules.end_excuse())
    t, event = run.events[-1]
    assert (t, event.type, event.data) == (80, fires, {"episode": 2, "wakes": True, "excused": False}), label
    assert old.closed_at == run.clock.t and run.rules.episode is not None and run.rules.episode.number == 2


def test_excuse_expiry_while_focused_changes_nothing():
    run = Run().play(focused, 10)
    run.rules.start_excuse()
    run.play(on_phone, 15).play(focused, 10)
    assert run.rules.end_excuse() == []
    assert run.rules.episode is None and len(run.rules.episodes) == 1


@pytest.mark.parametrize(
    ("label", "nudges", "away", "wakes"),
    [
        ("Phone Use, no Nudge", [], False, False),
        ("Phone Use, Nudged", ["subtle"], False, True),
        ("episode included Away, no Nudge", [], True, True),
    ],
)
def test_focused_resumed_wakes_only_after_a_nudge_or_away(label, nudges, away, wakes):
    run = Run().play(on_phone, 15)
    for intensity in nudges:
        run.rules.record_nudge(intensity)
    if away:
        run.play(gone, 61)
    run.play(focused, 10)
    assert run.events[-1][1].type == EventType.FOCUSED_RESUMED
    assert run.events[-1][1].data["wakes"] is wakes, label


def test_episode_counts_nudges_with_intensity_and_away_nudges():
    run = Run().play(on_phone, 15)
    run.rules.record_nudge("subtle")
    run.play(gone, 61)
    run.rules.record_nudge("firm")
    run.rules.record_nudge("firm")
    episode = run.rules.episode
    assert episode is not None
    assert (episode.nudges, episode.away_nudges, episode.included_away) == (["subtle", "firm", "firm"], 2, True)


@pytest.mark.parametrize("flag", ["paused", "blind"])
def test_pause_and_blind_stop_evaluation(flag):
    run = Run().play(focused, 10)
    getattr(run.rules, f"set_{flag}")(True)
    run.play(on_phone, 30).play(gone, 120)
    assert run.types() == []
    assert run.rules.state == AttentionState.FOCUSED


@pytest.mark.parametrize("flag", ["paused", "blind"])
def test_windows_freeze_rather_than_reset(flag):
    run = Run().play(focused, 10).play(on_phone, 3)
    set_flag = getattr(run.rules, f"set_{flag}")
    set_flag(True)
    run.play(focused, 30)
    set_flag(False)
    run.play(on_phone, 3)
    assert run.first(EventType.PHONE_USE_STARTED) == pytest.approx(43 + 4.8 - 3, abs=0.15)


def test_away_timer_freezes_while_blind():
    run = Run().play(focused, 10).play(gone, 50)
    run.rules.set_blind(True)
    run.play(gone, 30)
    run.rules.set_blind(False)
    run.play(gone, 20)
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(90 + 10, abs=2 / FPS)


def test_an_open_episode_stays_open_while_blind():
    run = Run().play(on_phone, 15)
    run.rules.set_blind(True)
    run.play(focused, 30)
    run.rules.set_blind(False)
    assert run.rules.state == AttentionState.PHONE_USE and run.rules.episode is not None
    run.play(focused, 5)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.FOCUSED_RESUMED]
    assert [e.data["episode"] for _, e in run.events] == [1, 1]


def test_reset_clears_windows_and_closes_the_episode():
    run = Run().play(on_phone, 15)
    episode = run.rules.episode
    run.rules.reset()
    assert episode is not None and episode.closed_at == run.clock.t
    assert run.rules.episode is None and run.rules.state == AttentionState.FOCUSED
    run.play(gone, 61)
    assert run.types() == [EventType.PHONE_USE_STARTED, EventType.AWAY_STARTED]
    assert run.first(EventType.AWAY_STARTED) == pytest.approx(15 + 60, abs=2 / FPS)


def test_reset_drops_phone_evidence_seen_before_it():
    run = Run().play(focused, 10).play(on_phone, 3)
    run.rules.reset()
    run.play(on_phone, 2).play(focused, 10)  # 5 s of phone in 8 s without the reset
    assert run.types() == []
