"""Sonny: a Mac-run ReachyMiniApp (spec §2). Boots to "ready", holds idle voice conversations, quits cleanly.

Run with `uv run sonny` from a terminal that has macOS Local Network permission.
"""

import asyncio
import fcntl
import os
import sys
import threading
import webbrowser
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from pathlib import Path
from typing import IO, Any, Protocol

from dotenv import find_dotenv, load_dotenv
from reachy_mini import ReachyMini, ReachyMiniApp

from sonny.config import Config, load_config
from sonny.daemon import DaemonClient, StartupError, check_startup, ipv4, sdk_version
from sonny.dashboard.camera import CameraFeed
from sonny.dashboard.feed import DashboardFeed, Readings
from sonny.dashboard.serve import announce, dashboard_url, mount_routes
from sonny.events import EndReason, EventBus
from sonny.head import HeadOwner, Robot
from sonny.link import LinkWatch, ReachyRepair
from sonny.media import MediaHub
from sonny.robot import ReachyRobot
from sonny.session_log import SessionLog

# The built Dashboard (`bun run build` in web/). The base class serves this same folder: <app module dir>/static.
STATIC_DIR = Path(__file__).with_name("static")


class VoiceLoop(Protocol):
    def run(self, stop: threading.Event, on_ready: Callable[[], None]) -> Coroutine[Any, Any, None]: ...


class Link(Protocol):
    """The link watch (`sonny.link.LinkWatch`)."""

    @property
    def down(self) -> str | None: ...
    def recheck(self) -> None: ...
    def start(self, stop: threading.Event) -> None: ...
    def join(self, timeout: float) -> None: ...


VoiceFactory = Callable[[Config, MediaHub, SessionLog, EventBus, HeadOwner, Link, CameraFeed], VoiceLoop]
RobotFactory = Callable[[Config, ReachyMini], Robot]
LinkFactory = Callable[[Config, ReachyMini, MediaHub, HeadOwner, threading.Event, Callable[..., None]], Link]


def make_voice(
    cfg: Config, hub: MediaHub, log: SessionLog, bus: EventBus, head: HeadOwner, link: Link, camera: CameraFeed
) -> VoiceLoop:
    from sonny.voice.pipeline import VoicePipeline  # loads Pipecat and the models only when the app runs

    return VoicePipeline(cfg, hub, log, bus, head, link, camera, api_key=os.environ[cfg.llm.api_key_env])


def make_robot(cfg: Config, mini: ReachyMini) -> Robot:
    daemon = DaemonClient(f"http://{cfg.robot.host}:{cfg.robot.port}", cfg.robot.http_timeout_s)
    return ReachyRobot(mini, daemon, cfg.head.emotions_dataset)


def make_link(
    cfg: Config, mini: ReachyMini, hub: MediaHub, head: HeadOwner, stop: threading.Event, write: Callable[..., None]
) -> Link:
    daemon = DaemonClient(f"http://{cfg.robot.host}:{cfg.robot.port}", cfg.robot.http_timeout_s)
    return LinkWatch(cfg, hub, daemon, ReachyRepair(cfg, mini, hub, daemon, head, stop), write)


