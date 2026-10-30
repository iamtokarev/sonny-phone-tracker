"""Sonny: a Mac-run ReachyMiniApp (spec §2). Boots to "ready" and quits cleanly.

Run with `uv run sonny` from a terminal that has macOS Local Network permission.
"""

import sys
import threading
from collections.abc import Callable

from reachy_mini import ReachyMini, ReachyMiniApp

from sonny.config import Config, load_config
from sonny.daemon import DaemonClient, StartupError, check_startup, sdk_version
from sonny.events import EndReason, EventBus
from sonny.media import MediaHub
from sonny.session_log import SessionLog


class Sonny(ReachyMiniApp):
    custom_app_url: str | None = None  # no settings page, no web server
    request_media_backend: str | None = None

    def __init__(self, cfg: Config, log: SessionLog | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.bus = EventBus()
        self.log = log or SessionLog(cfg.log.dir)

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        hub = MediaHub(reachy_mini, self.cfg.media, self.cfg.startup)
        try:
            reachy_mini.enable_motors()  # motors come up disabled after a daemon restart
            reachy_mini.wake_up()
            hub.start()
            if not hub.wait_speaker_ready(stop_event):
                if not stop_event.is_set():
                    print(
                        f"Sonny's speaker link didn't come up within {self.cfg.startup.speaker_wait_timeout_s:g} s; "
                        "restart the app."
                    )
                return
            print("ready", flush=True)
            stop_event.wait()
        finally:
            self._quit(reachy_mini, hub)

    def _quit(self, mini: ReachyMini, hub: MediaHub) -> None:
        """Spec §2 "Quit". Each step runs even if an earlier one fails, so the readers always
        stop before `ReachyMini` closes."""
        _step("end session log", lambda: self.log.end_session(EndReason.APP_QUIT))
        _step("stop tracking", mini.stop_head_tracking)
        _step("go to sleep", mini.goto_sleep)
        _step("stop media readers", hub.stop)
        _step("close log", self.log.close)


def _step(name: str, fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception as e:
        print(f"quit: {name} failed: {e}", flush=True)


def main() -> int:
    cfg = load_config()
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
