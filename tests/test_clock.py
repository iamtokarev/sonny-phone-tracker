"""The session clock and its tool bounds (spec §3, §6, §7 "Tools", "Limits and guards"), on a scripted clock."""

import json

import pytest

from sonny.config import load_config
from sonny.events import EndReason, EventBus, EventType
from sonny.focus.clock import FocusClock, Refused
from sonny.session_log import SessionLog

CFG = load_config()
MIN = 60.0


class ScriptedTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Rig:
    """A clock on scripted time, with every event it publishes."""

    def __init__(self, tmp_path) -> None:
        self.time = ScriptedTime()
        self.bus = EventBus()
        self.seen = self.bus.subscribe()
        self.log = SessionLog(tmp_path)
        self.clock = FocusClock(CFG, self.log, self.bus, now=self.time)

    def advance(self, seconds: float, step: float = 1.0) -> None:
        """Move time in `step`s, ticking the clock like the conductor does."""
        end = self.time.t + seconds
        while self.time.t < end:
            self.time.t = min(end, self.time.t + step)
            self.clock.tick()

    def events(self) -> list[tuple[str, dict]]:
        out = []
        while not self.seen.empty():
            e = self.seen.get_nowait()
            out.append((str(e.type), e.data))
        return out

    def remaining_min(self) -> float:
        return self.clock.snapshot()["session"]["remaining_min"]


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.log.end_session(EndReason.APP_QUIT)
    rig.log.close()


def records(log_path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text().splitlines()]


# ---------------------------------------------------------------- Duration


def test_duration_defaults_to_25_min(rig):
    assert rig.clock.start("thesis intro")["duration_min"] == 25
    assert rig.events() == [("session.started", {"task": "thesis intro", "duration_min": 25})]


@pytest.mark.parametrize(("asked", "got"), [(1, 5), (5, 5), (40, 40), (120, 120), (300, 120)])
def test_duration_is_clamped_to_5_to_120_min(rig, asked, got):
    assert rig.clock.start("email", asked)["duration_min"] == got


def test_session_completes_on_time_and_hooks_expire_with_it(rig):
    rig.clock.start("email", 5)
    rig.clock.set_hook(EventType.TIMER, "stretch", after_seconds=10 * MIN)
    rig.events()
    rig.advance(5 * MIN)
    assert rig.events() == [("session.ended", {"reason": "completed"})]
    assert rig.clock.list_hooks() == {"hooks": []}
    assert any(r["type"] == "hook_expired" for r in records(rig.log.session.path))


def test_session_log_holds_the_session(rig):
    rig.clock.start("email", 5)
    path = rig.log.session.path
    rig.clock.end()
    assert [r["type"] for r in records(path)] == ["session.started", "session.ended"]
    assert rig.log.session is None


# ---------------------------------------------------------------- extend


def test_extend_defaults_to_10_min(rig):
    rig.clock.start("email", 20)
    rig.advance(5 * MIN)
    assert rig.clock.extend()["remaining_min"] == 25


def test_extend_needs_a_positive_number(rig):
    rig.clock.start("email", 20)
    with pytest.raises(Refused, match="at least 1"):
        rig.clock.extend(0)


def test_extend_keeps_the_duration_within_120_min(rig):
    rig.clock.start("email", 100)
    assert rig.clock.extend(60)["extended_by_min"] == 20
    with pytest.raises(Refused, match="120"):
        rig.clock.extend(5)


def test_extend_works_in_the_2_min_grace_and_continues_the_session(rig):
    rig.clock.start("email", 5)
    path = rig.log.session.path
    rig.events()
    rig.advance(5 * MIN + 90)
    assert rig.events() == [("session.ended", {"reason": "completed"})]
    result = rig.clock.extend(5)
    assert result["remaining_min"] == 5 and result["continued"] is True
    assert rig.clock.snapshot()["session"]["task"] == "email"
    rig.advance(5 * MIN)
    assert rig.events() == [("session.ended", {"reason": "completed"})]
    ends = [r["reason"] for r in records(path) if r["type"] == "session.ended"]
    assert ends == ["completed", "completed"]  # the log keeps both time's-ups, in one file


def test_extend_after_the_grace_is_refused(rig):
    rig.clock.start("email", 5)
    rig.advance(5 * MIN + 2 * MIN + 1)
    with pytest.raises(Refused, match="no Focus Session"):
        rig.clock.extend(5)
    assert rig.clock.snapshot()["session"] is None
    assert rig.log.session is None


# ---------------------------------------------------------------- Pause


