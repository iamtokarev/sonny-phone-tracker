"""In-memory stand-ins for ReachyMini and its media, so tests never touch a robot."""

import threading
import time

import numpy as np


class FakeAudio:
    def __init__(self) -> None:
        self._appsrc = None  # the WebRTC speaker send chain; set once it is up


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
