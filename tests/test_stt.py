"""Speech outside an owner turn never becomes text (spec §8 "Owner turns").

Only a segment that a turn was open for is transcribed, so talk to someone else stays audio on the Mac.
Segmented STT transcribes a whole VAD segment, so "blah blah blah, Hey Sonny" said in one breath
would carry the "blah" along: a wake makes the STT drop what it buffered before the phrase.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence

import numpy as np
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineParams
from pipecat.services.settings import STTSettings
from pipecat.tests.utils import SleepFrame, run_test

from sonny.config import load_config
from sonny.voice.models import TurnGatedSTT
from sonny.voice.turns import WakeHeardFrame

CFG = load_config()
RATE = CFG.media.audio_sample_rate
CHATTER, WAKE_PHRASE = 1000, 2000  # sample values standing in for what was said


class SampleValuesSTT(TurnGatedSTT):
    """Transcribes a segment as the distinct sample values it holds."""

    def __init__(self) -> None:
        super().__init__(CFG.wake, settings=STTSettings(model="fake", language="en"))

    @property
    def wants_wav_segments(self) -> bool:
        return False

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        values = sorted(set(np.frombuffer(audio, np.int16).tolist()) - {0})
        yield TranscriptionFrame(" ".join(map(str, values)), "owner", "")


def speech(value: int, seconds: float) -> list[InputAudioRawFrame]:
    chunk = np.full(RATE // 50, value, np.int16).tobytes()  # 20 ms, as the media hub sends
    return [InputAudioRawFrame(audio=chunk, sample_rate=RATE, num_channels=1) for _ in range(round(seconds * 50))]


def transcribe(frames: Sequence[Frame]) -> list[str]:
    down, _ = asyncio.run(
        run_test(
            SampleValuesSTT(),
            frames_to_send=frames,
            pipeline_params=PipelineParams(audio_in_sample_rate=RATE),
        )
    )
    return [f.text for f in down if isinstance(f, TranscriptionFrame)]


def test_a_wake_drops_the_speech_before_the_wake_phrase():
    frames = [
        VADUserStartedSpeakingFrame(),
        *speech(CHATTER, 3),
        *speech(WAKE_PHRASE, CFG.wake.keep_before_s),
        SleepFrame(sleep=0.2),  # the wake is scored downstream, after the STT has buffered this audio
        WakeHeardFrame(),
        UserStartedSpeakingFrame(),  # the wake started the turn
        SleepFrame(sleep=0.2),
        VADUserStoppedSpeakingFrame(),
    ]
    assert transcribe(frames) == [str(WAKE_PHRASE)]


def test_speech_with_no_turn_open_is_never_transcribed():
    frames = [VADUserStartedSpeakingFrame(), *speech(CHATTER, 2), SleepFrame(sleep=0.2), VADUserStoppedSpeakingFrame()]
    assert transcribe(frames) == []


def test_a_segment_that_ended_before_the_turn_started_stays_out_of_it():
    frames = [
        VADUserStartedSpeakingFrame(),
        *speech(CHATTER, 1),
        SleepFrame(sleep=0.2),
        VADUserStoppedSpeakingFrame(),  # still being transcribed when ...
        UserStartedSpeakingFrame(),  # ... a turn starts
        VADUserStartedSpeakingFrame(),
        *speech(WAKE_PHRASE, 1),
        SleepFrame(sleep=0.2),
        VADUserStoppedSpeakingFrame(),
        UserStoppedSpeakingFrame(),
    ]
    assert transcribe(frames) == [str(WAKE_PHRASE)]


def test_a_follow_up_turn_is_transcribed_whole():
    frames = [
        VADUserStartedSpeakingFrame(),
        UserStartedSpeakingFrame(),  # VAD inside the window started the turn
        *speech(CHATTER, 1),
        *speech(WAKE_PHRASE, 1),
        SleepFrame(sleep=0.2),
        VADUserStoppedSpeakingFrame(),
    ]
    assert transcribe(frames) == [f"{CHATTER} {WAKE_PHRASE}"]
