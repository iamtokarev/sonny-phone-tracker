"""In-memory stand-ins for ReachyMini and its media, so tests never touch a robot."""

import threading
import time

import numpy as np


class FakeAudio:
    def __init__(self) -> None:
        self._appsrc = None  # the WebRTC speaker send chain; set once it is up
        self.clears = 0

    def clear_player(self) -> None:
        self.clears += 1


class FakeMedia:
    """Serves `chunks` stereo audio chunks (ch0 = chunk index, ch1 = -1) and `frames` frames, then None.

    Audio is paced at one chunk per `pace_s` like a real mic, in compressed time."""

    def __init__(
        self, chunks: int = 0, frames: int = 0, chunk_len: int = 320, sample_rate: int = 16000, pace_s: float = 0.0002
    ) -> None:
        self.pace_s = pace_s
        self.audio = FakeAudio()
        self.sample_rate = sample_rate
        self._chunks = iter(
            np.stack([np.full(chunk_len, i, np.float32), np.full(chunk_len, -1, np.float32)], 1) for i in range(chunks)
        )
        self._frames = iter(np.full((2, 2, 3), i, np.uint8) for i in range(frames))
        self.calls: list[str] = []
        self.calls_after_stop = 0
        self.output_sample_rate = 16000
        self.played: list[np.ndarray] = []
        self.stopped = threading.Event()
        self._lock = threading.Lock()

    def _call(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
            if self.stopped.is_set():
                self.calls_after_stop += 1

    def start_recording(self) -> None:
        self._call("start_recording")

    def start_playing(self) -> None:
        self._call("start_playing")

    def get_input_audio_samplerate(self) -> int:
        return self.sample_rate

    def get_output_audio_samplerate(self) -> int:
        return self.output_sample_rate

    def push_audio_sample(self, samples) -> None:
        self.played.append(samples)

    def get_audio_sample(self):
        self._call("get_audio_sample")
        time.sleep(self.pace_s)
        return next(self._chunks, None)

    def get_frame(self):
        self._call("get_frame")
        return next(self._frames, None)


class FakeMini:
    def __init__(self, media: FakeMedia | None = None) -> None:
        self.media = media or FakeMedia()
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        if name in {"enable_motors", "wake_up", "stop_head_tracking", "goto_sleep"}:
            return lambda *a, **k: self.calls.append(name)
        raise AttributeError(name)


class FakeRobot:
    """Stands in for Sonny's head at the head owner's seam: the SDK calls and the daemon REST routes.

    A recorded emotion runs until `finish()`; `fail_play` makes the REST play raise; `motors` is what the
    daemon reports."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.running: set[str] = set()
        self.motors = True
        self.fail_play: Exception | None = None
        self.vol = 62
        self.played = threading.Event()  # set when an emotion starts playing on the daemon
        self._next = 0
        self._lock = threading.Lock()

    def _call(self, *call) -> None:
        with self._lock:
            self.calls.append(call)

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def finish(self) -> None:
        self.running.clear()

    def wake_up(self) -> None:
        self._call("wake_up")

    def sleep(self) -> None:
        self._call("sleep")

    def start_tracking(self, weight: float) -> None:
        self._call("start_tracking", weight)

    def stop_tracking(self) -> None:
        self._call("stop_tracking")

    def goto(self, yaw_deg: float, pitch_deg: float, antennas: list[float], duration_s: float) -> None:
        self._call("goto", yaw_deg, pitch_deg, antennas)

    def goto_antennas(self, antennas: list[float], duration_s: float) -> None:
        self._call("goto_antennas", antennas)

    def play_emotion(self, name: str) -> str:
        self._call("play_emotion", name)
        if self.fail_play is not None:
            raise self.fail_play
        self._next += 1
        uuid = f"move-{self._next}"
        self.running.add(uuid)
        self.played.set()
        return uuid

    def move_running(self, uuid: str) -> bool:
        return uuid in self.running

    def stop_move(self, uuid: str) -> None:
        self._call("stop_move", uuid)
        self.running.discard(uuid)

    def motors_up(self) -> bool:
        return self.motors

    def volume(self) -> int:
        return self.vol

    def set_volume(self, volume: int) -> None:
        self._call("set_volume", volume)
        self.vol = volume


class StubHead:
    """The head owner as the session tools see it: every move granted, nothing played."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def enter_session(self) -> None:
        self.calls.append("enter_session")

    def request(self, name: str) -> dict:
        from sonny.head import MOVES

        self.calls.append(name)
        return {"playing": name, "intensity": MOVES[name].intensity}
