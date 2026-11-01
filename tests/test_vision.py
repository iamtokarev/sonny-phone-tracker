"""The vision worker (spec §2, §4, §5, §10) with a fake detector stream: when it watches, what it flags, what it
publishes and logs, and the framing moves it hands the head owner."""

import json

import numpy as np
import pytest

from sonny.attention import Box, Detections
from sonny.config import load_config
from sonny.events import EndReason, EventBus
from sonny.session_log import SessionLog
from sonny.vision import VisionWorker

CFG = load_config()
W = 1280
FPS = 32  # 1/32 s is exact in binary, so second boundaries fall on a frame


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Frames:
    """The media hub's video side: a new frame whenever time has moved on."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.frames = 0
        self._frame = np.zeros((720, W, 3), np.uint8)
        self.frozen = False

    def latest_frame(self):
        if not self.frozen and self._clock.t != getattr(self, "_t", None):
            self._t = self._clock.t
            self.frames += 1
        return self._frame, self._t


class Detector:
    name = "fake"

    def __init__(self) -> None:
        self.next = Detections(W)
        self.calls = 0

    def detect(self, frame) -> Detections:
        self.calls += 1
        return self.next


class Session:
    def __init__(self) -> None:
        self.number: int | None = 1
        self.running = True
        self.paused = False
        self.excused = False


class Head:
    def __init__(self) -> None:
        self.moving = False
        self.sweeping = False
        self.grant = True
        self.reframes: list[float] = []
        self.sweeps = 0
        self.found = 0
        self.stopped = 0

    def reframe(self, yaw_deg: float) -> bool:
        self.reframes.append(yaw_deg)
        return self.grant

    def sweep(self) -> bool:
        self.sweeps += 1
        return self.grant

    def sweep_found(self) -> None:
        self.found += 1

    def stop_framing(self) -> None:
        self.stopped += 1


def person(cx: float = 0.5, conf: float = 0.9) -> Box:
    return Box(conf, cx * W - 380, 0, cx * W + 380, 720)


def phone(n: int) -> Box:
    x = 600 + 15 * (n % 2)  # a phone in the hand moves; a parked one doesn't count
    return Box(0.8, x, 300, x + 80, 450)


class Rig:
    def __init__(self, tmp_path) -> None:
        self.clock = Clock()
        self.frames = Frames(self.clock)
        self.detector = Detector()
        self.session = Session()
        self.head = Head()
        self.bus = EventBus()
        self.seen = self.bus.subscribe()
        self.log = SessionLog(tmp_path)
        self.log.start_session()
        self.path = self.log.session.path
        self.worker = VisionWorker(
            CFG, self.frames, self.head, self.session, self.bus, self.log, self.detector, clock=self.clock
        )

    def run(self, seconds: float, who: Box | None = None, phone_in_hand: bool = False) -> None:
        for n in range(round(seconds * FPS)):
            self.clock.t += 1 / FPS
            self.detector.next = Detections(W, phone=phone(n) if phone_in_hand else None, person=who)
            self.worker.step()

    def events(self) -> list[str]:
        out = []
        while not self.seen.empty():
            out.append(str(self.seen.get_nowait().type))
        return out

    def records(self, type: str | None = None) -> list[dict]:
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        return [r for r in rows if type is None or r["type"] == type]


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.log.end_session(EndReason.APP_QUIT)
    rig.log.close()


# ---------------------------------------------------------------- when it watches


def test_the_worker_is_off_in_a_pause(rig):
    rig.run(1, person())
    calls = rig.detector.calls
    rig.session.paused = True
    rig.run(120)  # nobody, for longer than Away needs
    assert rig.detector.calls == calls
    assert rig.events() == [] and rig.head.sweeps == 0
    rig.session.paused = False
    rig.run(1, person())
    assert rig.detector.calls > calls


def test_a_pause_freezes_the_attention_windows_not_resets_them(rig):
    rig.run(2, person())
    rig.run(50)  # nobody for 50 s
    rig.session.paused = True
    rig.run(30)
    rig.session.paused = False
    rig.run(9)
    assert "away.started" not in rig.events()
    rig.run(2)
    assert "away.started" in rig.events()


def test_a_pause_stops_a_running_sweep(rig):
    rig.run(1)
    rig.head.sweeping = True
    rig.session.paused = True
    rig.worker.step()
    assert rig.head.stopped == 1


def test_the_worker_is_off_with_no_session_and_after_times_up(rig):
    rig.session.number = None
    rig.run(2, person())
    rig.session.number, rig.session.running = 1, False  # time's up: the extend grace
    rig.run(2, person())
    assert rig.detector.calls == 0


def test_a_frozen_stream_is_not_detected_twice(rig):
    rig.run(1, person())
    calls = rig.detector.calls
    rig.frames.frozen = True
    rig.run(1, person())
    assert rig.detector.calls == calls


# ---------------------------------------------------------------- attention


def test_phone_use_and_back_are_published_and_logged_with_the_episode(rig):
    rig.run(2, person())
    rig.run(9, person(), phone_in_hand=True)
    assert rig.events() == ["phone_use.started"]
    rig.run(6, person())
    assert rig.events() == ["focused.resumed"]
    types = [r["type"] for r in rig.records() if r["type"] != "detections"]
    assert types == [
        "episode_opened",
        "phone_use.started",
        "attention",
        "episode_closed",
        "focused.resumed",
        "attention",
    ]
    opened, closed = rig.records("episode_opened")[0], rig.records("episode_closed")[0]
    assert opened["number"] == 1 and opened["kind"] == "phone_use"
    assert closed["number"] == 1 and closed["nudges"] == [] and closed["seconds"] > 0
    assert [r["state"] for r in rig.records("attention")] == ["phone_use", "focused"]


def test_frames_taken_during_a_head_move_are_flagged_and_count_for_nothing(rig):
    rig.run(2, person())
    rig.head.moving = True
    rig.run(10, person(), phone_in_hand=True)
    assert rig.events() == []
    assert all(r["head_moving"] for r in rig.records("detections")[-8:])
    rig.head.moving = False
    rig.run(9, person(), phone_in_hand=True)
    assert rig.events() == ["phone_use.started"]


def test_leaving_for_a_minute_is_away(rig):
    rig.run(2, person())
    rig.head.grant = False  # no sweep moves the head here
    rig.run(CFG.away.start_s + 1)
    assert rig.events() == ["away.started"]


def test_an_excuse_marks_attention_events_as_waking_no_one_and_its_expiry_starts_a_fresh_episode(rig):
    rig.run(2, person())
    rig.session.excused = True
    rig.run(9, person(), phone_in_hand=True)
    started = rig.records("phone_use.started")
    assert started[-1]["wakes"] is False and started[-1]["excused"] is True
    rig.session.excused = False
    rig.run(0.1, person(), phone_in_hand=True)
    started = rig.records("phone_use.started")
    assert len(started) == 2 and started[-1]["episode"] == 2 and started[-1]["wakes"] is True


def test_a_new_session_starts_from_focused_with_new_episode_numbers(rig):
    rig.run(2, person())
    rig.run(9, person(), phone_in_hand=True)
    rig.session.number = 2
    rig.run(2, person())
    rig.run(9, person(), phone_in_hand=True)
    started = rig.records("phone_use.started")
    assert [r["episode"] for r in started] == [1, 1]


# ---------------------------------------------------------------- framing


def test_an_off_centre_owner_gets_one_reframe(rig):
    rig.run(2, person())
    rig.run(CFG.reframe.hold_s + 1, person(0.75))
    assert rig.head.reframes == [pytest.approx(-0.25 / CFG.reframe.gain_per_deg)]


def test_nobody_for_the_set_time_asks_for_one_sweep(rig):
    rig.run(2, person())
    rig.run(CFG.sweep.after_no_person_s + 5)
    assert rig.head.sweeps == 1


def test_a_sweep_the_head_declines_is_asked_for_again(rig):
    rig.run(2, person())
    rig.head.grant = False
    rig.run(CFG.sweep.after_no_person_s + 1)
    assert rig.head.sweeps > 1


def test_a_sweep_stops_on_the_first_person_seen(rig):
    rig.run(2, person())
    rig.run(CFG.sweep.after_no_person_s + 1)
    rig.head.moving = rig.head.sweeping = True
    rig.run(0.5)
    assert rig.head.found == 0
    rig.run(1 / FPS, person(0.95))
    assert rig.head.found == 1


def test_an_unsure_person_does_not_stop_a_sweep(rig):
    rig.head.moving = rig.head.sweeping = True
    rig.run(1, person(conf=CFG.person.min_conf - 0.1))
    assert rig.head.found == 0


# ---------------------------------------------------------------- the detection summary


def test_a_detection_summary_is_logged_once_a_second(rig):
    rig.run(1, person(conf=0.8))
    rig.run(1, person(conf=0.9), phone_in_hand=True)
    rig.run(1 / FPS, person())  # the first frame of the next second closes this one
    rows = rig.records("detections")
    assert len(rows) == 2
    quiet, busy = rows
    assert quiet["phone_conf"] is None and quiet["person_conf"] == pytest.approx(0.8)
    assert busy["phone_conf"] == pytest.approx(0.8) and busy["phone_inside"] == pytest.approx(1.0)
    assert busy["phone_motion"] == pytest.approx(15 / W, abs=1e-4)
    assert busy["frames"] == FPS and busy["detected"] == FPS and busy["head_moving"] is False
    assert busy["detect_ms"] >= 0
