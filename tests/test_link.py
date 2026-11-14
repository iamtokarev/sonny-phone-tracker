"""The link watch (spec §4 "Blind", §9): what counts as the robot link being down, the one terminal line for a
daemon failure, and the reconnect every `blind.reconnect_every_s`, with a fake media hub, daemon and repair."""

import pytest

from sonny_focus_companion.config import load_config
from sonny_focus_companion.daemon import DaemonError
from sonny_focus_companion.link import LinkWatch

CFG = load_config()
FREEZE = CFG.blind.freeze_s
EVERY = CFG.blind.reconnect_every_s


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Feeds:
    """The media hub's arrival times: a frame and an audio chunk arrive whenever `flowing` says so."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.video = self.audio = True
        self._frame_t = self._audio_t = clock.t

    def tick(self) -> None:
        if self.video:
            self._frame_t = self._clock.t
        if self.audio:
            self._audio_t = self._clock.t

    def latest_frame(self):
        return object(), self._frame_t

    @property
    def audio_t(self) -> float:
        return self._audio_t


class Daemon:
    """The daemon's status route: running, unreachable, or in error."""

    def __init__(self) -> None:
        self.up = True
        self.error: str | None = None
        self.probes = 0

    def request(self, path, body=None):
        assert path == "/api/daemon/status"
        self.probes += 1
        if not self.up:
            raise DaemonError("Can't reach Sonny's daemon at http://reachy-mini.local:8000: timed out")
        if self.error:
            return {"state": "error", "error": self.error}
        return {"state": "running", "error": None}


class Repair:
    """Reconnects; on success the media flows again."""

    def __init__(self, feeds: Feeds) -> None:
        self.calls: list[bool] = []
        self.fail: Exception | None = None
        self._feeds = feeds

    def relink(self) -> None:
        self.calls.append(True)
        if self.fail is not None:
            raise self.fail
        self._feeds.video = self._feeds.audio = True


class Rig:
    def __init__(self) -> None:
        self.clock = Clock()
        self.feeds = Feeds(self.clock)
        self.daemon = Daemon()
        self.repair = Repair(self.feeds)
        self.said: list[str] = []
        self.records: list[tuple[str, dict]] = []
        self.watch = LinkWatch(
            CFG,
            self.feeds,
            self.daemon,
            self.repair,
            lambda type, **f: self.records.append((type, f)),
            now=self.clock,
            say=self.said.append,
        )

    def run(self, seconds: float, step: float = 0.5) -> None:
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t = min(end, self.clock.t + step)
            self.feeds.tick()
            self.watch.check()


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_a_healthy_link_is_up_and_nothing_is_repaired(rig):
    rig.run(60)
    assert rig.watch.down is None
    assert rig.repair.calls == [] and rig.said == []


def test_a_routine_freeze_is_not_blind_but_five_seconds_without_a_frame_is(rig):
    rig.run(2)
    rig.feeds.video = False
    rig.run(FREEZE - 0.5)
    assert rig.watch.down is None  # routine freezes are 2-4 s
    rig.run(0.5)
    assert rig.watch.down == "video_freeze"
    assert rig.repair.calls == []  # the media session is up (the mic still flows): wait for a fresh frame
    rig.feeds.video = True
    rig.run(0.5)
    assert rig.watch.down is None


def test_a_media_drop_reconnects_at_once_then_every_five_seconds(rig):
    rig.run(2)
    rig.repair.fail = RuntimeError("speaker link didn't come up")
    rig.feeds.video = rig.feeds.audio = False
    rig.run(FREEZE)
    assert rig.watch.down == "media_drop"
    assert rig.repair.calls == [True]
    rig.run(EVERY - 0.5)
    assert rig.repair.calls == [True]
    rig.run(0.5)
    assert rig.repair.calls == [True, True]
    rig.repair.fail = None
    rig.run(EVERY)
    assert rig.repair.calls == [True, True, True]
    rig.run(0.5)
    assert rig.watch.down is None
    assert [r for t, r in rig.records if t == "reconnect"] == [
        {"ok": False, "attempt": 1, "error": "RuntimeError: speaker link didn't come up"},
        {"ok": False, "attempt": 2, "error": "RuntimeError: speaker link didn't come up"},
        {"ok": True, "attempt": 3, "error": None},
    ]
    assert any("reconnected" in line for line in rig.said)


