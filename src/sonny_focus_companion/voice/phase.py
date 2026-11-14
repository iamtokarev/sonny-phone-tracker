"""The agent's phase for the Dashboard (Dashboard spec §6): idle, listening, thinking or speaking.

A Pipecat observer on the voice pipeline reads frames the pipeline already sees, in its own task, so it adds
nothing to the path from the owner's last word to Sonny's voice:

- an owner turn starts (the Wake Word, or VAD inside the follow-up window) → listening;
- a wake-up begins (`begin`: the owner's turn reached the LLM, or the conductor delivered an event) → thinking;
  so does a new LLM response when Sonny is idle (the call after tool results);
- `BotStartedSpeaking` → speaking; `BotStoppedSpeaking` → idle;
- an interruption → idle, unless it is the barge-in of a turn that just started.

Not every turn reaches the LLM ("Hey Sonny" alone, a turn with no words) and not every response is spoken, so
the conductor `settle`s the phase whenever the agent is free: listening while the wake window is open, else
idle. Each change goes to `on_phase` once.
"""

from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    UserStartedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from sonny_focus_companion.dashboard.protocol import AgentPhase


class AgentPhases(BaseObserver):
    def __init__(self, on_phase: Callable[[AgentPhase], None]) -> None:
        super().__init__(observe_every_push=False)  # a frame's first push is the moment it stands for
        self._on_phase = on_phase
        self.phase: AgentPhase = "idle"

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, UserStartedSpeakingFrame):
            self._set("listening")
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._set("speaking")
        elif isinstance(frame, LLMFullResponseStartFrame) and self.phase == "idle":
            self._set("thinking")
        elif (isinstance(frame, BotStoppedSpeakingFrame) and self.phase == "speaking") or (
            isinstance(frame, InterruptionFrame) and self.phase != "listening"
        ):
            self._set("idle")

    def begin(self) -> None:
        """A wake-up began: the owner's turn or a conductor event went to the LLM."""
        self._set("thinking")

    def settle(self, window_open: bool) -> None:
        """The agent is free (no turn, no wake-up, Sonny silent)."""
        self._set("listening" if window_open and self.phase == "listening" else "idle")

    def _set(self, phase: AgentPhase) -> None:
        if phase != self.phase:
            self.phase = phase
            self._on_phase(phase)
