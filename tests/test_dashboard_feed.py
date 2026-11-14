"""The Dashboard's state feed (spec §4): `DashboardFeed` turns records, bus events and health ticks into envelopes."""

import json
import subprocess
from pathlib import Path

import pytest

from sonny_focus_companion.config import load_config
from sonny_focus_companion.dashboard.feed import DashboardFeed
from sonny_focus_companion.events import Event, EventBus, EventType
from sonny_focus_companion.session_log import SessionLog

CFG = load_config().dashboard
ROOT = Path(__file__).parents[1]


def recorded_logs() -> list[Path]:
    """Recorded `logs/session-*.jsonl` (gitignored, so only on the owner's Mac): this checkout's, or the main
    checkout's when this is a git worktree."""
    dirs = [ROOT / "logs"]
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True)
    if common.returncode == 0:
        dirs.append((ROOT / common.stdout.strip()).resolve().parent / "logs")
    return sorted({p for d in dirs for p in d.glob("session-*.jsonl")})


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def feed_with_sink(**kw) -> tuple[DashboardFeed, list[dict]]:
    feed = DashboardFeed(CFG, **kw)
    sent: list[dict] = []
    feed.listen(sent.append)
    return feed, sent


def rec(ts: float, type: str, **fields) -> dict:
    return {"ts": ts, "type": type, **fields}


@pytest.mark.parametrize("path", recorded_logs() or [None], ids=lambda p: p.name if p else "no-recorded-logs")
def test_a_recorded_session_log_comes_out_as_envelopes_of_its_types_in_order(path):
    if path is None:
        pytest.skip("no recorded logs/session-*.jsonl on this machine")
    records = read(path)
    feed, sent = feed_with_sink()
    for record in records:
        feed.on_record(record)
    assert [e["type"] for e in sent] == [r["type"] for r in records]
    assert [e["seq"] for e in sent] == list(range(1, len(records) + 1))
    assert [e["t"] for e in sent] == [r["ts"] for r in records]
    assert sent[0]["data"] == {k: v for k, v in records[0].items() if k not in ("ts", "type")}


SESSION = [
    rec(100.0, "session.started", task="Email", duration_min=10),
    rec(100.1, "tool_call", name="start_session", args={"task": "Email"}, result={"started": True}),
    rec(130.0, "episode_opened", number=1, kind="phone_use"),
    rec(130.0, "phone_use.started", episode=1, wakes=True, excused=False),
    rec(130.0, "attention", state="phone_use", was="focused"),
    rec(140.0, "nudge", wakeup="phone_use.started", intensity="subtle", episode=1),
    rec(150.0, "tool_call", name="pause", args={}, result={"paused": True, "ends_session_after_min": 30}),
    rec(170.0, "tool_call", name="resume", args={}, result={"resumed": True, "remaining_min": 8.7}),
    rec(180.0, "episode_closed", number=1, kind="phone_use", seconds=50.0, included_away=False, nudges=["subtle"]),
    rec(180.0, "focused.resumed", episode=1, wakes=True, excused=False),
    rec(180.0, "attention", state="focused", was="phone_use"),
    rec(200.0, "session.ended", reason="ended_by_owner"),
]


def snapshot_after(n: int, **kw) -> dict:
    feed = DashboardFeed(CFG, **kw)
    for record in SESSION[:n]:
        feed.on_record(record)
    return feed.snapshot()


def test_before_anything_the_snapshot_is_resting_at_seq_0():
    snap = snapshot_after(0)
    assert (snap["seq"], snap["session"], snap["attention"], snap["episode"]) == (0, None, None, None)
    assert snap["agent"] == {"phase": "idle"}
    assert snap["recent"] == []


def test_a_started_session_carries_its_raw_times_so_the_page_can_tick():
    snap = snapshot_after(2)
    assert snap["seq"] == 2
    assert snap["session"] == {
        "task": "Email",
        "started_at": 100.0,
        "duration_s": 600.0,
        "paused_since": None,
        "paused_total_s": 0.0,
        "time_up_at": None,
        "blind": None,
        "episodes": 0,
        "nudges": 0,
    }
    assert (snap["attention"], snap["episode"]) == ("focused", None)
    assert (snap["ended"], snap["clear_after_s"]) == (None, 60.0)


