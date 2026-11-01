"""The head owner (spec §3 "Head" column, §5): the only code that tracks, moves or poses Sonny's head and
antennas.

- **Head by mode.** Idle: tracking stopped, still at neutral. Idle conversation: daemon face tracking at
  `head.tracking_weight`, back to neutral once Sonny stops listening. Focus Session: tracking stopped before
  anything else, then the level framing pose. After a session: glide to idle, after its last line and move.
- **#1453.** While tracking is on, its weight goes to 0 before any app-driven motion: a target is ignored
  at weight 1 once a face has been lost.
- **Emotion moves** (`play_emotion`). One at a time: a request during another returns `busy` and nothing is
  queued; with the motors down it returns `unavailable`. Recorded emotions play through the daemon REST
  route with their sound; the four antenna gestures are goto sequences here. After every move, failed and
  interrupted ones too, the head returns to its mode's pose.
- **Framing** (in a Focus Session). A re-frame is one smooth yaw move from the current framing yaw; the search
  sweep steps the head target across the yaw range at `sweep.speed_dps` and stops where the vision worker says
  it saw someone, or returns to the framing pose. Both are head moves like any other: they start only when no
  move is playing and make `play_emotion` busy. Unlike emotion moves they are silent and may overlap speech.
  `framing.Framing` decides when; this module plays them.
- **Moving.** The head counts as moving through every head move and glide, and for `vision.settle_s` after:
  frames taken then show the motion, so the vision worker leaves them out.
- **Call order.** A granted move doesn't start at once: its cue comes down the frame stream after any line
  the agent said before calling it (`MoveGate`), and it waits for that line to be spoken. A line that comes
  after the cue waits for the move (`move_cued`).

All robot calls run on one worker thread, in order, except `enter_session`'s tracking stop, which must
happen before the session starts. Every move writes `move_start` and `move_end` to the log.
"""

import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sonny.config import Config


@dataclass(frozen=True)
class Move:
    intensity: str  # subtle, medium or strong: the agent's escalation scale, also logged
    meaning: str  # what the agent reads, for Focus Sessions


# The `play_emotion` enum (ticket 06 toolkit, each one watched on Sonny with its meaning).
MOVES: dict[str, Move] = {
    "perk": Move("subtle", "Antennas perk up: I noticed you. A silent first sign."),
    "twitch": Move("subtle", "One antenna flicks twice: psst, hey."),
    "inquiring2": Move("subtle", "A small questioning tilt: hmm, what are you doing?"),
    "attentive1": Move("subtle", "Leans in and watches: I'm here with you."),
    "understanding2": Move("subtle", "A small nod: good, okay."),
    "wiggle": Move("medium", "Antennas wiggle: playful, come on."),
    "impatient1": Move("medium", "Fidgets impatiently: let's get moving."),
    "impatient2": Move("medium", "Impatient, sceptical: you're stalling."),
    "displeased2": Move("medium", "A short headshake: I don't approve."),
    "droop": Move("medium", "Antennas droop slowly: disappointed."),
    "cheerful1": Move("medium", "A happy little whistle: glad you're back."),
    "yes1": Move("medium", "A long nod: yes, agreed. Granting a request."),
    "calming1": Move("medium", "Slow and soothing: it's okay, breathe."),
    "reprimand3": Move("strong", "A funny scold: oh come on, really?"),
    "reprimand1": Move("strong", "A firmer scold: hey, what are you doing?"),
    "irritated1": Move("strong", "A brief irritated huff: not this again."),
    "proud3": Move("strong", "Yes, we did it: celebrating a finished session."),
    "success2": Move("strong", "Celebration: good news, well done."),
}

