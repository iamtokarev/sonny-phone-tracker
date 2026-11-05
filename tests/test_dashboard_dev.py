"""Developing the Dashboard without the robot (spec §10): a session log replayed as envelopes, a placeholder camera."""

import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sonny.attention import AttentionState
from sonny.config import load_config
from sonny.dashboard.dev import Replayer, build_app, placeholder_frame
from sonny.dashboard.feed import DashboardFeed
from tests.test_dashboard_feed import recorded_logs

CFG = load_config().dashboard
SESSION_LOG = next((p for p in recorded_logs() if p.name.startswith("session-20261103-")), None)


class FakeTime:
    """A clock that `sleep` moves on; `sleep` says whether to stop, as `threading.Event.wait` does."""

    def __init__(self, now: float = 5000.0) -> None:
        self.now = now

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> bool:
        self.now += s
        return False


def write_log(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


RECORDS = [
    {"ts": 100.0, "type": "session.started", "task": "Email", "duration_min": 10},
    {"ts": 110.0, "type": "attention", "state": "phone_use", "was": "focused"},
    {"ts": 130.0, "type": "nudge", "wakeup": "phone_use.continuing", "intensity": "subtle", "episode": 1},
    {"ts": 160.0, "type": "session.ended", "reason": "app_quit"},
]


def replay(paths: list[Path], speed: float, loops: int = 1) -> tuple[list[tuple[float, dict]], FakeTime]:
    time = FakeTime()
    emitted: list[tuple[float, dict]] = []
    Replayer(paths, speed, lambda r: emitted.append((time.now, r)), gap_s=2.0, clock=time.clock, sleep=time.sleep).run(
        loops=loops
    )
    return emitted, time


@pytest.mark.parametrize("speed", [1.0, 10.0])
def test_the_replayer_emits_the_logs_records_in_order_with_timing_scaled_by_speed(tmp_path, speed):
    emitted, _ = replay([write_log(tmp_path / "session.jsonl", RECORDS)], speed)

    assert [r["type"] for _, r in emitted] == [r["type"] for r in RECORDS]
    start = 5000.0
    offsets = [0.0, 10.0, 30.0, 60.0]  # seconds after the first record, in the log
    assert [at for at, _ in emitted] == pytest.approx([start + o / speed for o in offsets])
    # Each record is stamped with the time it went out, so the page's clock and the feed's agree.
    assert [r["ts"] for _, r in emitted] == pytest.approx([start + o / speed for o in offsets])


def test_a_sped_up_session_keeps_its_clock_in_step_with_the_replay(tmp_path):
    emitted, _ = replay([write_log(tmp_path / "session.jsonl", RECORDS)], speed=10.0)
    assert emitted[0][1]["duration_min"] == pytest.approx(1.0)  # 10 min at 10x


def test_the_replayer_loops_back_to_the_start_after_a_gap(tmp_path):
    emitted, _ = replay([write_log(tmp_path / "session.jsonl", RECORDS)], speed=1.0, loops=2)

    assert [r["type"] for _, r in emitted] == [r["type"] for r in RECORDS] * 2
    end_of_first, start_of_second = emitted[3][0], emitted[4][0]
    assert start_of_second - end_of_first == pytest.approx(2.0)


def test_several_logs_play_one_after_another_each_on_its_own_timing(tmp_path):
    a = write_log(tmp_path / "a.jsonl", RECORDS[:2])
    b = write_log(tmp_path / "b.jsonl", [{**r, "ts": r["ts"] + 86400} for r in RECORDS[2:]])  # a day later
    emitted, _ = replay([a, b], speed=1.0)

    assert [r["type"] for _, r in emitted] == [r["type"] for r in RECORDS]
    assert [at - 5000.0 for at, _ in emitted] == pytest.approx([0.0, 10.0, 12.0, 42.0])  # 2 s gap, not a day


def test_the_replayed_log_drives_the_feeds_snapshot(tmp_path):
    feed = DashboardFeed(CFG)
    time = FakeTime()
    path = write_log(tmp_path / "session.jsonl", RECORDS[:2])
    Replayer([path], 1.0, feed.on_record, clock=time.clock, sleep=time.sleep).run(loops=1)

    snap = feed.snapshot()
    assert (snap["seq"], snap["session"]["task"], snap["attention"]) == (2, "Email", "phone_use")
    assert snap["session"]["started_at"] == pytest.approx(5000.0)


@pytest.mark.skipif(SESSION_LOG is None, reason="the 2026-11-03 session log lives only in the main checkout")
def test_the_2026_11_03_log_takes_the_feed_through_focused_phone_use_and_resting():
    feed = DashboardFeed(CFG)
    time = FakeTime()
    states = []

    def emit(record: dict) -> None:
        feed.on_record(record)
        snap = feed.snapshot()
        session, episode = snap["session"], snap["episode"]
        state = "resting" if session is None else snap["attention"]
        state += "+nudge" if episode and episode["nudges"] else ""
        if state not in states:
            states.append(state)

    assert SESSION_LOG is not None
    Replayer([SESSION_LOG], 100.0, emit, clock=time.clock, sleep=time.sleep).run(loops=1)
    assert states == ["focused", "phone_use", "phone_use+nudge", "resting"]  # in order of first appearance


def test_the_placeholder_scene_boxes_what_it_draws():
    image, d = placeholder_frame(1.0, "phone_use", width=1280, height=720)
    assert image.shape == (720, 1280, 3)
    assert d.frame_width == 1280
    assert d.person is not None and d.phone is not None
    assert d.phone.share_inside(d.person) > 0.5  # the phone is held in front of the person

    _, away = placeholder_frame(1.0, "away", width=1280, height=720)
    assert away.person is None and away.phone is None

    _, focused = placeholder_frame(1.0, "focused", width=1280, height=720)
    assert focused.person is not None and focused.phone is None


def test_the_dev_app_serves_sonnys_feed_and_camera_routes():
    app, feed, camera = build_app(load_config())
    feed.on_record({"ts": 100.0, "type": "session.started", "task": "Email", "duration_min": 10})
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}})
            assert ws.receive_json()["result"]["session"]["task"] == "Email"
        with client.websocket_connect("/ws/camera") as ws:
            image, d = placeholder_frame(1.0, "phone_use")
            camera.offer(image, 1.0, d, AttentionState.PHONE_USE)
            message = ws.receive_bytes()
            (n,) = struct.unpack("<I", message[:4])
            header = json.loads(message[4 : 4 + n])
            assert {b["cls"] for b in header["boxes"]} == {"phone", "person"}
            assert header["attention"] == "phone_use"


def test_the_dev_app_binds_127_0_0_1_only(monkeypatch, capsys):
    import uvicorn

    bound: list[tuple[str, int]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, **kw: bound.append((host, port)))
    from sonny.dashboard import dev

    assert dev.main([]) == 0
    assert bound == [("127.0.0.1", CFG.port)]
