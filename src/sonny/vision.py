"""The vision worker (spec §2 "Vision worker", §3, §4, §5, §10): RF-DETR on the media hub's latest frame, feeding
the attention rules and the framing rules of the Focus Session.

- **When it watches.** Only while a session is running, Excuse included. It is off in a Pause and while Blind
  (the attention windows freeze, not reset), outside a session and after time's up. It runs on each new frame
  once, so a video freeze sends nothing and the state holds. After Mac sleep the windows start over and the open
  episode closes, quietly (`reset_attention`).
- **Head moves.** Each frame is flagged when the head owner says the head is moving; such frames count for
  nothing in either rule set. While a sweep runs, a sure `person` in `sweep.found_frames` frames in a row stops it.
- **Out.** Attention events go on the bus, in the session log and as a terminal line, with the Attention State
  transitions and each Distraction Episode's open and close. Nothing here decides what wakes the agent (the
  conductor does). Framing moves go to the head owner, which may decline them while another move plays. Once a
  second, a detection summary goes to the log: what later tuning of every threshold reads.
- **The Dashboard's camera.** Each new frame goes to the camera feed (`sonny.dashboard.camera`): a detected frame
  with its Detections and the Attention State it left; when not watching, the raw frame alone. The detector never
  runs just for the page.
"""

import dataclasses
import math
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np

from sonny.attention import AttentionRules, AttentionState, Detections, Episode
from sonny.config import Config
from sonny.detector import Detector, load_detector
from sonny.events import Event, EventBus
from sonny.framing import Framing, Reframe
from sonny.session_log import SessionLog


class Frames(Protocol):
    """The media hub's video side (`sonny.media.MediaHub`)."""

    frames: int

    def latest_frame(self) -> tuple[np.ndarray | None, float]: ...


class Head(Protocol):
    """The head owner's framing side (`sonny.head.HeadOwner`)."""

    @property
    def moving(self) -> bool: ...
    @property
    def sweeping(self) -> bool: ...
    def reframe(self, yaw_deg: float) -> bool: ...
    def sweep(self) -> bool: ...
    def sweep_found(self) -> None: ...
    def stop_framing(self) -> None: ...


class Camera(Protocol):
    """The Dashboard's camera feed (`sonny.dashboard.camera.CameraFeed`)."""

    def offer(
        self,
        image: np.ndarray,
        t: float,
        detections: Detections | None = None,
        attention: AttentionState | None = None,
    ) -> None: ...


class SessionState(Protocol):
    """The session clock as the worker reads it (`sonny.focus.clock.FocusClock`)."""

    @property
    def number(self) -> int | None: ...
    @property
    def running(self) -> bool: ...
    @property
    def paused(self) -> bool: ...
    @property
    def excused(self) -> bool: ...


