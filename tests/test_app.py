import json
import threading

import pytest

import sonny.main as main_mod
from sonny.config import load_config
from sonny.daemon import StartupError
from sonny.main import Sonny
from sonny.session_log import SessionLog
from tests.fakes import FakeMedia, FakeMini

CFG = load_config()


@pytest.fixture
def app(tmp_path):
    return Sonny(CFG, log=SessionLog(tmp_path))


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
    Sonny(cfg, log=SessionLog(tmp_path)).run(mini, threading.Event())
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


def test_main_refuses_and_exits_before_connecting(monkeypatch, capsys):
    def refuse(*_):
        raise StartupError("A dashboard app is running on Sonny: stop marionette in Sonny's dashboard first.")

    monkeypatch.setattr(main_mod, "check_startup", refuse)
    monkeypatch.setattr(main_mod, "Sonny", lambda *a, **k: pytest.fail("app must not start"))
    assert main_mod.main() == 1
    assert capsys.readouterr().out.strip() == (
        "A dashboard app is running on Sonny: stop marionette in Sonny's dashboard first."
    )