# Antenna gestures, as validated on Sonny (ticket 06 toolkit; spec §5 keeps them in code): steps of
# ([right, left] in rad, goto duration s, hold s). 0 is upright; a droop is (-a, +a).
_UP = [0.0, 0.0]
GESTURES: dict[str, list[tuple[list[float], float, float]]] = {
    "perk": [(_UP, 0.4, 0.2), ([0.35, -0.35], 0.25, 1.0), (_UP, 0.6, 0.0)],
    "twitch": [([0.0, 0.7], 0.15, 0.05), (_UP, 0.15, 0.3), ([0.0, 0.7], 0.15, 0.05), (_UP, 0.2, 0.0)],
    "wiggle": [(a, 0.18, 0.0) for a in ([0.5, 0.5], [-0.5, -0.5]) * 3] + [(_UP, 0.3, 0.0)],
    "droop": [([-1.4, 1.4], 1.6, 2.0), (_UP, 1.0, 0.0)],
}

FAILED_TURN_MOVE = "inquiring2"  # a confused look for an owner turn whose LLM call failed (spec §7)


class Mode(StrEnum):
    IDLE = "idle"
    CONVERSATION = "conversation"
    SESSION = "session"
    ASLEEP = "asleep"


class Robot(Protocol):
    """Sonny's head as the head owner sees it: SDK calls and daemon REST routes (`sonny.robot`)."""

    def wake_up(self) -> None: ...
    def sleep(self) -> None: ...
    def start_tracking(self, weight: float) -> None: ...
    def stop_tracking(self) -> None: ...
    def goto(
        self, yaw_deg: float, pitch_deg: float, antennas: list[float], duration_s: float, free_body: bool = False
    ) -> None: ...
    def aim(self, yaw_deg: float, pitch_deg: float) -> None: ...  # a head target, at once (the sweep's steps)
    def goto_antennas(self, antennas: list[float], duration_s: float) -> None: ...
    def play_emotion(self, name: str) -> str: ...  # the daemon's move uuid
    def move_running(self, uuid: str) -> bool: ...
    def stop_move(self, uuid: str) -> None: ...
    def motors_up(self) -> bool: ...
    def volume(self) -> int: ...
    def set_volume(self, volume: int) -> None: ...


