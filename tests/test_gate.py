"""Moves and speech in call order (spec §5): the line gate between the LLM and TTS."""

import asyncio
import threading

from pipecat.frames.frames import BotStoppedSpeakingFrame, Frame, InterruptionFrame, LLMTextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import SleepFrame, run_test

from sonny.voice.gate import MoveCue, MoveGate


class FakeHead:
    def __init__(self) -> None:
        self.move_cued = False
        self.cues: list[tuple[str, object, bool]] = []  # name, after_line, after_line() at cue time
        self.cancels = 0

    def cue(self, name, after_line, source="agent") -> None:
        self.cues.append((name, after_line, after_line()))

    def cancel_pending(self) -> None:
        self.cancels += 1


class Recorder(FrameProcessor):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame | MoveCue):
            self.events.append(f"{type(frame).__name__}: {getattr(frame, 'text', getattr(frame, 'move', ''))}")
        await self.push_frame(frame, direction)


def run(gate: MoveGate, frames: list[Frame], events: list[str]) -> None:
    asyncio.run(run_test(Pipeline([gate, Recorder(events)]), frames_to_send=frames))


def test_a_line_queued_during_a_move_plays_after_it_ends():
    head, events = FakeHead(), []
    head.move_cued = True

    def move_ends() -> None:
        events.append("move ended")
        head.move_cued = False

    threading.Timer(0.3, move_ends).start()
    run(MoveGate(head, poll_s=0.01), [LLMTextFrame("Okay, take five."), SleepFrame(0.5)], events)
    assert events == ["move ended", "LLMTextFrame: Okay, take five."]


def test_a_line_with_no_move_running_passes_at_once():
    head, events = FakeHead(), []
    run(MoveGate(head, poll_s=0.01), [LLMTextFrame("Hi.")], events)
    assert events == ["LLMTextFrame: Hi."]


def test_a_cue_waits_for_the_line_said_before_it_and_goes_no_further():
    head, events = FakeHead(), []
    gate = MoveGate(head, poll_s=0.01)
    run(gate, [LLMTextFrame("Time's up!"), MoveCue("proud3"), SleepFrame(0.1), BotStoppedSpeakingFrame()], events)
    (name, after_line, waiting) = head.cues[0]
    assert name == "proud3" and waiting is True  # the line before it wasn't spoken yet
    assert after_line() is False  # Sonny has stopped speaking
    assert events == ["LLMTextFrame: Time's up!"]


def test_a_cue_with_no_line_before_it_plays_at_once():
    head, events = FakeHead(), []
    run(MoveGate(head, poll_s=0.01), [MoveCue("yes1")], events)
    assert head.cues[0][2] is False


def test_a_barge_in_cancels_a_move_not_yet_played():
    head, events = FakeHead(), []
    run(MoveGate(head, poll_s=0.01), [LLMTextFrame("A long story"), InterruptionFrame()], events)
    assert head.cancels == 1
