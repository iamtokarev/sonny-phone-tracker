"""Owner turns (spec §8): the Wake Word opens a follow-up window, and only the window lets VAD start a turn.

- A wake opens the window, starts a turn and barges in on Sonny's speech (the user aggregator turns
  the interruption into `clear_player()` at the output).
- A VAD turn start passes only inside the window and never while Sonny speaks.
- After every line from Sonny the window reopens, so a reply needs no second wake.
- A wake is ignored while Sonny speaks a line containing "Sonny" (the self-wake guard).

Speech not meant for Sonny (talk to someone else) never reaches the LLM:
- the STT transcribes only segments an owner turn was open for (`TurnGatedSTT`);
- every turn start also resets the user aggregator, which collects transcripts before any strategy
  sees them;
- a wake sends `WakeHeardFrame` up to the STT, which drops the audio from before the wake phrase in the
  same breath;
- that misses a wake phrase that closes a breath (the segment is already transcribing), so a turn
  started by a wake is also cut, as text, at the wake phrase (`OwnerTurns`).

A finished turn that is only the wake phrase is dropped before the LLM (`OwnerTurns`).
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ControlFrame,
    Frame,
    InputAudioRawFrame,
    LLMContextFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import BaseUserTurnStartStrategy

from sonny.config import Wake

SONNY_NAME = re.compile(r"sonny", re.I)
# A turn that is only the wake phrase, maybe with filler: the owner paused after "Hey Sonny".
_WAKE_NAME = re.compile(r"sonn|sunn|sony|soni", re.I)
_BARE_WAKE = re.compile(r"^(?:\W|hey|hi|hay|okay|ok|so|yeah|yes|um+|uh+|ah|sonny|sunny|sony|soni|sonnie|son)*$", re.I)
_WAKE_PHRASE = re.compile(r"(?:\b(?:hey|hi|hay|okay|ok)\W+)?\b(?:sonn|sunn|sony|soni)\w*", re.I)


def is_bare_wake(text: str) -> bool:
    return bool(_BARE_WAKE.match(text) and _WAKE_NAME.search(text))


def from_wake_phrase(text: str) -> str:
    """The text from the first wake phrase on; all of it when the phrase was misheard past matching."""
    match = _WAKE_PHRASE.search(text)
    return text[match.start() :] if match else text


@dataclass
class WakeHeardFrame(ControlFrame):
    """Sent upstream on a wake: the STT drops the audio it holds from before the wake phrase."""


@dataclass(frozen=True)
class WakeSeen:
    score: float
    ignored: bool  # the self-wake guard held: Sonny was saying its own name
    barge_in: bool  # Sonny was speaking
    new_conversation: bool  # no window or turn was open


class WakeWordTurnStart(BaseUserTurnStartStrategy):
    """Put first in the user aggregator's start strategies, before a `VADUserTurnStartStrategy`."""

    def __init__(
        self,
        cfg: Wake,
        sample_rate: int,
        score: Callable[[np.ndarray], float],
        line_text: Callable[[], str],
        on_wake: Callable[[WakeSeen], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(enable_interruptions=True)
        self.cfg = cfg
        self._score, self._line_text, self._on_wake, self._clock = score, line_text, on_wake, clock
        self._frame_len = round(cfg.frame_s * sample_rate)  # samples per scored frame
        self._buf = np.zeros(0, np.int16)
        self._last_wake = -float("inf")
        self._open_until = -float("inf")
        self.bot_speaking = False
        self.in_turn = False
        self.woken_turn = False  # the current (or last) turn was started by a wake, not by VAD in the window

    @property
    def window_open(self) -> bool:
        return self._clock() < self._open_until

    def reopen_window(self) -> None:
        self._open_until = self._clock() + self.cfg.window_s

    async def handle_user_turn_started(self) -> None:
        self.in_turn = True
        await self.trigger_reset_aggregation()

    async def handle_user_turn_stopped(self) -> None:
        self.in_turn = False

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, InputAudioRawFrame):
            return await self._audio(frame)
        if isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.bot_speaking = False
            self.reopen_window()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            if self.bot_speaking or not self.window_open:
                return ProcessFrameResult.STOP
            if not self.in_turn:
                self.woken_turn = False
        return ProcessFrameResult.CONTINUE

    async def _audio(self, frame: InputAudioRawFrame) -> ProcessFrameResult:
        self._buf = np.concatenate([self._buf, np.frombuffer(frame.audio, np.int16)])
        result = ProcessFrameResult.CONTINUE
        while len(self._buf) >= self._frame_len:
            score = self._score(self._buf[: self._frame_len])
            self._buf = self._buf[self._frame_len :]
            now = self._clock()
            if score < self.cfg.threshold or now - self._last_wake < self.cfg.refractory_s:
                continue
            self._last_wake = now
            if self.bot_speaking and SONNY_NAME.search(self._line_text()):
                self._on_wake(WakeSeen(score, ignored=True, barge_in=False, new_conversation=False))
                continue
            new = not (self.bot_speaking or self.in_turn or self.window_open)
            self._on_wake(WakeSeen(score, ignored=False, barge_in=self.bot_speaking, new_conversation=new))
            self.reopen_window()
            await self.push_frame(WakeHeardFrame(), FrameDirection.UPSTREAM)
            if not self.in_turn:
                self.woken_turn = True
                await self.trigger_user_turn_started(enable_interruptions=True)
            result = ProcessFrameResult.STOP
        return result


class OwnerTurns(FrameProcessor):
    """Sits right after the user aggregator, which ends each finished turn's context with the owner's
    new message. Cuts a woken turn at the wake phrase and drops bare-wake messages from the context. A turn
    that is only the wake phrase goes no further: it never re-answers an earlier message."""

    def __init__(
        self, on_bare_wake: Callable[[str], None], on_turn: Callable[[str], None], woken_turn: Callable[[], bool]
    ) -> None:
        super().__init__()
        self._on_bare_wake, self._on_turn, self._woken_turn = on_bare_wake, on_turn, woken_turn

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        messages = list(frame.context.get_messages()) if isinstance(frame, LLMContextFrame) else []
        said = owner_text(messages[-1]) if messages else None
        if isinstance(frame, LLMContextFrame) and said is not None:
            if self._woken_turn():
                said = from_wake_phrase(said)
                messages[-1] = {"role": "user", "content": said}
            kept = [m for m in messages if not is_bare_wake(owner_text(m) or "")]
            for m in messages:
                if m not in kept:
                    self._on_bare_wake(owner_text(m) or "")
            frame.context.set_messages(kept)
            if is_bare_wake(said):
                return
            self._on_turn(said)
        await self.push_frame(frame, direction)


def owner_text(message: object) -> str | None:
    """The text of an owner message in an LLM context, or None for any other message."""
    if isinstance(message, dict) and message.get("role") == "user" and isinstance(message.get("content"), str):
        return message["content"]
    return None