def test_pause_freezes_the_clock(rig):
    rig.clock.start("email", 20)
    rig.advance(5 * MIN)
    rig.clock.pause()
    rig.advance(10 * MIN)
    assert rig.remaining_min() == 15
    rig.clock.resume()
    rig.advance(5 * MIN)
    assert rig.remaining_min() == 10


def test_pause_and_resume_refuse_the_wrong_state(rig):
    rig.clock.start("email", 20)
    with pytest.raises(Refused, match="not paused"):
        rig.clock.resume()
    rig.clock.pause()
    with pytest.raises(Refused, match="already paused"):
        rig.clock.pause()


def test_30_min_paused_ends_the_session(rig):
    rig.clock.start("email", 20)
    rig.clock.pause()
    rig.events()
    rig.advance(30 * MIN - 1)
    assert rig.events() == []
    rig.advance(1)
    assert rig.events() == [("pause.expired", {}), ("session.ended", {"reason": "pause_timeout"})]
    assert rig.clock.snapshot()["session"] is None
    with pytest.raises(Refused):
        rig.clock.extend()  # the grace is only for time's up


def test_the_pause_limit_counts_each_pause_on_its_own(rig):
    rig.clock.start("email", 60)
    rig.clock.pause()
    rig.advance(20 * MIN)
    rig.clock.resume()
    rig.clock.pause()
    rig.advance(20 * MIN)
    assert rig.clock.snapshot()["session"]["paused"] is True


# ---------------------------------------------------------------- end and Task switch


def test_end_session_ends_now(rig):
    rig.clock.start("email", 20)
    rig.events()
    rig.clock.end()
    assert rig.events() == [("session.ended", {"reason": "ended_by_owner"})]
    with pytest.raises(Refused, match="no Focus Session"):
        rig.clock.end()


def test_a_task_switch_ends_the_session_and_starts_a_new_one(rig):
    rig.clock.start("email", 20)
    first = rig.log.session.path
    rig.events()
    result = rig.clock.start("slides", 30)
    assert result["switched_from"] == "email"
    assert rig.events() == [
        ("session.ended", {"reason": "ended_by_owner"}),
        ("session.started", {"task": "slides", "duration_min": 30}),
    ]
    assert rig.log.session.path != first


def test_session_tools_need_a_session(rig):
    for call in (rig.clock.pause, rig.clock.resume, rig.clock.end, lambda: rig.clock.grant_excuse("call")):
        with pytest.raises(Refused, match="no Focus Session"):
            call()


# ---------------------------------------------------------------- Excuse


def test_excuse_defaults_to_5_min_and_the_clock_keeps_running(rig):
    rig.clock.start("email", 20)
    assert rig.clock.grant_excuse("mum is calling") == {"granted": True, "minutes": 5}
    rig.events()
    rig.advance(4.5 * MIN)
    assert rig.clock.snapshot()["excuse"] == {"reason": "mum is calling", "minutes_left": 0.5}
    rig.advance(30 - 1)
    assert rig.events() == []
    rig.advance(1)
    assert rig.events() == [("excuse.expired", {"reason": "mum is calling"})]
    assert rig.remaining_min() == 15
    assert rig.clock.snapshot()["excuses_granted"] == [{"reason": "mum is calling", "minutes": 5}]


@pytest.mark.parametrize("minutes", [0, -3])
def test_an_excuse_is_always_granted(rig, minutes):
    rig.clock.start("email", 20)
    assert rig.clock.grant_excuse("doorbell", minutes)["granted"] is True


# ---------------------------------------------------------------- hooks


def test_timer_hook_fires_once_with_its_note(rig):
    rig.clock.start("email", 20)
    hook = rig.clock.set_hook(EventType.TIMER, "check the oven", after_seconds=2 * MIN)["hook_id"]
    rig.events()
    rig.advance(2 * MIN)
    assert rig.events() == [("timer", {"hook_id": hook, "note": "check the oven"})]
    rig.advance(5 * MIN)
    assert rig.events() == []


def test_remaining_hook_fires_when_that_much_is_left(rig):
    rig.clock.start("email", 5)
    hook = rig.clock.set_hook(EventType.SESSION_REMAINING, "two left", remaining_minutes=2)["hook_id"]
    rig.events()
    rig.advance(3 * MIN - 1)
    assert rig.events() == []
    rig.advance(1)
    assert rig.events() == [("session.remaining", {"hook_id": hook, "note": "two left"})]


def test_hooks_run_on_session_time(rig):
    rig.clock.start("email", 20)
    rig.clock.set_hook(EventType.TIMER, "stretch", after_seconds=2 * MIN)
    rig.clock.pause()
    rig.events()
    rig.advance(10 * MIN)
    assert rig.events() == []