class VisionWorker:
    def __init__(
        self,
        cfg: Config,
        frames: Frames,
        head: Head,
        session: SessionState,
        bus: EventBus,
        log: SessionLog,
        detector: Detector | None = None,
        clock: Callable[[], float] = time.time,
        camera: Camera | None = None,
    ) -> None:
        self.cfg = cfg
        self._frames, self._head, self._session, self._bus, self._log = frames, head, session, bus, log
        self.detector = detector  # `load()` loads it when not given
        self._clock = clock  # wall time, like the attention rules and `Event.t`
        self._camera = camera
        self._offered_t = 0.0  # arrival time of the last frame offered to the camera feed
        self._number: int | None = None  # the session the rules below belong to
        self._rules: AttentionRules | None = None
        self._framing: Framing | None = None
        self._summary: _Summary | None = None
        self._paused = self._excused = self._watching = False
        self._blind = self._rules_blind = False  # Blind as the conductor says, and as the rules have it
        self._reset = False  # the conductor asked for fresh windows (Mac sleep)
        self._frame_t = 0.0  # arrival time of the last frame detected
        self._finding = 0  # frames in a row with a person sure enough to stop a sweep
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # the rules: this thread runs them, the conductor reports Nudges

    def load(self) -> bool:
        """Load the detector (exported on the first run), before `ready`. Says whether Sonny can watch."""
        try:
            self.detector = load_detector(self.cfg.vision)
        except Exception as e:
            print(f"vision: RF-DETR didn't load ({type(e).__name__}: {e}); Sonny can't watch", flush=True)
            return False
        print(f"vision: RF-DETR Nano on {self.detector.name}", flush=True)
        return True

    def start(self, stop: threading.Event) -> None:
        """Watch until `stop`; without a loaded detector there is nothing to watch with."""
        if self.detector is None:
            return
        self._thread = threading.Thread(target=self._run, args=(stop,), name="sonny-vision", daemon=True)
        self._thread.start()

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                if not self.step():
                    stop.wait(self.cfg.media.reader_idle_sleep_s)
            except Exception as e:
                print(f"vision: {type(e).__name__}: {e}", flush=True)
                stop.wait(1)

    def step(self) -> bool:
        """Follow the session, then detect the newest frame if it is new and the session is watched. Says
        whether a frame was detected."""
        self._follow_session()
        watching = self._rules is not None and self._session.running and not self._paused and not self._blind
        if self._watching and not watching:
            self._head.stop_framing()  # a Pause: a sweep goes back to the framing pose
        self._watching = watching
        frame, t = self._frames.latest_frame()
        if not watching:
            if self._camera is not None and frame is not None and t != self._offered_t:
                self._offered_t = t
                self._camera.offer(frame, t)  # the page still sees the room; the detector stays off
            return False
        if frame is None or t == self._frame_t:
            return False
        self._frame_t = t
        assert self.detector is not None and self._rules and self._framing and self._summary
        moving = self._head.moving  # asked before detecting: the frame came in already
        started = time.perf_counter()
        d = dataclasses.replace(self.detector.detect(frame), head_moving=moving)
        detect_ms = (time.perf_counter() - started) * 1000
        self._summary.add(d, detect_ms)
        sweep = self.cfg.sweep
        if self._head.sweeping and d.person is not None and d.person.conf >= sweep.found_conf:
            self._finding += 1
            if self._finding >= sweep.found_frames:
                self._head.sweep_found()
        else:
            self._finding = 0
        self._apply(lambda rules: rules.observe(d))
        if self._camera is not None:
            self._offered_t = t
            self._camera.offer(frame, t, d, self._rules.state)  # the state this frame left the rules in
        move = self._framing.observe(d)
        if move is not None:
            started_move = self._head.reframe(move.yaw_deg) if isinstance(move, Reframe) else self._head.sweep()
            if not started_move:
                self._framing.declined(move)
        return True

    def _follow_session(self) -> None:
        """A new session gets new rules; Pause and Excuse changes reach the open session's rules."""
        number = self._session.number
        if number != self._number:
            self._number = number
            self._rules = self._framing = self._summary = None
            self._paused = self._excused = self._rules_blind = self._reset = False
            if number is not None:
                self._rules = AttentionRules(self.cfg, self._clock)
                self._framing = Framing(self.cfg, self._clock)
                self._summary = _Summary(self.cfg, self._frames, self._clock, self._write)
        if self._rules is None:
            return
        if (paused := self._session.paused) != self._paused:
            self._paused = paused
            self._rules.set_paused(paused)
        if (excused := self._session.excused) != self._excused:
            self._excused = excused
            if excused:
                self._rules.start_excuse()
            else:
                self._apply(lambda rules: rules.end_excuse())
        if (blind := self._blind) != self._rules_blind:
            self._rules_blind = blind
            self._rules.set_blind(blind)
        if self._reset:
            self._reset = False
            self._start_over()

    def set_blind(self, blind: bool) -> None:
        """Blind: frames are ignored and the windows freeze until it ends. Thread-safe (the next step applies it)."""
        self._blind = blind

    def reset_attention(self) -> None:
        """The Mac woke from sleep: the windows start over and the open episode closes with no event. Thread-safe."""
        self._reset = True

    def _start_over(self) -> None:
        rules = self._rules
        assert rules is not None
        with self._lock:
            episode, state = rules.episode, rules.state
            rules.reset()
        self._framing = Framing(self.cfg, self._clock)
        if episode is not None:
            self._write("episode_closed", **_episode_fields(episode), reason="mac_sleep")
        if rules.state != state:
            self._write("attention", state=rules.state, was=state)

    def record_nudge(self, episode: int, intensity: str) -> None:
        """A Nudge Sonny gave in `episode`, for the rules' copy of it (the log's episode record). Thread-safe."""
        with self._lock:
            rules = self._rules
            if rules is not None and rules.episode is not None and rules.episode.number == episode:
                rules.record_nudge(intensity)

    def _apply(self, change: Callable[[AttentionRules], list[Event]]) -> None:
        """Run a change on the rules; publish its events and log them with the episode and state changes."""
        rules = self._rules
        assert rules is not None
        with self._lock:
            episode, state = rules.episode, rules.state
            events = change(rules)
        if episode is not None and episode.closed_at is not None:
            self._write("episode_closed", **_episode_fields(episode))
        if rules.episode is not None and rules.episode is not episode:
            self._write("episode_opened", number=rules.episode.number, kind=rules.episode.kind)
        for event in events:
            self._bus.publish(event)
            (self._log.session or self._log.idle).write_event(event)
            excused = " (excused)" if event.data.get("excused") else ""
            print(f"attention: {str(event.type).replace('.', ' ')}{excused}", flush=True)
        if rules.state != state:
            self._write("attention", state=rules.state, was=state)

    def _write(self, type: str, **fields) -> None:
        (self._log.session or self._log.idle).write(type, **fields)


