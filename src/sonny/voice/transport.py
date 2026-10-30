"""A Pipecat transport over the media hub (spec §8 "Transport").

Input: the hub's 16 kHz mono mic chunks as `InputAudioRawFrame`s. Output: TTS audio to Sonny's
speaker, paced in real time because `push_audio_sample()` doesn't block, with a small lead queued
ahead. An interruption clears what the speaker still holds: only a wake interrupts (ADR 0004), and
`clear_player()` on Sonny is cheap (~44 ms), so it runs on every one, as in the ticket-16 runs.
"""

import asyncio
import queue
import threading
import time

import numpy as np
from pipecat.frames.frames import Frame, InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

from sonny.config import Tts, Voice
from sonny.media import MediaHub


class ReachyInput(BaseInputTransport):
    def __init__(self, hub: MediaHub, cfg: Voice, params: TransportParams) -> None:
        super().__init__(params)
        self.hub, self.cfg = hub, cfg
        self._mic = hub.subscribe_audio()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, args=(loop,), name="sonny-voice-in", daemon=True)
        self._thread.start()

    async def stop(self, frame) -> None:
        await self._stop_pump()
        await super().stop(frame)

    async def cancel(self, frame) -> None:
        await self._stop_pump()
        await super().cancel(frame)

    async def _stop_pump(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, self.hub.cfg.reader_join_timeout_s)

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        rate = self.hub.input_sample_rate
        while not self._stop.is_set():
            try:
                chunk = self._mic.get(timeout=self.cfg.input_poll_s)
            except queue.Empty:
                continue
            pcm = (np.clip(chunk, -1, 1) * 32767).astype(np.int16).tobytes()
            push = self.push_audio_frame(InputAudioRawFrame(audio=pcm, sample_rate=rate, num_channels=1))
            try:
                asyncio.run_coroutine_threadsafe(push, loop)
            except RuntimeError:  # the loop closed under us: the pipeline is gone
                push.close()
                return


class ReachyOutput(BaseOutputTransport):
    def __init__(self, hub: MediaHub, cfg: Tts, params: TransportParams | None = None) -> None:
        super().__init__(params or TransportParams(audio_out_enabled=True))
        self.hub, self.cfg = hub, cfg
        self._play_until = 0.0  # when the audio already handed to the speaker runs out

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterruptionFrame):
            await self.handle_interruption(frame)
        await super().process_frame(frame, direction)

    async def handle_interruption(self, _frame: InterruptionFrame) -> None:
        """Clear the speaker. Our pacing can't tell whether Sonny's WebRTC player still holds audio."""
        self.hub.clear_speaker()
        self._play_until = 0.0

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        samples = np.frombuffer(frame.audio, np.int16).astype(np.float32) / 32768
        self._play_until = max(self._play_until, time.monotonic())
        self.hub.play(samples)
        self._play_until += len(samples) / frame.sample_rate
        await asyncio.sleep(max(0.0, self._play_until - self.cfg.queue_ahead_s - time.monotonic()))
        return True


class ReachyTransport(BaseTransport):
    def __init__(self, hub: MediaHub, voice: Voice, tts: Tts) -> None:
        super().__init__()
        params = TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=hub.input_sample_rate,
            audio_out_enabled=True,
            audio_out_sample_rate=hub.output_sample_rate,
        )
        self._in, self._out = ReachyInput(hub, voice, params), ReachyOutput(hub, tts, params)

    def input(self) -> ReachyInput:
        return self._in

    def output(self) -> ReachyOutput:
        return self._out
