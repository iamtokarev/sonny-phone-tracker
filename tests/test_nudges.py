"""Nudges end to end on the code side (spec §6 wake column, §7 "Limits and guards"): which attention events wake
the agent, the gap between Nudges, follow-up hooks, the Away cap and the episode in the snapshot.

The clock, conductor and tools are the real ones on scripted time; attention events are published by hand,
except in the Excuse-expiry test at the end, which runs the real vision worker and attention rules."""

import json
import math

import numpy as np
import pytest

from sonny.attention import Box, Detections
from sonny.config import load_config
from sonny.events import EndReason, Event, EventBus, EventType
from sonny.focus.clock import FocusClock
from sonny.focus.conductor import Conductor, wakeup_payload
from sonny.focus.tools import SessionTools
from sonny.session_log import SessionLog
from sonny.vision import VisionWorker
from tests.fakes import StubHead

CFG = load_config()
GAP = CFG.nudge.min_gap_s
PHONE, AWAY, BACK = EventType.PHONE_USE_STARTED, EventType.AWAY_STARTED, EventType.FOCUSED_RESUMED


class ScriptedTime:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Rig:
    """The conductor side of a Focus Session: clock, conductor and tools on scripted time."""

    def __init__(self, tmp_path) -> None:
        self.time = ScriptedTime()
        self.bus = EventBus()
        self._events = self.bus.subscribe()
        self.log = SessionLog(tmp_path)
        self.recorded: list[tuple[int, str]] = []
        self.vision: VisionWorker | None = None
        self.clock = FocusClock(CFG, self.log, self.bus, now=self.time, on_nudge=self._nudged)
        self.conductor = Conductor(self.clock.snapshot, self.clock.nudges)
        self.head = StubHead()
        self.tools = SessionTools(CFG, self.clock, lambda type, **f: None, head=self.head)
        self.clock.start("email", 60)
        self.pump()
        self.wake()  # session.started

    def _nudged(self, episode: int, intensity: str) -> None:
        self.recorded.append((episode, intensity))
        if self.vision is not None:  # as the pipeline wires it
            self.vision.record_nudge(episode, intensity)

    def attention(self, kind: EventType, episode: int, excused: bool = False) -> None:
        """An attention event as the vision worker publishes it."""
        self.bus.publish(Event(kind, {"episode": episode, "wakes": not excused, "excused": excused}))
        self.pump()

    def pump(self) -> None:
        while not self._events.empty():
            self.conductor.offer(self._events.get_nowait())

    def advance(self, seconds: float) -> None:
        end = self.time.t + seconds
        while self.time.t < end:
            self.time.t = min(end, self.time.t + 1)
            self.clock.tick()
            self.pump()

    def wake(self) -> dict | None:
        """Deliver the next wake-up, as the conversation begins it."""
        message = self.conductor.next_wakeup()
        if message is None:
            return None
        payload = wakeup_payload(message)
        assert payload is not None
        self.tools.begin_wakeup(payload["event"])
        return payload

    def owner(self) -> None:
        self.tools.begin_wakeup(EventType.OWNER_SPOKE)

    def nudge(self, move: str | None = "twitch", line: bool = True) -> dict | None:
        """What the agent does in the wake-up: a move and/or a line. Returns the move's result."""
        result = self.tools.call("play_emotion", {"name": move}) if move else None
        if line:
            self.tools.spoke()
        return result

    def episode(self) -> dict | None:
        return self.clock.snapshot()["episode"]

    def records(self) -> list[dict]:
        assert self.log.session is not None
        return [json.loads(line) for line in self.log.session.path.read_text().splitlines()]


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.log.end_session(EndReason.APP_QUIT)
    rig.log.close()


# ---------------------------------------------------------------- wake rules


@pytest.mark.parametrize("kind", [PHONE, AWAY])
def test_phone_use_and_away_wake_the_agent(rig, kind):
    rig.attention(kind, 1)
    payload = rig.wake()
    assert payload is not None and payload["event"] == str(kind) and payload["episode"] == 1
    assert "wakes" not in payload and "excused" not in payload


@pytest.mark.parametrize("kind", [PHONE, AWAY])
def test_during_an_excuse_attention_events_wake_no_one(rig, kind):
    rig.attention(kind, 1, excused=True)
    assert rig.wake() is None and rig.conductor.pending == 0