def _episode_fields(e: Episode) -> dict:
    assert e.closed_at is not None
    return {
        "number": e.number,
        "kind": e.kind,
        "seconds": round(e.closed_at - e.started_at, 1),
        "included_away": e.included_away,
        "nudges": e.nudges,
        "away_nudges": e.away_nudges,
    }


class _Summary:
    """The once-a-second detection summary (spec §10). A second is closed by the first frame after it."""

    def __init__(self, cfg: Config, frames: Frames, clock: Callable[[], float], write: Callable[..., None]) -> None:
        self._every, self._frames, self._clock, self._write = cfg.vision.summary_every_s, frames, clock, write
        self._start: float | None = None
        self._received = frames.frames

    def add(self, d: Detections, detect_ms: float) -> None:
        now = self._clock()
        if self._start is None or now - self._start >= self._every:
            if self._start is not None:
                self._flush()
            self._start, self._rows = now, []
        self._rows.append((d, detect_ms))

    def _flush(self) -> None:
        rows = self._rows
        phones = [(d.phone, d.person, d.frame_width) for d, _ in rows if d.phone is not None]
        best = max(phones, key=lambda p: p[0].conf, default=None)
        motion = None
        if phones:
            xs, ys = [p.centre[0] for p, _, _ in phones], [p.centre[1] for p, _, _ in phones]
            motion = round(math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / phones[0][2], 4)
        persons = [d.person.conf for d, _ in rows if d.person is not None]
        xs = [d.person.centre[0] / d.frame_width for d, _ in rows if d.person is not None]
        received, self._received = self._frames.frames - self._received, self._frames.frames
        self._write(
            "detections",
            phone_conf=round(best[0].conf, 3) if best else None,
            phone_inside=round(best[0].share_inside(best[1]), 3) if best and best[1] else None,
            phone_motion=motion,  # spread of the phone box centre, share of the frame width
            person_conf=round(max(persons), 3) if persons else None,
            person_x=round(sum(xs) / len(xs), 3) if xs else None,  # mean box centre, share of the frame width
            head_moving=any(d.head_moving for d, _ in rows),
            frames=received,
            detected=len(rows),
            detect_ms=round(sum(ms for _, ms in rows) / len(rows), 1),
        )