def test_during_a_distraction_episode_the_snapshot_has_its_kind_start_and_nudges():
    snap = snapshot_after(6)
    assert snap["seq"] == 6
    assert snap["attention"] == "phone_use"
    assert snap["episode"] == {"number": 1, "kind": "phone_use", "started_at": 130.0, "nudges": ["subtle"]}


def test_a_pause_stops_the_clock_and_a_resume_adds_it_to_the_paused_total():
    assert snapshot_after(7)["session"]["paused_since"] == 150.0
    session = snapshot_after(8)["session"]
    assert (session["paused_since"], session["paused_total_s"]) == (None, 20.0)


def test_once_focused_again_and_after_the_end_the_snapshot_follows():
    snap = snapshot_after(11)
    assert (snap["seq"], snap["attention"], snap["episode"]) == (11, "focused", None)
    snap = snapshot_after(12)
    assert (snap["seq"], snap["session"], snap["attention"], snap["episode"]) == (12, None, None, None)


# ---------------------------------------------------------------- the last session, once it ended (v2 spec §4)


def ended_after(records: list[dict]) -> dict | None:
    feed = DashboardFeed(CFG)
    for record in records:
        feed.on_record(record)
    return feed.snapshot()["ended"]


def test_a_session_the_owner_ended_leaves_its_end_reason_and_counts_in_the_snapshot():
    ended = ended_after(SESSION)
    assert (ended["ended_at"], ended["reason"]) == (200.0, "ended_by_owner")
    assert ended["session"] == {
        "task": "Email",
        "started_at": 100.0,
        "duration_s": 600.0,
        "paused_since": None,
        "paused_total_s": 20.0,
        "time_up_at": None,
        "blind": None,
        "episodes": 1,
        "nudges": 1,
    }


TIME_UP = [
    rec(100.0, "session.started", task="Email", duration_min=5),
    rec(120.0, "episode_opened", number=1, kind="phone_use"),
    rec(125.0, "nudge", wakeup="phone_use.continuing", intensity="subtle", episode=1),
    rec(140.0, "nudge", wakeup="phone_use.continuing", intensity="firm", episode=1),
    rec(150.0, "episode_closed", number=1, kind="phone_use", seconds=30.0, included_away=False, nudges=[]),
    rec(200.0, "episode_opened", number=2, kind="away"),
    rec(260.0, "nudge", wakeup="away.continuing", intensity="subtle", episode=2),
    rec(270.0, "episode_closed", number=2, kind="away", seconds=70.0, included_away=True, nudges=["subtle"]),
    rec(400.0, "session.ended", reason="completed"),
]


def test_a_session_whose_time_ran_out_is_ended_while_it_waits_out_its_extend_grace():
    feed = DashboardFeed(CFG)
    for record in TIME_UP:
        feed.on_record(record)
    snap = feed.snapshot()
    assert snap["ended"] == {
        "session": {**snap["session"], "time_up_at": 400.0},
        "ended_at": 400.0,
        "reason": "completed",
    }
    assert (snap["ended"]["session"]["episodes"], snap["ended"]["session"]["nudges"]) == (2, 3)


def test_a_session_that_ended_while_blind_says_so():
    ended = ended_after(
        [
            rec(100.0, "session.started", task="Email", duration_min=5),
            rec(150.0, "blind.started", reason="robot_stream_stalled"),
            rec(400.0, "blind.ended", reason="robot_stream_stalled", seconds=250.0, session_ended=True),
            rec(400.0, "session.ended", reason="ended_while_blind"),
        ]
    )
    assert (ended["ended_at"], ended["reason"], ended["session"]["blind"]) == (400.0, "ended_while_blind", None)
    assert (ended["session"]["episodes"], ended["session"]["nudges"]) == (0, 0)


def test_extending_in_the_grace_or_starting_a_new_session_means_no_session_has_ended():
    extended = rec(410.0, "tool_call", name="extend", args={}, result={"extended_by_min": 5, "continued": True})
    assert ended_after([*TIME_UP, extended]) is None
    assert ended_after([*SESSION, rec(300.0, "session.started", task="Read", duration_min=10)]) is None


