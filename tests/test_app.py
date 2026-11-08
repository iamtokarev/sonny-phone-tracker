import http.client
import json
import socket
import threading
import time
from typing import ClassVar

import pytest

import sonny.main as main_mod
from sonny.config import load_config
from sonny.daemon import StartupError
from sonny.main import Sonny
from sonny.robot import ReachyRobot
from sonny.session_log import SessionLog
from tests.fakes import FakeMedia, FakeMini

CFG = load_config()


class FakeVoice:
    """Stands in for the voice pipeline: ready at once, then runs until the app stops."""

    def __init__(self, *_args) -> None:
        pass

    async def run(self, stop, on_ready) -> None:
        on_ready()
        stop.wait()


class QuietDaemon:
    """The daemon's REST routes at startup: Sonny's volume is already right."""

    def request(self, path, body=None):
        return {"volume": CFG.robot.volume}


def robot(cfg, mini):
    return ReachyRobot(mini, QuietDaemon(), cfg.head.emotions_dataset)


class FakeLink:
    """The link watch: always up; remembers its lifecycle."""

    down = None
    calls: ClassVar[list[str]] = []

    def __init__(self, *_args) -> None:
        FakeLink.calls = []

    def recheck(self) -> None:
        pass

    def start(self, stop) -> None:
        FakeLink.calls.append("start")

    def join(self, timeout) -> None:
        FakeLink.calls.append("join" if not readers_stopped() else "join_late")


def readers_stopped() -> bool:
    return not readers_alive()


@pytest.fixture
def app(tmp_path):
    return Sonny(CFG, log=SessionLog(tmp_path), voice=FakeVoice, robot=robot, link=FakeLink)


def speaker_up_after(fake: FakeMedia, seconds: float) -> None:
    threading.Timer(seconds, lambda: setattr(fake.audio, "_appsrc", object())).start()


def readers_alive() -> bool:
    return any(t.name in {"sonny-video", "sonny-audio"} and t.is_alive() for t in threading.enumerate())


class StopAfterReady(threading.Event):
    """Sets itself as soon as "ready" has been printed, i.e. on the first untimed wait()."""

    def wait(self, timeout=None):
        if timeout is None:
            self.set()
        return super().wait(timeout)


def test_ready_only_after_speaker_link_then_clean_quit(app, capsys):
    mini = FakeMini()
    speaker_up_after(mini.media, 0.1)
    app.run(mini, StopAfterReady())
    assert capsys.readouterr().out.count("ready") == 1
    assert mini.calls == ["enable_motors", "wake_up", "stop_head_tracking", "goto_sleep"]
    assert mini.media.calls[:2] == ["start_recording", "start_playing"]
    assert not readers_alive()


def test_no_ready_when_stopped_before_the_speaker_link(app, capsys):
    mini = FakeMini()
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    app.run(mini, stop)
    assert "ready" not in capsys.readouterr().out
    assert mini.calls[-2:] == ["stop_head_tracking", "goto_sleep"]
    assert not readers_alive()


def test_speaker_timeout_prints_one_line_and_sleeps(tmp_path, capsys):
    import dataclasses

    cfg = dataclasses.replace(CFG, startup=dataclasses.replace(CFG.startup, speaker_wait_timeout_s=0.1))
    mini = FakeMini()
    Sonny(cfg, log=SessionLog(tmp_path), voice=FakeVoice, robot=robot, link=FakeLink).run(mini, threading.Event())
    out = capsys.readouterr().out
    assert "ready" not in out and "speaker" in out
    assert mini.calls[-1] == "goto_sleep"


def test_ctrl_c_runs_the_quit_sequence(app):
    class CtrlC(threading.Event):
        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt
            return super().wait(timeout)

    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    with pytest.raises(KeyboardInterrupt):
        app.run(mini, CtrlC())
    assert mini.calls[-2:] == ["stop_head_tracking", "goto_sleep"]
    assert not readers_alive()


def test_quit_ends_an_open_session_with_app_quit(app):
    writer = app.log.start_session()
    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterReady())
    last = json.loads(writer.path.read_text().splitlines()[-1])
    assert (last["type"], last["reason"]) == ("session.ended", "app_quit")


def test_readers_stop_even_if_the_robot_fails_at_quit(app, capsys):
    mini = FakeMini()

    def broken():
        raise RuntimeError("Motor communication error")

    mini.goto_sleep = broken
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterReady())
    assert "Motor communication error" in capsys.readouterr().out
    assert not readers_alive()


def test_main_refuses_without_an_llm_key(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "load_dotenv", lambda *_: None)
    monkeypatch.delenv(CFG.llm.api_key_env, raising=False)
    monkeypatch.setattr(main_mod, "check_startup", lambda *_: pytest.fail("must not reach the daemon"))
    assert main_mod.main() == 1
    assert CFG.llm.api_key_env in capsys.readouterr().out


