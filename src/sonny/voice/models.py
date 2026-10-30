"""The on-device voice models (spec §8): the Wake Word scorer, Parakeet STT and Pocket TTS.

Each loads at startup, before "ready". STT and TTS run on the Mac, so audio stays on it (spec §2).
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.pocket_tts.tts import PocketTTSService
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from sonny.config import Stt, Tts, Wake
from sonny.voice.turns import WakeHeardFrame


def load_wake_scorer(cfg: Wake) -> Callable[[np.ndarray], float]:
    """openWakeWord on the owner's "Hey Sonny" model, downloaded from the Hub at the pinned revision.
    The scorer takes one 80 ms int16 frame and returns its wake score."""
    import openwakeword
    from huggingface_hub import hf_hub_download
    from openwakeword.model import Model
    from openwakeword.utils import download_models

    resources = Path(openwakeword.__file__).parent / "resources" / "models"
    if not (resources / "melspectrogram.onnx").exists():
        download_models([])  # the shared feature models openWakeWord runs before any wake model
    path = hf_hub_download(cfg.model_repo, cfg.model_file, revision=cfg.model_revision)
    model = Model(wakeword_models=[path], inference_framework="onnx")

    def score(frame: np.ndarray) -> float:
        return float(next(iter(model.predict(frame).values())))

    return score


class WakeTrimmedSTT(SegmentedSTTService):
    """A segmented STT that, on a wake, keeps only the last `keep_before_s` of the segment so far: the
    wake phrase. Owner speech before the phrase (talk to someone else) never becomes text."""

    def __init__(self, wake: Wake, **kwargs) -> None:
        super().__init__(**kwargs)
        self._keep_before_s = wake.keep_before_s

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, WakeHeardFrame):
            keep = round(self._keep_before_s * self.sample_rate) * 2  # 16-bit mono
            del self._audio_buffer[: max(0, len(self._audio_buffer) - keep)]
            return
        await super().process_frame(frame, direction)


class ParakeetSTT(WakeTrimmedSTT):
    """Parakeet TDT on MLX as a segmented STT service: it gets each VAD segment as 16-bit PCM."""

    def __init__(self, cfg: Stt, wake: Wake, **kwargs) -> None:
        from parakeet_mlx import from_pretrained

        super().__init__(
            wake,
            settings=STTSettings(model=cfg.model, language=Language(cfg.language)),
            ttfs_p99_latency=cfg.ttfs_p99_s,
            **kwargs,
        )
        # MLX streams are per thread: load and run the model on one dedicated thread.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parakeet")
        self._model = self._pool.submit(from_pretrained, cfg.model).result()

    def can_generate_metrics(self) -> bool:
        return True

    async def cleanup(self) -> None:
        await super().cleanup()
        self._pool.shutdown(wait=False)

    @property
    def wants_wav_segments(self) -> bool:
        return False

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        try:
            await self.start_processing_metrics()
            pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            text = await asyncio.get_running_loop().run_in_executor(self._pool, self._transcribe, pcm)
            await self.stop_processing_metrics()
            if text:
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601())
        except Exception as e:
            yield ErrorFrame(error=f"Parakeet failed: {e}")

    def _transcribe(self, pcm: np.ndarray) -> str:
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        mel = get_logmel(mx.array(pcm, dtype=mx.float32), self._model.preprocessor_config)
        return self._model.generate(mel)[0].text.strip()


def pocket_tts(cfg: Tts) -> PocketTTSService:
    return PocketTTSService(settings=PocketTTSService.Settings(voice=cfg.voice))
