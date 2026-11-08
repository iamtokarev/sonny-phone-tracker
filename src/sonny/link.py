"""The link watch (spec §4 "Blind", §9 "Failures"): is the robot link up, and if not, why; reconnecting when it
can help.

- **Down**, by precedence:
  - `daemon_unreachable`: the daemon's status route doesn't answer;
  - `daemon_error`: the daemon reports an error, such as "Motor communication error";
  - `media_drop`: neither a frame nor a mic chunk for `blind.freeze_s` (the WebRTC session is gone: video,
    mic and speaker go together);
  - `video_freeze`: no frame for `blind.freeze_s`, while the mic still flows.
- **The daemon** is asked every `blind.daemon_check_s` while it answers and every `blind.reconnect_every_s`
  while it doesn't. A failure gets one terminal line per outage: what failed, and that a power-cycle fixes it.
  Mic and speaker are the WebRTC session's, so with a daemon error and the mic flowing Sonny carries on
  voice-only (the head owner answers `play_emotion` with `unavailable` while the motors are down).
- **Reconnect** (`Repair.relink`) at once, then every `blind.reconnect_every_s`, while the media session is down
  and the daemon answers (after one that came up, `blind.freeze_s` later, so the new session can deliver). It
  rebuilds the media session and waits for the speaker link as at startup. Nothing is retried for a video
  freeze (the session is up: a fresh frame ends it) or a daemon error (only a power-cycle fixes it; that
  restarts the daemon, which is then unreachable for a while, then reachable with the media session gone, and
  so reconnected).

The watch runs on its own thread the whole time the app runs, so a link lost outside a session comes back
too. Whether the session is Blind is the conductor's call (`sonny.focus.failures`), from `down`.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from loguru import logger

from sonny.config import Config
from sonny.daemon import DaemonError


class Feeds(Protocol):
    """The media hub's arrival times (`sonny.media.MediaHub`)."""

    def latest_frame(self) -> tuple[np.ndarray | None, float]: ...
    @property
    def audio_t(self) -> float: ...


class Daemon(Protocol):
    """The daemon's HTTP API (`sonny.daemon.DaemonClient`)."""

    def request(self, path: str, body: Any = None) -> Any: ...


class Repair(Protocol):
    """Brings the robot link back (`ReachyRepair`). Raises when it couldn't."""

    def relink(self) -> None: ...


class LinkWatch:
    def __init__(
        self,
        cfg: Config,
        feeds: Feeds,
        daemon: Daemon,
        repair: Repair,
        write: Callable[..., None],
        now: Callable[[], float] = time.time,  # wall time, like the hub's arrival times
        say: Callable[[str], None] = logger.warning,
    ) -> None:
        self._c = cfg.blind
        self._feeds, self._daemon, self._repair, self._write, self._now, self._say = (
            feeds,
            daemon,
            repair,
            write,
            now,
            say,
        )
        self._since = now()  # before the first frame or chunk, ages count from here
        self.down: str | None = None
        self._daemon_down: str | None = None  # "daemon_unreachable" or "daemon_error", from the last probe
        self._probed = -float("inf")
        self._attempted = -float("inf")
        self._attempts = 0
        self._out_since = self._since  # when the link last went down
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- the thread

    def start(self, stop: threading.Event) -> None:
        self._thread = threading.Thread(target=self._run, args=(stop,), name="sonny-link", daemon=True)
        self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(self._c.check_s):
            try:
                self.check()
            except Exception as e:
                logger.warning("check failed: {}: {}", type(e).__name__, e)

    def recheck(self) -> None:
        """Ask the daemon at the next check, and allow a reconnect then (after Mac sleep). Thread-safe."""
        self._probed = self._attempted = -float("inf")

    # ---------------------------------------------------------------- one check

    def check(self) -> None:
        """Read the feeds, ask the daemon when due, set `down`, and reconnect when that can help."""
        now = self._now()
        if now - self._probed >= (self._c.daemon_check_s if self._daemon_down is None else self._c.reconnect_every_s):
            self._probe(now)
        down = self._reason(now)
        if down is not None and self.down is None:
            self._out_since = now
        self.down = down
        if down == "media_drop" and now - self._attempted >= self._c.reconnect_every_s:
            self._relink(now)
            self.down = self._reason(self._now())
        if self.down is None and self._attempts:
            self._say(f"Sonny reconnected after {self._now() - self._out_since:.0f} s")
            self._attempts = 0

    def _reason(self, now: float) -> str | None:
        frame_age = now - (self._feeds.latest_frame()[1] or self._since)
        audio_age = now - (self._feeds.audio_t or self._since)
        if self._daemon_down is not None:
            return self._daemon_down
        if frame_age >= self._c.freeze_s:
            return "media_drop" if audio_age >= self._c.freeze_s else "video_freeze"
        return None

    def _probe(self, now: float) -> None:
        self._probed = now
        was = self._daemon_down
        try:
            status = self._daemon.request("/api/daemon/status") or {}
        except DaemonError as e:
            self._daemon_down = "daemon_unreachable"
            if was is None:
                self._say(f"{e}. If it doesn't come back, power-cycle Sonny.")
            return
        error = status.get("error")
        if status.get("state") == "error" or error:
            self._daemon_down = "daemon_error"
            if was is None:
                audio_age = now - (self._feeds.audio_t or self._since)
                voice = " Carrying on voice-only." if audio_age < self._c.freeze_s else ""
                self._say(
                    f"Sonny's daemon reports an error: {error or 'unknown'}. A power-cycle of Sonny fixes it.{voice}"
                )
            return
        self._daemon_down = None

    def _relink(self, now: float) -> None:
        self._attempted = now
        self._attempts += 1
        if self._attempts == 1:
            self._say(f"Sonny's media link is down; reconnecting every {self._c.reconnect_every_s:g} s")
        error: str | None = None
        try:
            self._repair.relink()
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        self._write("reconnect", ok=error is None, attempt=self._attempts, error=error)
        if error is None:
            self._attempted = self._now() + self._c.freeze_s  # the new session gets that long to deliver


class _Hub(Protocol):
    def reconnect(self, stop_event: threading.Event) -> bool: ...


class _Head(Protocol):
    def revive(self) -> None: ...


class ReachyRepair:
    """The reconnect on Sonny, once the daemon answers again: a new SDK connection if the old one died, a new
    media session (waiting for the speaker link as at startup), and, after a daemon restart (motors come up
    disabled), `enable_motors()` and the head woken back into its mode's pose.

    The SDK can't reconnect by itself; `_connect_single` is how its constructor connects (reachy-mini 1.14.0)."""

    def __init__(self, cfg: Config, mini: Any, hub: _Hub, daemon: Daemon, head: _Head, stop: threading.Event) -> None:
        self._timeout_s = cfg.blind.reconnect_every_s
        self._mini, self._hub, self._daemon, self._head, self._stop = mini, hub, daemon, head, stop

    def relink(self) -> None:
        client = self._mini.client
        if not getattr(client, "_is_alive", True):
            client.disconnect()
            self._mini.client = self._mini._connect_single(host=client.host, port=client.port, timeout=self._timeout_s)
        if not self._hub.reconnect(self._stop):
            raise RuntimeError("the speaker link didn't come up")
        motors = self._daemon.request("/api/motors/status") or {}
        if motors.get("mode") != "enabled":
            self._mini.enable_motors()  # motors come up disabled after a daemon restart
            self._head.revive()