class HeadOwner:
    """Owns Sonny's head: the mode's pose and tracking, and one emotion move at a time."""

    def __init__(
        self,
        cfg: Config,
        robot: Robot,
        write: Callable[..., None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg, self._c = cfg, cfg.head
        self._robot, self._write, self._clock = robot, write, clock  # write(type, **fields): a log record
        self._mode = Mode.IDLE  # the pose the head should be in; the worker brings it there
        self._tracking: float | None = None  # the weight daemon tracking runs at, or None when stopped
        self._motors_ok = True
        self._reserved: tuple[str, float] | None = None  # a granted move waiting for its cue, and since when
        self._queued: str | None = None  # a cued move waiting for the line before it
        self._playing: str | None = None
        self._framing: str | None = None  # a re-frame or sweep that hasn't finished
        self._frame_yaw = 0.0  # the framing pose's yaw in this session
        self._found = threading.Event()  # the sweep running now saw someone
        self._unframe = threading.Event()  # a running sweep stops and goes back to the framing pose
        self._motion = 0  # head moves and glides running now
        self._still_since = -float("inf")  # when the last one ended
        self._lock = threading.Lock()  # move state
        self._robot_lock = threading.RLock()  # tracking changes from the worker and from `enter_session`
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="sonny-head", daemon=True)

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Startup: Sonny's volume (the emotions' sound plays at it), then the wake-up move, then idle."""
        if self._robot.volume() != self.cfg.robot.volume:
            self._robot.set_volume(self.cfg.robot.volume)  # the daemon plays a test sound on every set
        self._robot.wake_up()
        self._worker.start()

    def quit(self) -> None:
        """Spec §2 "Quit": interrupt a move (its restore still runs), stop tracking, then `goto_sleep()`."""
        self._stop.set()
        self._jobs.put(None)
        if self._worker.is_alive():
            self._worker.join(self._c.move_timeout_s)
        self._mode = Mode.ASLEEP
        try:
            with self._robot_lock:
                self._robot.stop_tracking()
                self._tracking = None
        finally:
            self._robot.sleep()

    def _run(self) -> None:
        """Runs the jobs in order; when idle, checks the motors every `motors_check_s`, so `play_emotion`
        answers `unavailable` without trying first."""
        while True:
            try:
                job = self._jobs.get(timeout=self._c.motors_check_s)
            except queue.Empty:
                job = self._motors_up
            if job is None:
                return
            try:
                job()
            except Exception as e:
                print(f"head: {e}", flush=True)

    # ---------------------------------------------------------------- modes

    @property
    def mode(self) -> Mode:
        return self._mode

    def converse(self) -> None:
        """A wake with no session: Sonny faces the owner."""
        if self._mode is Mode.IDLE:
            self._mode = Mode.CONVERSATION
            self._jobs.put(self._settle_job)

    def enter_session(self) -> None:
        """Called before a session starts: tracking stops here and now, so the session never uses a frame
        taken while the daemon tracks (it stalls Mac video), then the head glides to the framing pose."""
        with self._robot_lock:
            self._zero_tracking()
            self._robot.stop_tracking()
            self._tracking = None
            if self._mode is not Mode.SESSION:
                self._frame_yaw = 0.0
            self._mode = Mode.SESSION  # under the lock: a settle on the worker can't turn tracking back on
        self._jobs.put(self._settle_job)

    def settle(self, session_running: bool, listening: bool) -> None:
        """Called whenever the agent is free: leave a conversation or a session that is over. Queued moves
        and lines have played by now, so after time's up this is the glide to idle."""
        if session_running:
            if self._mode is not Mode.SESSION:
                self.enter_session()  # an extend after time's up
        elif self._mode is Mode.SESSION or (self._mode is Mode.CONVERSATION and not listening):
            self._mode = Mode.IDLE
            self._jobs.put(self._settle_job)

    def _settle_job(self) -> None:
        if not self._stop.is_set():  # quitting: the head goes to sleep instead
            self._settle_pose()

    def _settle_pose(self) -> None:
        """Bring the head to its mode's pose and tracking. Runs on the worker."""
        mode = self._mode
        if mode is Mode.ASLEEP:
            return
        rest = [-self._c.rest_antenna_rad, self._c.rest_antenna_rad]
        with self._robot_lock:
            if self._zero_tracking() and mode is not Mode.CONVERSATION:
                self._robot.stop_tracking()
                self._tracking = None
        with self._moving():
            if mode is Mode.SESSION:  # the body turns along, as in the framing moves
                self._robot.goto(self._frame_yaw, self.cfg.frame.pitch_deg, rest, self._c.glide_s, free_body=True)
            else:
                self._robot.goto(0.0, 0.0, rest, self._c.glide_s)
        with self._robot_lock:
            if mode is Mode.CONVERSATION and self._mode is Mode.CONVERSATION:  # not left meanwhile
                self._robot.start_tracking(self._c.tracking_weight)
                self._tracking = self._c.tracking_weight

    def _zero_tracking(self) -> bool:
        """#1453: while tracking runs, a head target is ignored at weight 1 once a face was lost, so the weight
        goes to 0 before any app-driven motion. Says whether tracking was on. Hold `_robot_lock`."""
        if self._tracking is None:
            return False
        self._robot.start_tracking(0.0)
        self._tracking = 0.0
        return True

    # ---------------------------------------------------------------- moves

    @property
    def move_active(self) -> bool:
        """A granted move hasn't finished: another is `busy`."""
        with self._lock:
            return self._move_active_locked()

    @property
    def move_cued(self) -> bool:
        """A move has its place in the call order and hasn't finished: lines that come after it wait."""
        with self._lock:
            return self._queued is not None or self._playing is not None

    @property
    def moving(self) -> bool:
        """The head is moving, or stopped less than `vision.settle_s` ago: a frame taken now may show the motion."""
        with self._lock:
            return self._motion > 0 or self._clock() - self._still_since < self.cfg.vision.settle_s

    @contextmanager
    def _moving(self) -> Iterator[None]:
        with self._lock:
            self._motion += 1
        try:
            yield
        finally:
            with self._lock:
                self._motion -= 1
                self._still_since = self._clock()

    def _move_active_locked(self) -> bool:
        granted = self._reserved is not None and self._clock() - self._reserved[1] <= self._c.cue_timeout_s
        busy = self._queued is not None or self._playing is not None or self._framing is not None
        return granted or busy  # a stale grant's cue never came

    def request(self, name: str) -> dict:
        """`play_emotion`: grant the move, or say why not. A granted move plays once its cue arrives."""
        if name not in MOVES:
            return {"error": f"no move {name!r}"}
        if not self._motors_ok:
            return {"unavailable": "Sonny's motors are down; speak instead"}
        with self._lock:
            if self._move_active_locked():
                return {"busy": "another head move is playing; nothing was queued"}
            self._reserved = (name, self._clock())
        return {"playing": name, "intensity": MOVES[name].intensity}

    def cue(self, name: str, after_line: Callable[[], bool], source: str = "agent") -> None:
        """The granted move's turn in the call order: it plays once `after_line()` says the line said
        before it has been spoken."""
        with self._lock:
            if self._reserved is None or self._reserved[0] != name:
                return
            self._reserved, self._queued = None, name
        self._jobs.put(lambda: self._play(name, after_line, source))

    def cancel_pending(self) -> None:
        """A barge-in: a move that hasn't started won't."""
        with self._lock:
            self._reserved = self._queued = None

    def play_now(self, name: str, after_line: Callable[[], bool], source: str) -> dict:
        """A move code asks for itself (not the agent): granted and cued at once."""
        result = self.request(name)
        if "playing" in result:
            self.cue(name, after_line, source)
        return result

    def _play(self, name: str, after_line: Callable[[], bool], source: str) -> None:
        deadline = self._clock() + self._c.line_wait_s
        while after_line() and self._clock() < deadline and not self._stop.wait(self._c.move_poll_s):
            pass
        with self._lock:
            if self._queued != name or self._stop.is_set():
                self._queued = None
                return  # cancelled by a barge-in, or the app is quitting
            self._queued, self._playing = None, name
        if not self._motors_up():
            with self._lock:
                self._playing = None
            self._write("move_skipped", name=name, reason="motors down")
            return
        start = self._clock()
        self._write("move_start", name=name, intensity=MOVES[name].intensity, source=source)
        error: str | None = None
        try:
            with self._moving(), self._robot_lock:
                self._zero_tracking()
            with self._moving():
                if name in GESTURES:
                    self._gesture(GESTURES[name])
                else:
                    self._emotion(name)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            try:
                self._settle_pose()
            except Exception as e:
                error = error or f"restore: {type(e).__name__}: {e}"
            with self._lock:
                self._playing = None
            self._write(
                "move_end",
                name=name,
                ok=error is None and not self._stop.is_set(),
                error=error,
                interrupted=self._stop.is_set(),
                seconds=round(self._clock() - start, 2),
            )

    def _gesture(self, steps: list[tuple[list[float], float, float]]) -> None:
        for antennas, duration_s, hold_s in steps:
            if self._stop.is_set():
                return
            self._robot.goto_antennas(antennas, duration_s)
            if hold_s:
                self._stop.wait(hold_s)

    def _emotion(self, name: str) -> None:
        uuid = self._robot.play_emotion(name)
        deadline = self._clock() + self._c.move_timeout_s
        while self._robot.move_running(uuid):
            if self._stop.wait(self._c.move_poll_s) or self._clock() >= deadline:
                self._robot.stop_move(uuid)
                if not self._stop.is_set():
                    raise TimeoutError(f"still running after {self._c.move_timeout_s:g} s")
                return

    # ---------------------------------------------------------------- framing

    def reframe(self, yaw_deg: float) -> bool:
        """Turn the framing pose by `yaw_deg`, in one smooth move. False while another move plays or outside
        a session: nothing is queued."""
        if not self._claim_framing("reframe"):
            return False
        self._jobs.put(lambda: self._framing_job(lambda: self._reframe(yaw_deg)))
        return True

    def sweep(self) -> bool:
        """One search sweep. False while another move plays or outside a session: nothing is queued."""
        if not self._claim_framing("sweep"):
            return False
        self._found.clear()
        self._unframe.clear()
        self._jobs.put(lambda: self._framing_job(self._sweep))
        return True

    @property
    def sweeping(self) -> bool:
        with self._lock:
            return self._framing == "sweep"

    def sweep_found(self) -> None:
        """The vision worker saw someone: a running sweep stops where it is, and that is the framing pose."""
        self._found.set()

    def stop_framing(self) -> None:
        """A Pause: a running sweep goes back to the framing pose, and the head holds still."""
        self._unframe.set()

    def _claim_framing(self, name: str) -> bool:
        with self._lock:
            if self._mode is not Mode.SESSION or self._move_active_locked():
                return False
            self._framing = name
            return True

    def _framing_job(self, move: Callable[[], None]) -> None:
        try:
            if self._mode is Mode.SESSION and not self._stop.is_set():  # not left while it waited
                move()
        finally:
            with self._lock:
                self._framing = None

    def _reframe(self, yaw_deg: float) -> None:
        r = self.cfg.reframe
        to = self._clamp_yaw(self._frame_yaw + yaw_deg)
        start, came_from = self._clock(), self._frame_yaw
        self._write("move_start", name="reframe", source="framing", yaw_from=round(came_from, 1), yaw_to=round(to, 1))
        error: str | None = None
        rest = [-self._c.rest_antenna_rad, self._c.rest_antenna_rad]
        try:
            with self._moving():
                duration = max(r.min_move_s, abs(to - came_from) / r.speed_dps)
                self._robot.goto(to, self.cfg.frame.pitch_deg, rest, duration, free_body=True)
            self._frame_yaw = to
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        self._write("move_end", name="reframe", ok=error is None, error=error, seconds=self._since(start))

    def _sweep(self) -> None:
        """Out to the nearer end of the yaw range and across to the other, one step per tick, then a glide back
        to the framing pose it started from. On `sweep_found` it stops where it is."""
        c = self.cfg.sweep
        start, home = self._clock(), self._frame_yaw
        first = c.yaw_range_deg if home >= 0 else -c.yaw_range_deg
        self._write("move_start", name="sweep", source="framing", yaw_from=round(home, 1))
        step, tick = c.speed_dps / c.step_hz, 1 / c.step_hz
        yaw, found, error = home, False, None
        try:
            with self._moving():
                for end in (first, -first):
                    while yaw != end and not found and not self._sweep_cut():
                        yaw = end if abs(end - yaw) <= step else yaw + step * (1 if end > yaw else -1)
                        self._robot.aim(yaw, self.cfg.frame.pitch_deg)
                        self._stop.wait(tick)
                        found = self._found.is_set()
            if found:
                self._frame_yaw = yaw
            elif self._mode is Mode.SESSION and not self._stop.is_set():
                self._settle_pose()  # nobody: back to the last framing pose
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        self._write(
            "move_end",
            name="sweep",
            ok=error is None,
            error=error,
            found=found,
            yaw=round(yaw, 1),
            seconds=self._since(start),
        )

    def _sweep_cut(self) -> bool:
        """Quit, a Pause, or the session is over."""
        return self._stop.is_set() or self._unframe.is_set() or self._mode is not Mode.SESSION

    def _clamp_yaw(self, yaw: float) -> float:
        limit = self.cfg.sweep.yaw_range_deg
        return max(-limit, min(limit, yaw))

    def _since(self, start: float) -> float:
        return round(self._clock() - start, 2)

    def _motors_up(self) -> bool:
        try:
            self._motors_ok = self._robot.motors_up()
        except Exception:
            self._motors_ok = False
        return self._motors_ok
