"""Turn rules (spec §8 "Owner turns", "Barge-in"), driven with fake audio and transcripts.

The fake wake scorer reads a frame's amplitude as its wake score, so a test "says" the Wake Word by
sending one 80 ms frame at full scale times the score it wants.
"""

import asyncio

import numpy as np
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMContextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test
from pipecat.turns.types import ProcessFrameResult

from sonny.config import load_config
from sonny.voice.turns import OwnerTurns, WakeSeen, WakeWordTurnStart, is_bare_wake

CFG = load_config()
FRAME = round(CFG.wake.frame_s * CFG.media.audio_sample_rate)


def amplitude_score(pcm: np.ndarray) -> float:
    return float(np.abs(pcm).max()) / 32767


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Line:
    """What Sonny is saying now, as the line tracker reports it."""

    text = ""

    def __call__(self) -> str:
        return self.text


class Harness:
    def __init__(self) -> None:
        self.clock, self.line = Clock(), Line()
        self.wakes: list[tuple[float, bool]] = []  # (score, new_conversation) of each wake that counted
        self.turns: list[bool] = []  # enable_interruptions of each turn start
        self.strategy = WakeWordTurnStart(
            CFG.wake, CFG.media.audio_sample_rate, amplitude_score, self.line, self._on_wake, clock=self.clock
        )

        @self.strategy.event_handler("on_user_turn_started")
        async def _(_strategy, params):
            self.turns.append(params.enable_interruptions)
            await self.strategy.handle_user_turn_started()

    def _on_wake(self, seen: WakeSeen) -> None:
        if not seen.ignored:
            self.wakes.append((seen.score, seen.new_conversation))

    def send(self, frame) -> ProcessFrameResult:
        return asyncio.run(self.strategy.process_frame(frame))

    def audio(self, score: float) -> ProcessFrameResult:
        pcm = np.full(FRAME, round(score * 32767), np.int16)
        return self.send(
            InputAudioRawFrame(audio=pcm.tobytes(), sample_rate=CFG.media.audio_sample_rate, num_channels=1)
        )

    def wake(self) -> ProcessFrameResult:
        return self.audio(0.95)

    def vad_start(self) -> ProcessFrameResult:
        return self.send(VADUserStartedSpeakingFrame())

    def end_turn(self) -> None:
        asyncio.run(self.strategy.handle_user_turn_stopped())

    def later(self, seconds: float) -> None:
        self.clock.t += seconds


@pytest.fixture
def h() -> Harness:
    return Harness()


def test_speech_without_a_wake_never_starts_a_turn(h):
    assert h.audio(0.3) is ProcessFrameResult.CONTINUE
    assert h.vad_start() is ProcessFrameResult.STOP
    assert h.turns == [] and h.wakes == []


def test_a_wake_starts_a_turn_and_opens_the_follow_up_window(h):
    assert h.wake() is ProcessFrameResult.STOP
    assert h.turns == [True]
    assert [(round(score, 2), new) for score, new in h.wakes] == [(0.95, True)]
    h.end_turn()
    h.later(CFG.wake.window_s - 0.5)
    assert h.vad_start() is ProcessFrameResult.CONTINUE


def test_the_window_closes_after_its_seconds(h):
    h.wake()
    h.end_turn()
    h.later(CFG.wake.window_s + 0.1)
    assert h.vad_start() is ProcessFrameResult.STOP


def test_the_window_reopens_after_every_line_from_sonny(h):
    h.wake()
    h.end_turn()
    h.later(CFG.wake.window_s + 1)
    h.send(BotStartedSpeakingFrame())
    assert h.vad_start() is ProcessFrameResult.STOP  # never while Sonny speaks
    h.later(10)
    h.send(BotStoppedSpeakingFrame())
    h.later(CFG.wake.window_s - 0.5)
    assert h.vad_start() is ProcessFrameResult.CONTINUE
    h.later(1)
    assert h.vad_start() is ProcessFrameResult.STOP