def test_main_refuses_and_exits_before_connecting(monkeypatch, capsys):
    monkeypatch.setenv(CFG.llm.api_key_env, "test-key")

    def refuse(*_):
        raise StartupError("A dashboard app is running on Sonny: stop marionette in Sonny's dashboard first.")

    monkeypatch.setattr(main_mod, "check_startup", refuse)
    monkeypatch.setattr(main_mod, "Sonny", lambda *a, **k: pytest.fail("app must not start"))
    assert main_mod.main() == 1
    assert capsys.readouterr().out.strip() == (
        "A dashboard app is running on Sonny: stop marionette in Sonny's dashboard first."
    )


def test_the_link_watch_runs_from_ready_and_stops_before_the_readers(app):
    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterReady())
    assert FakeLink.calls == ["start", "join"]  # no reconnect can race the readers stopping


def test_without_a_dashboard_build_sonny_still_starts_and_says_so(app, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main_mod, "STATIC_DIR", tmp_path / "static")  # never built
    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterReady())
    out = capsys.readouterr().out
    assert "ready" in out
    assert "Dashboard not built (run bun run build in web/)" in out
    assert "Dashboard: http" not in out


@pytest.fixture
def built(monkeypatch, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Sonny</title>")
    monkeypatch.setattr(main_mod, "STATIC_DIR", static)


@pytest.mark.parametrize("open_browser", [False, True])
def test_on_ready_sonny_prints_the_dashboard_url_and_opens_a_tab_only_if_asked(
    built, open_browser, monkeypatch, tmp_path, capsys
):
    import dataclasses

    cfg = dataclasses.replace(CFG, dashboard=dataclasses.replace(CFG.dashboard, open_browser=open_browser))
    opened: list[str] = []
    monkeypatch.setattr(main_mod.webbrowser, "open", opened.append)
    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    Sonny(cfg, log=SessionLog(tmp_path), voice=FakeVoice, robot=robot, link=FakeLink).run(mini, StopAfterReady())
    assert "Dashboard: http://127.0.0.1:8042" in capsys.readouterr().out.splitlines()
    assert opened == (["http://127.0.0.1:8042"] if open_browser else [])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servers:
    """Records the uvicorn servers the base class starts, so a test can see where they listen."""

    started: ClassVar[list] = []

    def __init__(self, monkeypatch) -> None:
        import uvicorn

        Servers.started = []

        class Recording(uvicorn.Server):
            def __init__(self, config) -> None:
                super().__init__(config)
                Servers.started.append(self)

        monkeypatch.setattr(uvicorn, "Server", Recording)

    @staticmethod
    def wait_listening(timeout: float = 5) -> list[tuple]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if Servers.started and Servers.started[0].started:
                return [sock.getsockname()[:2] for srv in Servers.started[0].servers for sock in srv.sockets]
            time.sleep(0.02)
        pytest.fail("the Dashboard server never came up")


@pytest.fixture
def dashboard_app(monkeypatch, tmp_path):
    """A Sonny on a free port whose `run` is replaced by the test; `wrapped_run` is real except for the robot."""
    import contextlib
    import dataclasses

    import reachy_mini.apps.app as base

    monkeypatch.setattr(base, "ReachyMini", lambda *a, **k: contextlib.nullcontext(FakeMini()))
    servers = Servers(monkeypatch)
    cfg = dataclasses.replace(CFG, dashboard=dataclasses.replace(CFG.dashboard, port=free_port()))
    app = Sonny(cfg, log=SessionLog(tmp_path), voice=FakeVoice, robot=robot, link=FakeLink)
    return app, servers


def run_in_thread(app) -> threading.Thread:
    t = threading.Thread(target=app.wrapped_run, daemon=True)
    t.start()
    return t


def test_the_dashboard_listens_on_127_0_0_1_at_the_configured_port_and_stops_with_stop_event(dashboard_app):
    app, servers = dashboard_app
    seen: list = []

    def run(mini, stop_event) -> None:
        seen.append(servers.wait_listening())
        stop_event.wait()

    app.run = run
    t = run_in_thread(app)
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        time.sleep(0.02)
    app.stop()
    t.join(5)
    assert not t.is_alive()
    assert seen == [[("127.0.0.1", app.cfg.dashboard.port)]]
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", app.cfg.dashboard.port), timeout=0.5)


def test_quitting_with_the_page_open_does_not_hang(dashboard_app):
    app, servers = dashboard_app

    def run(mini, stop_event) -> None:
        servers.wait_listening()
        page = http.client.HTTPConnection("127.0.0.1", app.cfg.dashboard.port, timeout=5)
        page.request("GET", "/")
        page.getresponse().read()  # the tab keeps its keep-alive connection open
        stop_event.wait()

    app.run = run
    t = run_in_thread(app)
    servers.wait_listening()
    time.sleep(0.2)
    app.stop()
    t.join(5)
    assert not t.is_alive()


def test_the_dashboard_serves_the_camera_feed_the_vision_worker_fills(app):
    import numpy as np
    from fastapi.testclient import TestClient

    with TestClient(app.settings_app).websocket_connect("/ws/camera") as ws:
        app.camera.offer(np.zeros((720, 1280, 3), np.uint8), 1.0)
        assert len(ws.receive_bytes()) > 4


def test_quitting_with_the_camera_feed_open_does_not_hang(dashboard_app):
    import numpy as np
    from websockets.sync.client import connect

    app, servers = dashboard_app
    app.run = lambda mini, stop_event: stop_event.wait()
    t = run_in_thread(app)
    servers.wait_listening()
    with connect(f"ws://127.0.0.1:{app.cfg.dashboard.port}/ws/camera") as ws:
        app.camera.offer(np.zeros((720, 1280, 3), np.uint8), 1.0)
        assert len(ws.recv(timeout=5)) > 4
        app.stop()
        t.join(5)
    assert not t.is_alive()


def test_the_voice_pipeline_gets_the_camera_feed(tmp_path):
    seen: list = []

    def voice(*args):
        seen.extend(args)
        return FakeVoice()

    app = Sonny(CFG, log=SessionLog(tmp_path), voice=voice, robot=robot, link=FakeLink)
    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterReady())
    assert app.camera in seen