class Sonny(ReachyMiniApp):
    request_media_backend: str | None = None

    def __init__(
        self,
        cfg: Config,
        log: SessionLog | None = None,
        voice: VoiceFactory = make_voice,
        robot: RobotFactory = make_robot,
        link: LinkFactory = make_link,
    ) -> None:
        # Read by the base class, which serves STATIC_DIR on it in a thread that stops with stop_event (spec §2).
        self.custom_app_url = dashboard_url(cfg.dashboard)
        super().__init__()
        self.cfg = cfg
        self.bus = EventBus()
        self.log = log or SessionLog(cfg.log.dir)
        self.camera = CameraFeed(cfg.dashboard)  # spec §5: filled by the vision worker, served at /ws/camera
        self._make_voice, self._make_robot, self._make_link = voice, robot, link
        # The Dashboard's state feed (spec §4): every log record and bus event, served on /rpc.
        self.feed = DashboardFeed(cfg.dashboard, self.bus)
        self.log.on_record = self.feed.on_record
        if self.settings_app is not None:
            mount_routes(self.settings_app, self.feed, self.camera)
        self._health: threading.Thread | None = None

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        hub = MediaHub(reachy_mini, self.cfg.media, self.cfg.startup)

        def write(type: str, **fields) -> None:
            (self.log.session or self.log.idle).write(type, **fields)

        head = HeadOwner(self.cfg, self._make_robot(self.cfg, reachy_mini), write)
        link_stop = threading.Event()
        link = self._make_link(self.cfg, reachy_mini, hub, head, link_stop, write)
        # The models load (seconds) while the robot wakes up and the speaker link comes up; "ready" waits for both.
        voice = _in_background(
            lambda: self._make_voice(self.cfg, hub, self.log, self.bus, head, link, self.camera), "sonny-voice-load"
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
            link.start(link_stop)  # spec §9: Blind and reconnects
            self._health = self.feed.run_health(
                link_stop, lambda: Readings(link.down, hub.frames, hub.latest_frame()[1] or None)
            )
            asyncio.run(voice.result().run(stop_event, self._ready))
        finally:
            self._quit(head, hub, link, link_stop)

    def _ready(self) -> None:
        print("ready", flush=True)
        if announce(STATIC_DIR, self.custom_app_url) and self.cfg.dashboard.open_browser:
            webbrowser.open(self.custom_app_url)

    def _quit(self, head: HeadOwner, hub: MediaHub, link: Link, link_stop: threading.Event) -> None:
        """Spec §2 "Quit". Each step runs even if an earlier one fails, so the readers always
        stop before `ReachyMini` closes."""
        _step("end session log", lambda: self.log.end_session(EndReason.APP_QUIT))
        link_stop.set()  # no reconnect and no Dashboard health tick from here on
        _step("stop the link watch", lambda: link.join(self.cfg.media.reader_join_timeout_s))
        if (health := self._health) is not None:
            _step("stop the Dashboard health tick", lambda: health.join(self.cfg.media.reader_join_timeout_s))
        _step("stop tracking and go to sleep", head.quit)
        _step("stop media readers", hub.stop)
        _step("close log", self.log.close)


def _in_background[T](fn: Callable[[], T], name: str) -> Future[T]:
    """`fn()` on a daemon thread, so a quit during startup needn't wait for it."""
    future: Future[T] = Future()

    def work() -> None:
        try:
            future.set_result(fn())
        except BaseException as e:
            future.set_exception(e)

    threading.Thread(target=work, name=name, daemon=True).start()
    return future


def prewarm(cfg: Config) -> None:
    """Import the voice pipeline's libraries (Pipecat, torch, CoreML: seconds) while `main()` checks the daemon and
    connects. An import that fails here fails again where it is used, and says so there."""

    def imports() -> None:
        try:
            import sonny.voice.pipeline  # noqa: F401

            if cfg.vision.backend == "coreml":
                import coremltools  # noqa: F401
                from rfdetr.export._runtime import decode  # noqa: F401
        except Exception:
            pass

    threading.Thread(target=imports, name="sonny-prewarm", daemon=True).start()


def _step(name: str, fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception as e:
        print(f"quit: {name} failed: {e}", flush=True)


def claim_instance(log_dir: Path | str) -> IO[str] | None:
    """Lock `sonny.lock` in the log folder for this process's lifetime; None if another Sonny holds it. Keep the
    returned file open: the lock goes with it."""
    path = Path(log_dir) / "sonny.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    f.truncate(0)
    f.write(f"{os.getpid()}\n")
    f.flush()
    return f


def main() -> int:
    cfg = load_config()
    if (lock := claim_instance(cfg.log.dir)) is None:
        pid = (Path(cfg.log.dir) / "sonny.lock").read_text().strip()
        print(f"Sonny is already running (pid {pid}); quit it first (Ctrl-C in its terminal).")
        return 1
    prewarm(cfg)
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
        app.wrapped_run(host=ipv4(cfg.robot.host), port=cfg.robot.port)  # see `ipv4`: saves ~1 s per connect
    except KeyboardInterrupt:
        app.stop()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