def test_an_event_queued_before_an_excuse_or_pause_is_dropped(rig):
    rig.attention(PHONE, 1)
    rig.clock.grant_excuse("call")
    assert rig.wake() is None
    rig.attention(AWAY, 1)
    rig.clock.pause()
    assert rig.wake() is None


def test_a_started_event_whose_episode_closed_while_it_waited_is_dropped(rig):
    rig.attention(PHONE, 1)
    rig.attention(BACK, 1)
    assert rig.wake() is None  # no Nudge was given, so the return wakes no one either


def test_focused_resumed_wakes_after_a_nudge_in_the_episode(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge()
    rig.attention(BACK, 1)
    payload = rig.wake()
    assert payload is not None and payload["event"] == "focused.resumed"
    assert payload["episode"]["number"] == 1 and payload["episode"]["nudges"] == ["subtle"]


def test_focused_resumed_wakes_after_away_even_without_a_nudge(rig):
    rig.attention(AWAY, 1)
    rig.wake()  # the agent stays silent
    rig.attention(BACK, 1)
    assert (rig.wake() or {}).get("event") == "focused.resumed"


def test_focused_resumed_after_a_silent_phone_episode_wakes_no_one(rig):
    rig.attention(PHONE, 1)
    rig.wake()  # silence is no Nudge
    rig.attention(BACK, 1)
    assert rig.wake() is None


def test_focused_resumed_during_an_excuse_wakes_no_one(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge()
    rig.attention(BACK, 1, excused=True)
    assert rig.wake() is None


def test_a_nudge_given_while_the_return_was_queued_still_gets_its_warm_return(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.attention(BACK, 1)  # the owner puts the phone down while Sonny is still Nudging
    rig.nudge(move=None)
    assert (rig.wake() or {}).get("event") == "focused.resumed"


# ---------------------------------------------------------------- the gap


def test_a_line_and_a_move_in_one_wake_up_are_one_nudge(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge("wiggle")
    rig.tools.spoke()
    assert rig.episode()["nudges"] == ["medium"] and rig.recorded == [(1, "medium")]


def test_a_line_alone_is_a_spoken_nudge(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge(move=None)
    assert rig.episode()["nudges"] == ["spoken"]


def test_a_nudge_event_inside_the_gap_is_held_until_it_passes(rig):
    rig.attention(AWAY, 1)
    rig.wake()
    rig.nudge(move=None)
    rig.advance(5)
    rig.attention(PHONE, 1)  # back from Away still on the phone: same episode
    assert rig.wake() is None and rig.conductor.pending == 1
    rig.advance(GAP - 6)
    assert rig.wake() is None
    rig.advance(1)
    assert (rig.wake() or {}).get("event") == "phone_use.started"


def test_other_events_go_ahead_of_a_held_one(rig):
    rig.tools.call("set_hook", {"event": "timer", "note": "stretch", "after_seconds": 60})
    rig.advance(55)
    rig.attention(AWAY, 1)
    rig.wake()
    rig.nudge(move=None)
    rig.attention(PHONE, 1)
    rig.advance(5)  # the timer fires inside the gap
    assert (rig.wake() or {}).get("event") == "timer"
    assert rig.wake() is None


def test_a_move_that_would_nudge_inside_the_gap_is_refused(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge("twitch")
    rig.tools.begin_wakeup(EventType.PHONE_USE_CONTINUING)  # as if delivered inside the gap
    result = rig.nudge("reprimand1", line=False)
    assert result is not None and "between Nudges" in result["refused"]
    assert rig.head.calls == ["twitch"]


def test_the_second_move_of_the_same_nudge_is_not_refused(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge("twitch")
    assert "playing" in (rig.nudge("wiggle", line=False) or {})
    assert rig.episode()["nudges"] == ["subtle"]


def test_replies_to_the_owner_are_never_limited(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge("twitch")
    rig.owner()
    assert "playing" in (rig.nudge("yes1") or {})
    assert rig.episode()["nudges"] == ["subtle"]  # a reply is not a Nudge
    rig.advance(1)
    assert rig.clock.nudges.gap_left_s() == pytest.approx(GAP - 1)


def test_lines_for_other_events_are_not_nudges(rig):
    rig.attention(PHONE, 1)
    rig.tools.begin_wakeup(EventType.TIMER)
    rig.nudge("perk")
    assert rig.clock.nudges.gap_left_s() == 0 and rig.episode()["nudges"] == []


def test_a_silent_wake_up_is_no_nudge(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.attention(AWAY, 1)
    assert (rig.wake() or {}).get("event") == "away.started"  # no gap to wait out


# ---------------------------------------------------------------- follow-ups


def followup(rig: Rig, event: str = "phone_use.continuing", after: float = 30) -> dict:
    return rig.tools.call("set_hook", {"event": event, "note": "still on it?", "after_seconds": after})


def test_a_follow_up_fires_with_its_note_while_the_episode_goes_on(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge()
    hook = followup(rig, after=30)["hook_id"]
    rig.advance(29)
    assert rig.wake() is None
    rig.advance(1)
    payload = rig.wake()
    assert payload is not None and payload["event"] == "phone_use.continuing"
    assert payload["hooks"] == [{"id": hook, "note": "still on it?"}]
    assert payload["state"]["episode"]["number"] == 1


def test_a_follow_up_fires_no_sooner_than_the_minimum_delay(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    assert "at least 20" in followup(rig, after=CFG.followup.min_delay_s - 1)["refused"]
    assert "refused" in rig.tools.call("set_hook", {"event": "phone_use.continuing", "note": "n"})
    assert "hook_id" in followup(rig, after=CFG.followup.min_delay_s)


def test_a_follow_up_needs_an_episode_of_its_kind(rig):
    assert "no Distraction Episode" in followup(rig)["refused"]
    rig.attention(PHONE, 1)
    assert "use phone_use.continuing" in followup(rig, "away.continuing")["refused"]


def test_one_follow_up_per_episode_a_new_one_replaces_the_old(rig):
    rig.attention(PHONE, 1)
    old = followup(rig)["hook_id"]
    new = followup(rig, after=45)
    assert new["replaced"] == old
    hooks = rig.clock.list_hooks()["hooks"]
    assert [(h["id"], h["event"]) for h in hooks] == [(new["hook_id"], "phone_use.continuing")]


def test_follow_ups_are_outside_the_hook_cap(rig):
    for i in range(CFG.hooks.max_active):
        rig.tools.call("set_hook", {"event": "timer", "note": str(i), "after_seconds": 600 + i})
    rig.attention(PHONE, 1)
    assert "hook_id" in followup(rig)
    assert "at most" in rig.tools.call("set_hook", {"event": "timer", "note": "x", "after_seconds": 900})["refused"]


def test_a_follow_up_is_cancelled_when_the_episode_closes(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge()
    followup(rig)
    rig.attention(BACK, 1)
    assert rig.clock.list_hooks()["hooks"] == []
    assert any(r["type"] == "hook_cancelled" and r["reason"] == "episode_closed" for r in rig.records())
    rig.wake()  # focused.resumed
    rig.advance(60)
    assert rig.wake() is None


def test_a_follow_up_is_refused_and_does_not_fire_during_an_excuse(rig):
    rig.attention(PHONE, 1)
    followup(rig, after=30)
    rig.clock.grant_excuse("call", 2)
    assert "Excuse" in followup(rig)["refused"]
    rig.advance(30)
    assert rig.wake() is None
    assert any(r["type"] == "hook_expired" for r in rig.records())


def test_a_follow_up_whose_episode_turned_into_away_does_not_fire(rig):
    rig.attention(PHONE, 1)
    followup(rig, after=30)
    rig.attention(AWAY, 1)
    assert rig.wake()["event"] == "away.started"
    rig.advance(30)
    assert rig.wake() is None


# ---------------------------------------------------------------- the Away cap


def away_nudge(rig: Rig) -> None:
    payload = rig.wake()
    assert payload is not None and payload["event"] in ("away.started", "away.continuing"), payload
    rig.nudge(move=None)
    followup(rig, "away.continuing", after=30)


def test_at_most_two_away_nudges_per_episode(rig):
    rig.attention(AWAY, 1)
    away_nudge(rig)
    rig.advance(30)
    away_nudge(rig)  # the second: its follow-up was set before its line, so it stands until it fires
    assert rig.episode()["away_nudges"] == CFG.away.max_nudges_per_episode
    assert "Away Nudges" in followup(rig, "away.continuing")["refused"]
    rig.advance(60)
    assert rig.wake() is None


def test_after_the_away_cap_a_second_away_spell_wakes_no_one(rig):
    rig.attention(AWAY, 1)
    away_nudge(rig)
    rig.advance(30)
    away_nudge(rig)
    rig.advance(GAP)
    rig.attention(PHONE, 1)  # back on the phone, same episode
    assert rig.wake()["event"] == "phone_use.started"
    rig.advance(GAP)
    rig.attention(AWAY, 1)
    assert rig.wake() is None


def test_the_away_cap_is_per_episode(rig):
    rig.attention(AWAY, 1)
    away_nudge(rig)
    rig.advance(30)
    away_nudge(rig)
    rig.attention(BACK, 1)
    assert rig.wake()["event"] == "focused.resumed"
    rig.advance(GAP)
    rig.attention(AWAY, 2)
    assert rig.wake()["event"] == "away.started"


# ---------------------------------------------------------------- the snapshot


def test_the_snapshot_carries_the_episode(rig):
    assert rig.clock.snapshot()["attention"] == "focused" and rig.episode() is None
    rig.attention(PHONE, 1)
    rig.advance(10)
    payload = rig.wake()
    assert payload["state"]["attention"] == "phone_use"
    assert payload["state"]["episode"] == {
        "number": 1,
        "kind": "phone_use",
        "lasted_s": 10,
        "nudges": [],
        "away_nudges": 0,
    }
    rig.nudge("wiggle")
    rig.attention(AWAY, 1)
    assert rig.episode()["kind"] == "away" and rig.episode()["nudges"] == ["medium"]


def test_a_new_session_starts_with_no_episode_and_no_gap(rig):
    rig.attention(PHONE, 1)
    rig.wake()
    rig.nudge()
    rig.clock.start("slides", 30)
    assert rig.episode() is None and rig.clock.nudges.gap_left_s() == 0


# ---------------------------------------------------------------- Excuse expiry, with the real vision worker

W = 1280
PERSON = Box(0.9, 400, 100, 900, 720)
FPS = CFG.window.expected_fps


class Frames:
    def __init__(self, time: ScriptedTime) -> None:
        self._time, self.frames = time, 0
        self._frame = np.zeros((720, W, 3), np.uint8)

    def latest_frame(self):
        self.frames += 1
        return self._frame, self._time.t


class Detector:
    name = "fake"

    def detect(self, frame) -> Detections:
        x = 600 + 20 * math.sin(self.t * 6)
        return Detections(W, phone=Box(0.8, x, 400, x + 80, 560), person=PERSON)  # the owner on the phone

    t = 0.0


class StillHead:
    moving = sweeping = False

    def reframe(self, yaw_deg: float) -> bool:
        return False

    def sweep(self) -> bool:
        return False

    def sweep_found(self) -> None: ...
    def stop_framing(self) -> None: ...


def test_excuse_expiry_mid_phone_use_opens_a_fresh_episode_and_wakes_the_agent_at_once(rig):
    detector = Detector()
    vision = VisionWorker(CFG, Frames(rig.time), StillHead(), rig.clock, rig.bus, rig.log, detector, rig.time)
    rig.vision = vision

    def watch(seconds: float) -> list[dict]:
        woken = []
        for _ in range(round(seconds * FPS)):
            rig.time.t += 1 / FPS
            detector.t += 1 / FPS
            vision.step()
            rig.clock.tick()
            rig.pump()
            if (payload := rig.wake()) is not None:
                woken.append(payload)
                if payload["event"] == "phone_use.started":
                    rig.nudge("twitch")
        return woken

    woken = watch(10)
    assert [w["event"] for w in woken] == ["phone_use.started"] and woken[0]["episode"] == 1
    rig.owner()
    rig.tools.call("grant_excuse", {"reason": "mum calling", "minutes": 1})
    assert watch(59) == []  # still on the phone, Excused
    woken = watch(2)
    assert [w["event"] for w in woken] == ["excuse.expired", "phone_use.started"]
    fresh = woken[1]
    assert fresh["episode"] == 2 and fresh["state"]["episode"]["nudges"] == []
    closed = [r for r in rig.records() if r["type"] == "episode_closed"]
    assert closed[0]["number"] == 1 and closed[0]["nudges"] == ["subtle"]  # the vision worker's copy
