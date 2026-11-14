"""Failures on the conductor's side (spec §4 "Blind", §6, §9): Blind spells in a Focus Session, `link.restored`,
the end time passing while Blind or asleep, waking from Mac sleep, and the no-idle-sleep hold. A real session
clock and conductor on scripted time, with a fake link watch, vision worker and power assertion."""

import json

import pytest

from sonny_focus_companion.config import load_config
from sonny_focus_companion.events import EndReason, Event, EventBus, EventType
from sonny_focus_companion.focus.clock import FocusClock
from sonny_focus_companion.focus.conductor import Conductor, wakeup_payload
from sonny_focus_companion.focus.failures import Failures, NoSleep
from sonny_focus_companion.session_log import SessionLog

CFG = load_config()
MIN = 60.0
RESTORED_AFTER = CFG.blind.restored_event_after_s


class ScriptedTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Link:
    def __init__(self) -> None:
        self.down: str | None = None
        self.rechecks = 0

    def recheck(self) -> None:
        self.rechecks += 1


class Vision:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_blind(self, blind: bool) -> None:
        self.calls.append(("blind", blind))

    def reset_attention(self) -> None:
        self.calls.append(("reset",))


class Awake:
    def __init__(self) -> None:
        self.held = False

    def hold(self, on: bool) -> None:
        self.held = on


class Rig:
    def __init__(self, tmp_path) -> None:
        self.time = ScriptedTime()
        self.asleep = 0.0  # seconds the Mac has slept so far
        self.bus = EventBus()
        self.seen = self.bus.subscribe()
        self.log = SessionLog(tmp_path)
        self.clock = FocusClock(CFG, self.log, self.bus, now=self.time)
        self.conductor = Conductor(self.clock.snapshot, self.clock.nudges)
        self.wakes = self.bus.subscribe()
        self.link, self.vision, self.awake = Link(), Vision(), Awake()
        self.failures = Failures(
            CFG,
            self.clock,
            self.link,
            self.vision,
            self.bus,
            self.log,
            now=self.time,
            asleep=lambda: self.asleep,
            awake=self.awake,
        )

    def advance(self, seconds: float, step: float = 0.5) -> None:
        """Move time like the conductor's tick: the failures first, then the clock."""
        end = self.time.t + seconds
        while self.time.t < end:
            self.time.t = min(end, self.time.t + step)
            self.failures.step()
            self.clock.tick()
            while not self.wakes.empty():
                self.conductor.offer(self.wakes.get_nowait())

    def sleep(self, seconds: float) -> None:
        """The Mac sleeps: nothing runs, then time and the sleep counter have both moved on."""
        self.time.t += seconds
        self.asleep += seconds

    def events(self) -> list[tuple[str, dict]]:
        out = []
        while not self.seen.empty():
            e = self.seen.get_nowait()
            out.append((str(e.type), e.data))
        return out

    def wakeups(self) -> list[dict]:
        out = []
        while (m := self.conductor.next_wakeup()) is not None:
            out.append(wakeup_payload(m))
        return out

    def records(self, path) -> list[dict]:
        return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.log.end_session(EndReason.APP_QUIT)
    rig.log.close()


def started(rig: Rig, minutes: int = 25) -> None:
    rig.clock.start("email", minutes)
    rig.advance(1)
    rig.wakeups()  # session.started
    rig.events()


def test_no_session_no_blind(rig):
    rig.link.down = "video_freeze"
    rig.advance(60)
    assert not rig.clock.blind
    assert rig.events() == []


def test_a_link_down_in_a_session_is_blind_until_it_is_back(rig):
    started(rig)
    rig.link.down = "media_drop"
    rig.advance(1)
    assert rig.clock.blind and rig.vision.calls == [("blind", True)]
    assert rig.events() == [("blind.started", {"reason": "media_drop"})]
    assert rig.clock.snapshot()["session"]["blind"] is True
    rig.advance(9)
    rig.link.down = None
    rig.advance(0.5)
    assert not rig.clock.blind and rig.vision.calls[-1] == ("blind", False)
    assert rig.events() == [("blind.ended", {"reason": "media_drop", "seconds": 10.0})]
    assert rig.wakeups() == []  # a short spell only goes to the log


def test_a_spell_over_thirty_seconds_wakes_the_agent_with_link_restored(rig):
    started(rig)
    rig.link.down = "daemon_unreachable"
    rig.advance(RESTORED_AFTER + 1)
    rig.link.down = None
    rig.advance(0.5)
    events = rig.events()
    assert events[-1][0] == "link.restored" and events[-1][1]["seconds"] > RESTORED_AFTER
    [wakeup] = rig.wakeups()
    assert wakeup["event"] == "link.restored" and wakeup["state"]["session"]["blind"] is False


def test_a_spell_of_exactly_thirty_seconds_is_log_only(rig):
    started(rig)
    rig.link.down = "video_freeze"
    rig.advance(0.5)
    rig.advance(RESTORED_AFTER - 0.5)
    rig.link.down = None
    rig.advance(0.5)
    assert [t for t, _ in rig.events()] == ["blind.started", "blind.ended"]
    assert rig.wakeups() == []


def test_no_hook_fires_while_blind_and_a_due_timer_fires_after(rig):
    started(rig)
    rig.clock.set_hook("timer", "stretch", after_seconds=60)
    rig.link.down = "video_freeze"
    rig.advance(90)
    assert "timer" not in [t for t, _ in rig.events()]
    rig.link.down = None
    rig.advance(0.5)
    assert "timer" in [t for t, _ in rig.events()]