def test_dropping_a_bare_wake_keeps_the_window_open_for_the_command(h):
    h.wake()
    h.end_turn()
    h.later(CFG.wake.window_s - 1)
    h.strategy.reopen_window()  # the turn filter dropped "Hey Sonny." alone
    h.later(CFG.wake.window_s - 0.5)
    assert h.vad_start() is ProcessFrameResult.CONTINUE


def test_a_wake_while_sonny_speaks_barges_in(h):
    h.send(BotStartedSpeakingFrame())
    h.line.text = "Here's one: why did the scarecrow win an award?"
    assert h.wake() is ProcessFrameResult.STOP
    assert h.turns == [True]  # enable_interruptions: the aggregator interrupts, the output clears the speaker
    assert h.strategy.window_open


def test_self_wake_guard_ignores_a_wake_while_sonny_says_its_name(h):
    h.send(BotStartedSpeakingFrame())
    h.line.text = "Hi, I'm Sonny, your focus buddy."
    assert h.wake() is ProcessFrameResult.CONTINUE
    assert h.turns == []
    assert not h.strategy.window_open
    h.send(BotStoppedSpeakingFrame())
    h.later(CFG.wake.refractory_s)
    assert h.wake() is ProcessFrameResult.STOP  # the owner's own wake after the line still counts


def test_one_utterance_of_the_wake_phrase_is_one_wake(h):
    h.wake()
    h.later(CFG.wake.frame_s)
    h.wake()
    assert len(h.wakes) == 1


def test_a_wake_inside_an_open_window_continues_the_conversation(h):
    h.wake()
    h.end_turn()
    h.later(CFG.wake.refractory_s)
    h.wake()
    assert [new for _, new in h.wakes] == [True, False]


def test_transcripts_outside_a_turn_are_blanked(h):
    echo = TranscriptionFrame("why did the scarecrow", "owner", "")
    assert h.send(echo) is ProcessFrameResult.STOP
    assert echo.text == ""
    h.wake()
    said = TranscriptionFrame("how are you", "owner", "")
    assert h.send(said) is ProcessFrameResult.CONTINUE
    assert said.text == "how are you"


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("Hey Sonny.", True),
        ("hey, sunny", True),
        ("Um, hey Sonny?", True),
        ("Hey Sonny, how are you?", False),
        ("How are you?", False),
        ("yeah", False),
    ],
)
def test_bare_wake_is_the_wake_phrase_with_filler_only(text, bare):
    assert is_bare_wake(text) is bare


class Turns:
    def __init__(self) -> None:
        self.bare: list[str] = []
        self.said: list[str] = []
        self.filter = OwnerTurns(on_bare_wake=self.bare.append, on_turn=self.said.append)

    def finish(self, context: LLMContext) -> list:
        down, _ = asyncio.run(run_test(self.filter, frames_to_send=[LLMContextFrame(context)]))
        return [f for f in down if isinstance(f, LLMContextFrame)]


def test_a_bare_wake_turn_is_dropped_and_never_reaches_the_llm():
    turns, context = Turns(), LLMContext([{"role": "user", "content": "Hey Sonny."}])
    assert turns.finish(context) == []
    assert turns.bare == ["Hey Sonny."] and turns.said == []
    assert context.get_messages() == []


def test_the_command_after_a_bare_wake_is_one_turn():
    turns = Turns()
    context = LLMContext([{"role": "user", "content": "Hey Sonny."}, {"role": "user", "content": "How are you?"}])
    assert len(turns.finish(context)) == 1
    assert turns.said == ["How are you?"]
    assert context.get_messages() == [{"role": "user", "content": "How are you?"}]


def test_a_wake_and_command_in_one_breath_pass_as_said():
    turns, context = Turns(), LLMContext([{"role": "user", "content": "Hey Sonny, how are you?"}])
    assert len(turns.finish(context)) == 1
    assert turns.said == ["Hey Sonny, how are you?"] and turns.bare == []
