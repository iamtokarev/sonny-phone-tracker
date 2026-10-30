"""The media hub: the only code that touches `reachy_mini.media` (spec §2 "Components").

`get_frame()` keeps only the newest frame and `get_audio_sample()` removes the chunk it
returns, so one reader per stream serves every consumer: the video reader keeps the latest
frame, the audio reader fans each mono chunk out to per-consumer queues.
"""

import queue
import threading
import time
from typing import Any

import numpy as np

from sonny.config import Media, Startup


class MediaHub:
    def __init__(self, mini: Any, media: Media, startup: Startup) -> None:
        self.mini = mini
        self.cfg = media
        self.startup = startup
        self.frames = 0
        self.audio_chunks = 0
        self._frame: np.ndarray | None = None
        self._frame_t = 0.0
        self._subs: list[queue.Queue[np.ndarray]] = []
        self.dropped: list[int] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def subscribe_audio(self) -> queue.Queue[np.ndarray]:
        """A queue receiving every mono float32 chunk; when full, new chunks are dropped and counted."""
        q: queue.Queue[np.ndarray] = queue.Queue(maxsize=self.cfg.audio_queue_max)
        with self._lock:
            self._subs.append(q)
            self.dropped.append(0)
        return q

    def latest_frame(self) -> tuple[np.ndarray | None, float]:
        """The newest frame and its arrival time (epoch s), or (None, 0.0) before the first."""
        with self._lock:
            return self._frame, self._frame_t

    @property
    def input_sample_rate(self) -> int:
        return self.cfg.audio_sample_rate  # start() refuses a mic at any other rate

    @property
    def output_sample_rate(self) -> int:
        return self.mini.media.get_output_audio_samplerate()

    def play(self, samples: np.ndarray) -> None:
        """Queue float32 mono samples on Sonny's speaker. Returns at once: pace the calls in real time."""
        self.mini.media.push_audio_sample(samples)

    def clear_speaker(self) -> None:
        """Drop audio already queued on Sonny's speaker (barge-in)."""
        self.mini.media.audio.clear_player()

    def start(self) -> None:
        rate = self.mini.media.get_input_audio_samplerate()
        if rate != self.cfg.audio_sample_rate:
            raise RuntimeError(f"Sonny's mic runs at {rate} Hz; expected {self.cfg.audio_sample_rate} Hz")
        self.mini.media.start_recording()
        self.mini.media.start_playing()
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._read_video, name="sonny-video", daemon=True),
            threading.Thread(target=self._read_audio, name="sonny-audio", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        """Stop and join the readers. Must run before `ReachyMini` closes, or they log
        "Camera is not initialized" in a loop."""
        self._stop.set()
        for t in self._threads:
            t.join(self.cfg.reader_join_timeout_s)

    def wait_speaker_ready(self, stop_event: threading.Event) -> bool:
        """Block until the WebRTC speaker send chain is up (audio pushed before it is dropped
        silently). False on timeout or stop. Backends without a send chain are ready at once."""
        audio = self.mini.media.audio
        deadline = time.monotonic() + self.startup.speaker_wait_timeout_s
        while hasattr(audio, "_appsrc") and audio._appsrc is None:
            if stop_event.is_set() or time.monotonic() >= deadline:
                return False
            stop_event.wait(self.startup.speaker_poll_s)
        return not stop_event.is_set()

    def _read_video(self) -> None:
        while not self._stop.is_set():
            frame = self.mini.media.get_frame()
            if frame is None:
                time.sleep(self.cfg.reader_idle_sleep_s)
                continue
            with self._lock:
                self._frame, self._frame_t = frame, time.time()
                self.frames += 1

    def _read_audio(self) -> None:
        while not self._stop.is_set():
            chunk = self.mini.media.get_audio_sample()
            if chunk is None:
                time.sleep(self.cfg.reader_idle_sleep_s)
                continue
            mono = np.ascontiguousarray(chunk[:, 0] if chunk.ndim == 2 else chunk, dtype=np.float32)  # ch0 == ch1
            with self._lock:
                self.audio_chunks += 1
                subs = list(enumerate(self._subs))
            for i, q in subs:
                try:
                    q.put_nowait(mono)
                except queue.Full:
                    self.dropped[i] += 1