def test_the_snapshot_says_how_long_the_page_shows_an_ended_session():
    import dataclasses

    feed = DashboardFeed(dataclasses.replace(CFG, clear_after_s=5.0))
    assert feed.snapshot()["clear_after_s"] == 5.0


def test_recent_holds_the_last_envelopes_up_to_recent_max():
    import dataclasses

    feed = DashboardFeed(dataclasses.replace(CFG, recent_max=3))
    for record in SESSION[:5]:
        feed.on_record(record)
    assert [e["seq"] for e in feed.snapshot()["recent"]] == [3, 4, 5]


def test_bus_events_published_while_no_page_is_connected_still_land_in_recent():
    bus = EventBus()
    feed = DashboardFeed(CFG, bus=bus)
    bus.publish(Event(EventType.LINK_RESTORED, {"seconds": 12.0}, t=50.0))
    recent = feed.snapshot()["recent"]
    assert recent == [{"seq": 1, "t": 50.0, "type": "link.restored", "data": {"seconds": 12.0}}]


def test_a_bus_event_that_is_also_logged_comes_out_once(tmp_path):
    bus = EventBus()
    feed, sent = feed_with_sink(bus=bus)
    log = SessionLog(tmp_path, on_record=feed.on_record)
    for event in [Event(EventType.WAKE, {"score": 0.97}), Event(EventType.BLIND_STARTED, {"reason": "media_drop"})]:
        bus.publish(event)  # every source publishes, then logs the same event
        log.idle.write_event(event)
    feed.snapshot()
    assert [(e["seq"], e["type"]) for e in sent] == [(1, "wake"), (2, "blind.started")]


def test_a_bus_event_that_is_never_logged_goes_out_before_the_next_record():
    bus = EventBus()
    feed, sent = feed_with_sink(bus=bus)
    bus.publish(Event(EventType.TIMER, {"hook_id": 1, "note": "check in"}, t=10.0))
    feed.on_record(rec(11.0, "wake", score=0.9))
    feed.on_record(rec(12.0, "timer", hook_id=2, note="another"))
    assert [(e["seq"], e["type"], e["t"]) for e in sent] == [(1, "timer", 10.0), (2, "wake", 11.0), (3, "timer", 12.0)]


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_each_health_tick_sends_link_camera_detector_and_llm_readings():
    from sonny_focus_companion.dashboard.feed import Readings

    clock = Clock(1000.0)
    feed, sent = feed_with_sink(now=clock)
    for record in [
        rec(990.0, "detections", frames=30, detected=30, detect_ms=22.5),
        rec(991.0, "llm_call", model="m", ok=True, latency_s=1.4, first_text_s=1.2, attempts=1, error=None),
        rec(992.0, "reconnect", ok=False, attempt=1, error="timeout"),
        rec(993.0, "reconnect", ok=True, attempt=2, error=None),
        rec(994.0, "blind.started", reason="media_drop"),
    ]:
        feed.on_record(record)
    feed.tick(Readings(link_down="media_drop", frames=100, frame_t=999.5))
    clock.t = 1002.0
    feed.tick(Readings(link_down=None, frames=130, frame_t=1001.9))
    health = sent[-1]
    assert (health["seq"], health["t"], health["type"]) == (7, 1002.0, "health")
    assert health["data"] == {
        "down": None,
        "camera_fps": 15.0,
        "detect_ms": 22.5,
        "stream_lag_s": 0.1,
        "llm_latency_s": 1.4,
        "reconnects": 2,
        "blind_spells": 1,
    }
    snap = feed.snapshot()
    assert snap["link"] == health["data"]
    assert "health" not in [e["type"] for e in snap["recent"]]  # the snapshot carries the latest instead


def test_the_health_thread_ticks_until_stopped():
    import dataclasses
    import threading

    from sonny_focus_companion.dashboard.feed import Readings

    feed, sent = feed_with_sink()
    feed.cfg = dataclasses.replace(CFG, health_every_s=0.01)
    stop = threading.Event()
    thread = feed.run_health(stop, lambda: Readings(link_down="daemon_unreachable"))
    while len(sent) < 2:
        threading.Event().wait(0.01)
    stop.set()
    thread.join(1)
    assert not thread.is_alive()
    assert sent[0]["data"]["down"] == "daemon_unreachable"
