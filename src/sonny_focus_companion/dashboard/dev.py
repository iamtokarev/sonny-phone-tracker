"""The robot-free Dashboard backend. Run it with `mise run dev:backend`.

The same FastAPI routes Sonny serves (the base class's `/` and `/static`, `/rpc`, `/ws/camera`) on the same port,
the same `DashboardFeed` and `CameraFeed`, fed by:

- **a replayer** over session logs (`--log logs/session-*.jsonl`): each file's records go to `DashboardFeed.on_record`
  in order, as Sonny's log tap would hand them over, at `--speed` times the logged pace; several files play one
  after another, and the last one loops back to the first. Each record is stamped with the time it goes out, and
  a session's `duration_min` (and an extend's `extended_by_min`) is divided by the speed, so the page's clock
  runs out when the replayed session does;
- **a camera source**: the Mac webcam (`--webcam`) or a video file (`--clip`), through the real detector while
  the replayed session is watched (always, with no `--log`), raw frames otherwise, as the vision worker does; or,
  with neither, a drawn placeholder scene whose boxes are where it drew the person and the phone;
- a health tick, as in Sonny, with the camera's frame count.

No robot, no voice, no LLM; nothing is written to `logs/`.
"""

import argparse
import json
import math
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI
from loguru import logger
from reachy_mini import ReachyMiniApp

import sonny_focus_companion.main
from sonny_focus_companion.attention import AttentionState, Box, Detections
from sonny_focus_companion.config import Config, load_config
from sonny_focus_companion.dashboard.camera import CameraFeed
from sonny_focus_companion.dashboard.feed import DashboardFeed, Readings
from sonny_focus_companion.dashboard.origin import HOST
from sonny_focus_companion.dashboard.serve import announce, dashboard_url, mount_routes
from sonny_focus_companion.logs import setup

Record = dict[str, Any]


# ------------------------------------------------------------------ the replayer


def read_log(path: Path) -> list[Record]:
    """A session log's records; a line cut short by a crash is skipped."""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "ts" in record and "type" in record:
            records.append(record)
    return records


