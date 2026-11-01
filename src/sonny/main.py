"""Sonny: a Mac-run ReachyMiniApp (spec §2). Boots to "ready", holds idle voice conversations, quits cleanly.

Run with `uv run sonny` from a terminal that has macOS Local Network permission.
"""

import asyncio
import os
import sys
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from dotenv import find_dotenv, load_dotenv
from reachy_mini import ReachyMini, ReachyMiniApp

from sonny.config import Config, load_config
from sonny.daemon import DaemonClient, StartupError, check_startup, sdk_version
from sonny.events import EndReason, EventBus
from sonny.head import HeadOwner, Robot
from sonny.media import MediaHub
from sonny.robot import ReachyRobot
from sonny.session_log import SessionLog


class VoiceLoop(Protocol):
    def run(self, stop: threading.Event, on_ready: Callable[[], None]) -> Coroutine[Any, Any, None]: ...


VoiceFactory = Callable[[Config, MediaHub, SessionLog, EventBus, HeadOwner], VoiceLoop]
RobotFactory = Callable[[Config, ReachyMini], Robot]


def make_voice(cfg: Config, hub: MediaHub, log: SessionLog, bus: EventBus, head: HeadOwner) -> VoiceLoop:
    from sonny.voice.pipeline import VoicePipeline  # loads Pipecat and the models only when the app runs

    return VoicePipeline(cfg, hub, log, bus, head, api_key=os.environ[cfg.llm.api_key_env])


def make_robot(cfg: Config, mini: ReachyMini) -> Robot:
    daemon = DaemonClient(f"http://{cfg.robot.host}:{cfg.robot.port}", cfg.robot.http_timeout_s)
    return ReachyRobot(mini, daemon, cfg.head.emotions_dataset)


class Sonny(ReachyMiniApp):
    custom_app_url: str | None = None  # no settings page, no web server
    request_media_backend: str | None = None

    def __init__(
        self,
        cfg: Config,
        log: SessionLog | None = None,
        voice: VoiceFactory = make_voice,
        robot: RobotFactory = make_robot,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.bus = EventBus()
        self.log = log or SessionLog(cfg.log.dir)
        self._make_voice, self._make_robot = voice, robot

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        hub = MediaHub(reachy_mini, self.cfg.media, self.cfg.startup)
        head = HeadOwner(
            self.cfg,
            self._make_robot(self.cfg, reachy_mini),
            lambda type, **f: (self.log.session or self.log.idle).write(type, **f),
        )
        try:
            reachy_mini.enable_motors()  # motors come up disabled after a daemon restart
            head.start()
            hub.start()
            if not hub.wait_speaker_ready(stop_event):
                if not stop_event.is_set():
                    print(
                        f"Sonny's speaker link didn't come up within {self.cfg.startup.speaker_wait_timeout_s:g} s; "
                        "restart the app."
                    )
                return
            voice = self._make_voice(self.cfg, hub, self.log, self.bus, head)
            asyncio.run(voice.run(stop_event, lambda: print("ready", flush=True)))
        finally:
            self._quit(head, hub)

    def _quit(self, head: HeadOwner, hub: MediaHub) -> None:
        """Spec §2 "Quit". Each step runs even if an earlier one fails, so the readers always
        stop before `ReachyMini` closes."""
        _step("end session log", lambda: self.log.end_session(EndReason.APP_QUIT))
        _step("stop tracking and go to sleep", head.quit)
        _step("stop media readers", hub.stop)
        _step("close log", self.log.close)


def _step(name: str, fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception as e:
        print(f"quit: {name} failed: {e}", flush=True)


def main() -> int:
    cfg = load_config()
    load_dotenv(find_dotenv(usecwd=True))  # the repo-root .env holds the LLM key
    if not os.environ.get(cfg.llm.api_key_env):
        print(f"{cfg.llm.api_key_env} is not set; put it in the repo-root .env.")
        return 1
    try:
        check_startup(
            DaemonClient(f"http://{cfg.robot.host}:{cfg.robot.port}", cfg.robot.http_timeout_s), sdk_version()
        )
    except StartupError as e:
        print(e)
        return 1
    app = Sonny(cfg)
    try:
        app.wrapped_run(host=cfg.robot.host, port=cfg.robot.port)
    except KeyboardInterrupt:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