def test_at_most_5_hooks_are_active(rig):
    rig.clock.start("email", 60)
    for i in range(5):
        rig.clock.set_hook(EventType.TIMER, f"t{i}", after_seconds=(i + 2) * MIN)
    with pytest.raises(Refused, match="at most 5"):
        rig.clock.set_hook(EventType.TIMER, "one more", after_seconds=10 * MIN)
    rig.clock.cancel_hook(1)
    rig.clock.set_hook(EventType.TIMER, "one more", after_seconds=10 * MIN)


def test_a_fired_hook_frees_its_place(rig):
    rig.clock.start("email", 60)
    for i in range(5):
        rig.clock.set_hook(EventType.TIMER, f"t{i}", after_seconds=MIN + i)
    rig.advance(2 * MIN)
    rig.clock.set_hook(EventType.TIMER, "again", after_seconds=MIN)


def test_a_hook_wakes_the_agent_at_most_once_a_minute(rig):
    rig.clock.start("email", 20)
    with pytest.raises(Refused, match="once a minute"):
        rig.clock.set_hook(EventType.TIMER, "soon", after_seconds=30)
    rig.advance(14 * MIN + 30)  # 5.5 min left
    with pytest.raises(Refused, match="once a minute"):
        rig.clock.set_hook(EventType.SESSION_REMAINING, "five left", remaining_minutes=5)
    rig.clock.set_hook(EventType.TIMER, "a minute", after_seconds=MIN)


def test_remaining_hook_bounds(rig):
    rig.clock.start("email", 10)
    with pytest.raises(Refused, match="remaining_minutes"):
        rig.clock.set_hook(EventType.SESSION_REMAINING, "?")
    with pytest.raises(Refused, match="only 10"):
        rig.clock.set_hook(EventType.SESSION_REMAINING, "late", remaining_minutes=10)
    with pytest.raises(Refused, match="at least 1"):
        rig.clock.set_hook(EventType.SESSION_REMAINING, "zero", remaining_minutes=0)
    with pytest.raises(Refused, match="after_seconds"):
        rig.clock.set_hook(EventType.TIMER, "?")


def test_only_timer_and_remaining_hooks_are_offered(rig):
    rig.clock.start("email", 10)
    with pytest.raises(Refused, match=r"timer or session\.remaining"):
        rig.clock.set_hook(EventType.PHONE_USE_CONTINUING, "follow up", after_seconds=30)


def test_hooks_need_a_session_and_cancel_needs_a_hook(rig):
    with pytest.raises(Refused, match="no Focus Session"):
        rig.clock.set_hook(EventType.TIMER, "x", after_seconds=MIN)
    rig.clock.start("email", 10)
    with pytest.raises(Refused, match="no hook 7"):
        rig.clock.cancel_hook(7)


def test_hooks_are_logged(rig):
    rig.clock.start("email", 10)
    path = rig.log.session.path
    rig.clock.set_hook(EventType.TIMER, "a", after_seconds=MIN)
    rig.clock.set_hook(EventType.TIMER, "b", after_seconds=2 * MIN)
    rig.clock.cancel_hook(2)
    rig.advance(MIN)
    assert [r["type"] for r in records(path) if r["type"].startswith("hook")] == [
        "hook_set",
        "hook_set",
        "hook_cancelled",
        "hook_fired",
    ]


# ---------------------------------------------------------------- snapshot


def test_snapshot_without_a_session(rig):
    assert rig.clock.snapshot() == {"session": None}


def test_snapshot_in_a_session(rig):
    rig.clock.start("email", 20)
    rig.advance(5 * MIN + 30)
    rig.clock.set_hook(EventType.SESSION_REMAINING, "ten left", remaining_minutes=10)
    snap = rig.clock.snapshot()
    assert snap["session"] == {
        "task": "email",
        "duration_min": 20,
        "elapsed_min": 5.5,
        "remaining_min": 14.5,
        "paused": False,
        "time_up": False,
    }
    assert snap["active_hooks"] == [{"id": 1, "event": "session.remaining", "note": "ten left", "at_remaining_min": 10}]
    assert snap["excuse"] is None and snap["excuses_granted"] == []


def test_snapshot_in_the_grace_says_time_is_up(rig):
    rig.clock.start("email", 5)
    rig.advance(5 * MIN + 30)
    session = rig.clock.snapshot()["session"]
    assert session["time_up"] is True and session["remaining_min"] == 0
    assert session["can_still_extend"] is True