class Replayer:
    """Session logs played back as `{ts, type, ...}` records, each file on its own timing scaled by `speed`.

    `sleep(s)` waits and says whether to stop (`threading.Event.wait`); `clock` is wall time, like the log's `ts`.
    """

    def __init__(
        self,
        paths: Iterable[Path],
        speed: float,
        emit: Callable[[Record], None],
        gap_s: float = 2.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be above 0")
        self.paths, self.speed, self.gap_s = list(paths), speed, gap_s
        self._emit, self._clock = emit, clock
        self._sleep = sleep or threading.Event().wait

    def run(self, loops: int | None = None) -> None:
        """Play every file in turn, `loops` times (forever when None), until `sleep` says stop."""
        done = 0
        while loops is None or done < loops:
            for path in self.paths:
                if self._play(read_log(path)) or self._sleep(self.gap_s):
                    return
            done += 1

    def _play(self, records: list[Record]) -> bool:
        if not records:
            return False
        start, first = self._clock(), records[0]["ts"]
        for record in records:
            due = start + (record["ts"] - first) / self.speed
            if (wait := due - self._clock()) > 0 and self._sleep(wait):
                return True
            self._emit(self._restamp(record, due))
        return False

    def _restamp(self, record: Record, ts: float) -> Record:
        out = {**record, "ts": ts}
        if out["type"] == "session.started" and "duration_min" in out:
            out["duration_min"] = out["duration_min"] / self.speed
        result = out.get("result")
        if out["type"] == "tool_call" and isinstance(result, dict) and "extended_by_min" in result:
            out["result"] = {**result, "extended_by_min": result["extended_by_min"] / self.speed}
        return out


# ------------------------------------------------------------------ the camera

# BGR of the board's tokens (spec §9): the placeholder draws in the page's own colours.
_PAPER, _INK, _MUTED = (230, 237, 239), (17, 17, 17), (79, 87, 90)


def placeholder_frame(
    t: float, attention: str | None, width: int = 1280, height: int = 720
) -> tuple[np.ndarray, Detections]:
    """A drawn desk scene for time `t`: the owner at the desk, holding a phone during Phone Use, gone when Away.
    Its Detections are the boxes of what it drew."""
    image = np.full((height, width, 3), _PAPER, np.uint8)
    u = width / 1280
    desk_y = int(560 * u)
    cv2.line(image, (0, desk_y), (width, desk_y), _INK, max(2, int(4 * u)))
    cv2.rectangle(image, (int(80 * u), int(300 * u)), (int(360 * u), desk_y), _MUTED, max(2, int(3 * u)))  # monitor
    cv2.putText(image, "DEV PLACEHOLDER", (int(24 * u), int(48 * u)), cv2.FONT_HERSHEY_SIMPLEX, u, _INK, 2)
    clock = time.strftime("%H:%M:%S", time.localtime(t))
    cv2.putText(image, clock, (int(24 * u), int(88 * u)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * u, _MUTED, 2)
    if attention == "away":
        return image, Detections(width)

    sway = 18 * u * math.sin(t * 0.8)
    cx, head_y, r = 760 * u + sway, 250 * u, 70 * u
    cv2.circle(image, (int(cx), int(head_y)), int(r), _INK, -1)
    torso = np.array(
        [(cx - 150 * u, height), (cx - 120 * u, 360 * u), (cx + 120 * u, 360 * u), (cx + 150 * u, height)], np.int32
    )
    cv2.fillPoly(image, [torso], _INK)
    person = Box(0.95, cx - 150 * u, head_y - r, cx + 150 * u, float(height))
    phone = None
    if attention == "phone_use":
        px, py = cx - 40 * u + 6 * u * math.sin(t * 3), 400 * u
        x0, y0, x1, y1 = px - 32 * u, py - 58 * u, px + 32 * u, py + 58 * u
        cv2.rectangle(image, (int(x0), int(y0)), (int(x1), int(y1)), _PAPER, -1)
        cv2.rectangle(image, (int(x0), int(y0)), (int(x1), int(y1)), _MUTED, max(2, int(3 * u)))
        phone = Box(0.9, x0, y0, x1, y1)
    return image, Detections(width, phone=phone, person=person)


class CameraLoop:
    """Frames into the `CameraFeed`, watched (detected, with the Attention State) while the replayed session is."""

    def __init__(
        self,
        cfg: Config,
        feed: DashboardFeed,
        camera: CameraFeed,
        capture: cv2.VideoCapture | None,  # the webcam or a clip, opened; None for the placeholder
        always_watch: bool,
        loop_clip: bool = False,  # a clip: rewind at the end, at the clip's own frame rate
    ) -> None:
        self.cfg, self._feed, self._camera, self._always = cfg, feed, camera, always_watch
        self._capture, self._clip = capture, loop_clip
        # Written only by the camera thread, read by the health tick without a lock: each is one int or float
        # reference, which CPython swaps atomically. The two may be one frame apart, which a health reading allows.
        self.frames = 0
        self.frame_t = 0.0

    def watched(self) -> tuple[bool, str | None]:
        """Whether Sonny would be watching now, and the Attention State, from the replayed feed."""
        snap = self._feed.snapshot()
        session, attention = snap["session"], snap["attention"]
        if session is None or attention is None or session["paused_since"] is not None or session["blind"]:
            return self._always, None
        return True, attention

    def run(self, stop: threading.Event) -> None:
        if self._capture is None:
            self._run_placeholder(stop)
        else:
            self._run_capture(self._capture, stop)

    def _run_placeholder(self, stop: threading.Event) -> None:
        while not stop.wait(1 / self.cfg.dashboard.camera_fps):
            watching, attention = self.watched()
            t = time.time()
            image, d = placeholder_frame(t, attention if watching else None)  # resting: at the desk, no boxes
            self._offer(image, t, d if watching else None, attention)

    def _run_capture(self, cap: cv2.VideoCapture, stop: threading.Event) -> None:
        from sonny_focus_companion.detector import load_detector

        detector = load_detector(self.cfg.vision)
        logger.info("RF-DETR Nano on {}", detector.name)
        pace = 1 / (cap.get(cv2.CAP_PROP_FPS) or 30) if self._clip else 0.0  # the webcam paces itself
        try:
            while not stop.is_set():
                started = time.monotonic()
                ok, image = cap.read()
                if not ok:
                    if self._clip:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # the clip loops
                    else:
                        stop.wait(0.1)
                    continue
                t = time.time()
                watching, attention = self.watched()
                self._offer(image, t, detector.detect(image) if watching else None, attention)
                if (wait := pace - (time.monotonic() - started)) > 0:
                    stop.wait(wait)
        finally:
            cap.release()

    def _offer(self, image: np.ndarray, t: float, d: Detections | None, attention: str | None) -> None:
        self.frames += 1
        self.frame_t = t
        state = AttentionState(attention) if d is not None and attention else None
        self._camera.offer(image, t, d, state)


# ------------------------------------------------------------------ the app


class _Routes(ReachyMiniApp):
    """Only for the base class's FastAPI app: `/` and `/static` from Sonny's own static folder, as Sonny has them."""

    def __init__(self, cfg: Config) -> None:
        self.custom_app_url = dashboard_url(cfg.dashboard)
        super().__init__()

    # Private base-class hook (reachy-mini ==1.14.0); tests/test_reachy_private_api.py guards it.
    def _get_instance_path(self) -> Path:
        return Path(sonny_focus_companion.main.__file__).resolve()  # the static folder sits next to Sonny's module

    def run(self, reachy_mini, stop_event) -> None:  # never runs: there is no robot
        raise NotImplementedError


def build_app(cfg: Config) -> tuple[FastAPI, DashboardFeed, CameraFeed]:
    """Sonny's Dashboard routes with a feed and a camera of their own."""
    app = _Routes(cfg).settings_app
    assert app is not None
    feed, camera = DashboardFeed(cfg.dashboard), CameraFeed(cfg.dashboard)
    mount_routes(app, feed, camera)
    return app, feed, camera


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sonny-focus-companion-dashboard-dev", description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", type=Path, nargs="+", default=[], help="session logs to replay (they loop)")
    parser.add_argument("--speed", type=float, default=1.0, help="replay N times faster than logged")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--webcam", action="store_true", help="the Mac webcam through the real detector")
    source.add_argument("--clip", type=Path, help="a video file (looping) through the real detector")
    args = parser.parse_args(argv)
    if args.speed <= 0:
        parser.error("--speed must be above 0")
    if missing := [str(p) for p in args.log if not p.exists()]:
        parser.error(f"no such log: {', '.join(missing)}")

    import uvicorn

    setup(None)
    cfg = load_config()
    app, feed, camera = build_app(cfg)
    stop = threading.Event()
    capture = None
    if args.webcam or args.clip:
        # On the main thread: macOS asks for camera access only from there.
        capture = cv2.VideoCapture(0 if args.webcam else str(args.clip))
        if not capture.isOpened():
            logger.error(
                "Can't open {}", "the webcam (allow camera access for this terminal)" if args.webcam else args.clip
            )
            return 1
    loop = CameraLoop(cfg, feed, camera, capture, always_watch=not args.log, loop_clip=args.clip is not None)
    threading.Thread(target=loop.run, args=(stop,), name="sonny-focus-companion-dev-camera", daemon=True).start()
    if args.log:
        replayer = Replayer(args.log, args.speed, feed.on_record, sleep=stop.wait)
        threading.Thread(target=replayer.run, name="sonny-focus-companion-dev-replay", daemon=True).start()
    feed.run_health(stop, lambda: Readings(None, loop.frames, loop.frame_t or None))

    announce(sonny_focus_companion.main.STATIC_DIR, dashboard_url(cfg.dashboard))
    logger.info("With bun run dev in web/: http://{}:{}/static/", HOST, cfg.dashboard.dev_port)
    try:
        uvicorn.run(app, host=HOST, port=cfg.dashboard.port, log_level="warning")
    finally:
        stop.set()
    return 0
