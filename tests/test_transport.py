"""The Reachy transport's output: paced playback and barge-in (spec §8 "Transport", "Barge-in")."""

import asyncio
import time

import numpy as np
from pipecat.frames.frames import InterruptionFrame, OutputAudioRawFrame

from sonny.config import load_config
from sonny.media import MediaHub
from sonny.voice.transport import ReachyOutput
from tests.fakes import FakeMini

CFG = load_config()
RATE = 16000


def speech(seconds: float) -> OutputAudioRawFrame:
    pcm = np.full(round(seconds * RATE), 1000, np.int16)
    return OutputAudioRawFrame(audio=pcm.tobytes(), sample_rate=RATE, num_channels=1)


def output() -> tuple[ReachyOutput, FakeMini]:
    mini = FakeMini()
    return ReachyOutput(MediaHub(mini, CFG.media, CFG.startup), CFG.tts), mini


def test_playback_is_paced_with_a_small_lead():
    out, mini = output()

    async def play() -> float:
        t0 = time.monotonic()
        for _ in range(10):
            await out.write_audio_frame(speech(0.04))
        return time.monotonic() - t0

    took = asyncio.run(play())
    assert len(mini.media.played) == 10
    assert 0.4 - CFG.tts.queue_ahead_s - 0.03 <= took <= 0.4


def test_barge_in_clears_the_speaker_mid_line():
    out, mini = output()

    async def barge_in() -> None:
        await out.write_audio_frame(speech(0.5))
        await out.handle_interruption(InterruptionFrame())

    asyncio.run(barge_in())
    assert mini.media.audio.clears == 1


def test_playback_after_a_barge_in_starts_fresh():
    out, mini = output()

    async def barge_in_then_reply() -> float:
        await out.write_audio_frame(speech(0.5))
        await out.handle_interruption(InterruptionFrame())
        t0 = time.monotonic()
        await out.write_audio_frame(speech(0.3))
        return time.monotonic() - t0

    assert asyncio.run(barge_in_then_reply()) <= 0.3  # not queued behind the cleared line
    assert len(mini.media.played) == 2