def test_an_unreachable_daemon_is_blind_with_one_power_cycle_line_and_no_reconnect_until_it_answers(rig):
    rig.run(2)
    rig.daemon.up = False
    rig.feeds.video = rig.feeds.audio = False  # the whole robot is gone
    rig.run(EVERY + 0.5)
    assert rig.watch.down == "daemon_unreachable"
    rig.run(60)
    assert len(rig.said) == 1 and "power-cycle" in rig.said[0]
    assert rig.repair.calls == []  # nothing to reconnect to
    probes = rig.daemon.probes
    rig.run(EVERY)
    assert rig.daemon.probes - probes == 1  # asked again every reconnect_every_s
    rig.daemon.up = True  # Sonny is back (power-cycled): reconnect, media included
    rig.run(EVERY)
    assert rig.repair.calls == [True]
    rig.run(0.5)
    assert rig.watch.down is None


def test_a_motor_communication_error_is_blind_with_one_line_and_voice_only(rig):
    rig.run(2)
    rig.daemon.error = "Motor communication error"
    rig.run(EVERY + 0.5)
    assert rig.watch.down == "daemon_error"
    rig.run(60)
    assert len(rig.said) == 1
    assert "Motor communication error" in rig.said[0] and "power-cycle" in rig.said[0]
    assert "voice-only" in rig.said[0]  # the mic and speaker still work
    assert rig.repair.calls == []  # reconnecting doesn't fix motors


def test_each_daemon_outage_gets_its_own_line(rig):
    rig.daemon.up = False
    rig.run(EVERY + 0.5)
    rig.daemon.up = True
    rig.run(EVERY + 0.5)
    rig.daemon.up = False
    rig.run(EVERY + 0.5)
    assert len(rig.said) == 2


def test_recheck_asks_the_daemon_at_the_next_check(rig):
    rig.run(1)
    probes = rig.daemon.probes
    rig.watch.recheck()  # after Mac sleep
    rig.run(0.5)
    assert rig.daemon.probes == probes + 1


# ---------------------------------------------------------------- the repair on Sonny


class Client:
    def __init__(self, alive: bool) -> None:
        self._is_alive, self.host, self.port, self.disconnected = alive, "10.0.0.7", 8000, False

    def disconnect(self) -> None:
        self.disconnected = True


class Mini:
    def __init__(self, alive: bool) -> None:
        self.client = Client(alive)
        self.calls: list[str] = []

    def _connect_single(self, host, port, timeout):
        self.calls.append(f"connect {host}:{port}")
        return Client(True)

    def enable_motors(self) -> None:
        self.calls.append("enable_motors")


class Hub:
    def __init__(self, ok: bool = True) -> None:
        self.ok, self.reconnects = ok, 0

    def reconnect(self, stop) -> bool:
        self.reconnects += 1
        return self.ok


class Motors:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def request(self, path, body=None):
        assert path == "/api/motors/status"
        return {"mode": self.mode}


class Head:
    def __init__(self) -> None:
        self.revived = 0

    def revive(self) -> None:
        self.revived += 1


def repair(mini, hub, motors="enabled"):
    import threading

    from sonny_focus_companion.link import ReachyRepair

    head = Head()
    return ReachyRepair(CFG, mini, hub, Motors(motors), head, threading.Event()), head


def test_a_wifi_drop_reconnects_the_media_session_and_a_dead_sdk_client():
    mini, hub = Mini(alive=False), Hub()
    old = mini.client
    r, head = repair(mini, hub)
    r.relink()
    assert old.disconnected and mini.calls == ["connect 10.0.0.7:8000"] and mini.client._is_alive
    assert hub.reconnects == 1 and head.revived == 0  # the motors stayed up


def test_a_live_sdk_client_is_kept():
    mini = Mini(alive=True)
    r, _ = repair(mini, Hub())
    r.relink()
    assert mini.calls == []


def test_after_a_power_cycle_the_motors_are_enabled_and_the_head_woken():
    mini = Mini(alive=False)
    r, head = repair(mini, Hub(), motors="disabled")
    r.relink()
    assert mini.calls[-1] == "enable_motors" and head.revived == 1


def test_a_speaker_link_that_does_not_come_up_fails_the_attempt():
    r, head = repair(Mini(alive=True), Hub(ok=False))
    with pytest.raises(RuntimeError, match="speaker"):
        r.relink()
    assert head.revived == 0


def test_a_new_media_session_gets_time_to_deliver_before_it_is_torn_down_again(rig):
    rig.run(2)
    rig.feeds.video = rig.feeds.audio = False
    rig.repair.relink = lambda: rig.repair.calls.append(True)  # it comes up, but nothing flows yet
    rig.run(FREEZE)
    assert rig.repair.calls == [True]
    rig.run(EVERY + FREEZE - 0.5)
    assert rig.repair.calls == [True]
    rig.run(0.5)
    assert rig.repair.calls == [True, True]