def test_the_models_load_while_the_speaker_link_comes_up(tmp_path, capsys):
    """Loading the voice pipeline (seconds of models) overlaps the robot's startup; "ready" still waits for both."""
    mini = FakeMini()
    loading = threading.Event()

    def voice(*_args):
        loading.set()
        assert mini.media.audio._appsrc is None  # the speaker link isn't up yet
        return FakeVoice()

    app = Sonny(CFG, log=SessionLog(tmp_path), voice=voice, robot=robot, link=FakeLink)
    threading.Thread(target=lambda: loading.wait(5) and setattr(mini.media.audio, "_appsrc", object())).start()
    app.run(mini, StopAfterReady())
    assert capsys.readouterr().out.count("ready") == 1


def test_a_failed_voice_load_still_quits_cleanly(tmp_path):
    def voice(*_args):
        raise RuntimeError("no models")

    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    with pytest.raises(RuntimeError, match="no models"):
        Sonny(CFG, log=SessionLog(tmp_path), voice=voice, robot=robot, link=FakeLink).run(mini, threading.Event())
    assert mini.calls[-2:] == ["stop_head_tracking", "goto_sleep"]
    assert not readers_alive()


def test_quitting_with_the_state_feed_open_does_not_hang(dashboard_app):
    from websockets.sync.client import connect

    app, servers = dashboard_app
    app.run = lambda mini, stop_event: stop_event.wait()
    t = run_in_thread(app)
    servers.wait_listening()
    with connect(f"ws://127.0.0.1:{app.cfg.dashboard.port}/rpc") as ws:
        ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}}))
        assert json.loads(ws.recv(timeout=5))["result"]["seq"] == 0
        app.stop()
        t.join(5)
    assert not t.is_alive()


def test_the_page_gets_what_sonny_logs_and_publishes_over_rpc(app):
    from fastapi.testclient import TestClient

    from sonny.events import Event, EventType

    app.bus.publish(Event(EventType.LINK_RESTORED, {"seconds": 3.0}))  # before any page connects
    with TestClient(app.settings_app) as client, client.websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}})
        snap = ws.receive_json()["result"]
        assert [e["type"] for e in snap["recent"]] == ["link.restored"]
        app.log.idle.write("wake", score=0.95)
        notes = [ws.receive_json() for _ in range(2)]
        # The snapshot sent the bus event out too; a page drops envelopes with seq <= snapshot.seq.
        assert [(n["method"], n["params"]["seq"], n["params"]["type"]) for n in notes] == [
            ("sonny.event", 1, "link.restored"),
            ("sonny.event", 2, "wake"),
        ]
        assert snap["seq"] == 1


def test_health_ticks_run_while_sonny_runs_and_stop_at_quit(tmp_path):
    import dataclasses

    cfg = dataclasses.replace(CFG, dashboard=dataclasses.replace(CFG.dashboard, health_every_s=0.01))
    app = Sonny(cfg, log=SessionLog(tmp_path), voice=FakeVoice, robot=robot, link=FakeLink)
    ticks: list[dict] = []
    app.feed.listen(lambda e: e["type"] == "health" and ticks.append(e["data"]))

    class StopAfterTicks(threading.Event):
        def wait(self, timeout=None):
            if timeout is None:
                while len(ticks) < 2:
                    time.sleep(0.01)
                self.set()
            return super().wait(timeout)

    mini = FakeMini()
    speaker_up_after(mini.media, 0.05)
    app.run(mini, StopAfterTicks())
    assert ticks[-1]["down"] is None
    assert not any(t.name == "sonny-dashboard-health" and t.is_alive() for t in threading.enumerate())
