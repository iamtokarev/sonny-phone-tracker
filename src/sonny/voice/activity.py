"""When the agent is busy with a wake-up (spec §7 "One wake-up at a time"), read from the frames after the LLM.

A wake-up starts when the conductor delivers one or an owner turn reaches the LLM (`begin`), and lasts
while an LLM response streams, while tool calls run and the call after their results is due, and while
Sonny speaks the line. Events wait for all of it to finish.
"""

import time
from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class AgentActivity:
    def __init__(self, stuck_after_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._stuck_after_s, self._clock = stuck_after_s, clock
        self._reset()

    def _reset(self) -> None:
        self._call_due = False  # a call to the LLM is on its way
        self._responding = False
        self._tool_calls = 0
        self._line_due = False  # text went to TTS and Sonny hasn't finished saying it
        self._speaking = False
        self._changed = self._clock()

    @property
    def busy(self) -> bool:
        if self._speaking:
            return True  # Sonny always says when it stops
        busy = self._call_due or self._responding or self._tool_calls > 0 or self._line_due
        if busy and self._clock() - self._changed > self._stuck_after_s:
            self._reset()  # a frame we waited for never came (a line TTS never played); don't hold events
            return False
        return busy

    def begin(self) -> None:
        self._call_due = True
        self._changed = self._clock()

    def observe(self, frame: Frame) -> None:
        if isinstance(frame, LLMFullResponseStartFrame):
            self._call_due, self._responding = False, True
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._responding = False
        elif isinstance(frame, LLMTextFrame):
            if frame.text.strip():
                self._line_due = True
        elif isinstance(frame, FunctionCallsStartedFrame):
            self._tool_calls += len(frame.function_calls)
        elif isinstance(frame, FunctionCallResultFrame | FunctionCallCancelFrame):
            self._tool_calls = max(0, self._tool_calls - 1)
            runs_llm = frame.run_llm is not False if isinstance(frame, FunctionCallResultFrame) else frame.run_llm
            if self._tool_calls == 0 and runs_llm:
                self._call_due = True  # the aggregator sends the results back to the LLM
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = self._line_due = False
        elif isinstance(frame, InterruptionFrame):
            self._reset()  # a barge-in: the owner's turn takes over and begins its own wake-up
            return
        else:
            return
        self._changed = self._clock()


class ActivityTap(FrameProcessor):
    """Put right after the LLM: feeds every frame passing in either direction to `activity`."""

    def __init__(self, activity: AgentActivity) -> None:
        super().__init__()
        self._activity = activity

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self._activity.observe(frame)
        await self.push_frame(frame, direction)