def test_a_follow_up_waits_out_the_spell_like_the_episode_it_follows(rig):
    started(rig)
    rig.bus.publish(Event(EventType.PHONE_USE_STARTED, {"episode": 1, "wakes": True, "excused": False}))
    rig.advance(0.5)
    rig.wakeups()
    rig.clock.set_hook("phone_use.continuing", "again?", after_seconds=40)
    rig.advance(10)
    rig.link.down = "video_freeze"
    rig.advance(20)
    rig.link.down = None
    rig.advance(0.5)
    rig.events()
    rig.advance(29)  # 40 s of episode time since it was set, the spell left out
    assert "phone_use.continuing" not in [t for t, _ in rig.events()]
    rig.advance(1.5)
    assert "phone_use.continuing" in [t for t, _ in rig.events()]


def test_a_nudge_event_queued_before_the_spell_is_dropped_by_it(rig):
    started(rig)
    rig.link.down = "video_freeze"
    rig.advance(0.5)
    rig.bus.publish(Event(EventType.PHONE_USE_STARTED, {"episode": 1, "wakes": True, "excused": False}))
    rig.advance(0.5)
    assert rig.wakeups() == []


def test_the_end_time_passing_while_blind_ends_the_session_silently(rig, tmp_path):
    started(rig, minutes=5)
    path = rig.log.session.path
    rig.link.down = "media_drop"
    rig.advance(5 * MIN)
    events = rig.events()
    assert ("session.ended", {"reason": EndReason.ENDED_WHILE_BLIND}) in events
    assert rig.clock.number is None  # no extend grace: the session is closed
    assert rig.wakeups() == []  # no "time's up"
    types = [r["type"] for r in rig.records(path)]
    assert types.index("blind.ended") < types.index("session.ended")
    rig.link.down = None
    rig.advance(1)
    assert rig.events() == []  # the spell ended with the session


def test_waking_with_the_end_time_passed_ends_the_session_silently(rig):
    started(rig, minutes=5)
    rig.advance(2 * MIN)
    rig.sleep(10 * MIN)
    rig.advance(0.5)
    assert ("session.ended", {"reason": EndReason.ENDED_DURING_SLEEP}) in rig.events()
    assert rig.clock.number is None
    assert rig.wakeups() == []
    assert rig.link.rechecks == 1  # reconnect on wake


def test_waking_mid_session_resets_the_windows_and_closes_the_episode(rig, tmp_path):
    started(rig)
    rig.bus.publish(Event(EventType.PHONE_USE_STARTED, {"episode": 1, "wakes": True, "excused": False}))
    rig.advance(0.5)
    rig.wakeups()
    rig.clock.set_hook("phone_use.continuing", "again?", after_seconds=40)
    path = rig.log.session.path
    rig.sleep(MIN)
    rig.advance(0.5)
    assert ("reset",) in rig.vision.calls and rig.link.rechecks == 1
    assert rig.clock.number == 1 and rig.clock.nudges.episode is None
    assert rig.clock.list_hooks() == {"hooks": []}  # the follow-up went with the episode
    assert rig.wakeups() == []
    records = rig.records(path)
    assert {"type": "mac_woke", "slept_s": 60.0} in [{k: r[k] for k in r if k != "ts"} for r in records]
    assert rig.clock.snapshot()["session"]["remaining_min"] == pytest.approx(25 - 1 - 1 / 60, abs=0.1)


def test_a_short_scheduling_hiccup_is_not_sleep(rig):
    started(rig)
    rig.asleep += 0.2
    rig.advance(0.5)
    assert ("reset",) not in rig.vision.calls and rig.link.rechecks == 0


def test_sleep_outside_a_session_only_rechecks_the_link(rig):
    rig.sleep(MIN)
    rig.advance(0.5)
    assert rig.link.rechecks == 1 and rig.vision.calls == []


def test_the_mac_is_kept_awake_only_while_a_session_is_open(rig):
    rig.advance(1)
    assert not rig.awake.held
    rig.clock.start("email", 5)
    rig.advance(1)
    assert rig.awake.held
    rig.clock.end()
    rig.advance(1)
    assert not rig.awake.held


class Proc:
    def __init__(self, args) -> None:
        self.args, self.terminated = args, False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        return 0


def test_no_sleep_runs_caffeinate_tied_to_the_app_while_held():
    procs: list[Proc] = []
    awake = NoSleep(popen=lambda args, **kw: procs.append(Proc(args)) or procs[-1], pid=4242)
    awake.hold(True)
    awake.hold(True)
    assert [p.args for p in procs] == [["caffeinate", "-i", "-w", "4242"]]
    awake.hold(False)
    awake.hold(False)
    assert procs[0].terminated and len(procs) == 1


def test_a_follow_up_due_right_after_a_spell_waits_for_fresh_frames(rig):
    started(rig)
    rig.bus.publish(Event(EventType.PHONE_USE_STARTED, {"episode": 1, "wakes": True, "excused": False}))
    rig.advance(0.5)
    rig.wakeups()
    rig.clock.set_hook("phone_use.continuing", "again?", after_seconds=20)
    rig.advance(19.5)
    rig.link.down = "media_drop"
    rig.advance(40)
    rig.link.down = None
    rig.advance(0.5)
    rig.events()
    fresh = max(CFG.phone_use.end_window_s, CFG.away.end_presence_s)
    rig.advance(fresh - 1)  # without the wait it would fire 0.5 s after the spell, on frozen windows
    assert "phone_use.continuing" not in [t for t, _ in rig.events()]
    rig.advance(1.5)
    assert "phone_use.continuing" in [t for t, _ in rig.events()]
