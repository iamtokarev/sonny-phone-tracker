"""Moves and speech in call order (spec §5 "Moves and speech never overlap"), between the LLM and TTS.

`play_emotion` grants a move and sends a `MoveCue` down the frame stream behind whatever the agent already
said in that reply. Here, in stream order:
- a cue hands the move to the head owner, which plays it once the line forwarded before the cue has been
  spoken (Sonny stopped speaking, or a barge-in);
- a line that comes after a cue waits here while the move is granted, waiting or playing.

A barge-in cancels a move that hasn't started. Waiting blocks only data frames; Pipecat cancels the wait
on an interruption and drops what was held.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pipecat.frames.frames import BotStoppedSpeakingFrame, DataFrame, Frame, InterruptionFrame, LLMTextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class MoveCue(DataFrame):
    """A granted move's place among the agent's lines."""

    move: str = ""


class Head(Protocol):
    @property
    def move_active(self) -> bool: ...
    def cue(self, name: str, after_line: Callable[[], bool], source: str = "agent") -> None: ...
    def cancel_pending(self) -> None: ...


class MoveGate(FrameProcessor):
    def __init__(self, head: Head, poll_s: float) -> None:
        super().__init__()
        self._head, self._poll_s = head, poll_s
        self._unspoken = False  # text went on to TTS and Sonny hasn't finished saying it

    def line_pending(self) -> bool:
        return self._unspoken

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, MoveCue):
            self._head.cue(frame.move, self.line_pending)
            return
        if isinstance(frame, InterruptionFrame):
            self._unspoken = False
            self._head.cancel_pending()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._unspoken = False
        elif isinstance(frame, LLMTextFrame) and direction is FrameDirection.DOWNSTREAM:
            while self._head.move_active:
                await asyncio.sleep(self._poll_s)
            if frame.text.strip():
                self._unspoken = True
        await self.push_frame(frame, direction)
