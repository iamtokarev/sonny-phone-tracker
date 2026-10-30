"""The voice pipeline in idle (spec §8): Reachy mic → STT → Wake Word turns → LLM → Pocket TTS → Reachy speaker.

An idle conversation is a short-lived context: a wake with no window open starts a fresh one. Every
wake, turn, LLM call and spoken line goes to the log (spec §10).
"""

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from sonny.config import Config
from sonny.events import Event, EventBus, EventType
from sonny.media import MediaHub
from sonny.session_log import SessionLog
from sonny.voice.llm import CappedOpenRouterLLM, LlmCall
from sonny.voice.models import ParakeetSTT, load_wake_scorer, pocket_tts
from sonny.voice.prompt import IDLE_PROMPT
from sonny.voice.transport import ReachyTransport
from sonny.voice.turns import OwnerTurns, WakeSeen, WakeWordTurnStart, owner_text


@dataclass(frozen=True)
class SpokenLine:
    text: str
    tts_start: float  # epoch s, Sonny's first audio
    tts_end: float
    interrupted: bool


class LineTracker(FrameProcessor):
    """Sits between the LLM and TTS: knows the line Sonny is saying (for the self-wake guard) and
    reports each line when Sonny stops speaking."""

    def __init__(self, on_line: Callable[[SpokenLine], None]) -> None:
        super().__init__()
        self._on_line = on_line
        self._parts: list[str] = []
        self._start: float | None = None
        self._interrupted = False

    @property
    def text(self) -> str:
        return "".join(self._parts)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._parts = []
        elif isinstance(frame, LLMTextFrame):
            self._parts.append(frame.text)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._start, self._interrupted = time.time(), False
        elif isinstance(frame, InterruptionFrame):
            self._interrupted = self._start is not None
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._start is not None:
            self._on_line(SpokenLine(self.text.strip(), self._start, time.time(), self._interrupted))
            self._start = None
        await self.push_frame(frame, direction)


class IdleConversation:
    """The idle context and the log records of the voice loop.

    The context is dropped when a conversation ends: the next wake that finds no window open and no
    reply on its way starts a fresh one. A failed turn is cut from the context at the next owner turn,
    after the assistant aggregator has added whatever partial reply it got (spec §7).
    """

    def __init__(self, log: SessionLog, bus: EventBus) -> None:
        self.context = LLMContext()
        self._log, self._bus = log, bus
        self._replying = False  # an owner turn went to the LLM and Sonny hasn't finished answering it
        self._failed_from: int | None = None  # where a failed turn starts in the context

    def _write(self, type: str, **fields) -> None:
        (self._log.session or self._log.idle).write(type, **fields)

    def _emit(self, event: Event) -> None:
        self._bus.publish(event)
        (self._log.session or self._log.idle).write_event(event)

    def on_wake(self, seen: WakeSeen) -> None:
        if seen.ignored:
            self._write(EventType.WAKE, score=seen.score, ignored="self_wake")
            return
        if seen.new_conversation and not self._replying:
            self.context.set_messages([])
            self._failed_from = None
            self._write("idle_conversation_started")
        self._emit(Event(EventType.WAKE, {"score": seen.score}))
        if seen.barge_in:
            self._write("barge_in")

    def on_bare_wake(self, text: str) -> None:
        self._write("bare_wake", text=text)

    def on_turn(self, text: str) -> None:
        if self._failed_from is not None:
            messages = list(self.context.get_messages())
            self.context.set_messages(messages[: self._failed_from] + messages[-1:])  # keep only the new turn
            self._failed_from = None
        self._replying = True
        self._emit(Event(EventType.OWNER_SPOKE, {"text": text}))

    def on_llm_call(self, call: LlmCall) -> None:
        self._write("llm_call", **asdict(call))
        if not call.ok or call.first_text_s is None:
            self._replying = False  # nothing will be said
        if not call.ok and call.error != "interrupted":
            owner = [i for i, m in enumerate(self.context.get_messages()) if owner_text(m) is not None]
            self._failed_from = owner[-1] if owner else None

    def on_line(self, line: SpokenLine) -> None:
        self._write("line", **asdict(line))
        self._replying = False
        if line.interrupted and line.text:
            self._keep_cut_off_line(line.text)

    def _keep_cut_off_line(self, text: str) -> None:
        """A barge-in can leave the aggregator nothing spoken to add (Pocket TTS sends no word timing).
        Keep the line, so the agent knows the owner's next words cut it off ("stop" means stop talking)."""
        messages = self.context.get_messages()
        last = messages[-1] if messages else None
        if not (isinstance(last, dict) and last.get("role") == "assistant"):
            self.context.add_message({"role": "assistant", "content": text})


class VoicePipeline:
    """Loads the models and builds the pipeline; `run()` holds the idle conversation until stopped."""

    def __init__(self, cfg: Config, hub: MediaHub, log: SessionLog, bus: EventBus, api_key: str) -> None:
        self.cfg = cfg
        self.conversation = IdleConversation(log, bus)
        rate = cfg.media.audio_sample_rate
        line = LineTracker(self.conversation.on_line)
        self.wake = WakeWordTurnStart(
            cfg.wake, rate, load_wake_scorer(cfg.wake), lambda: line.text, self.conversation.on_wake
        )

        def bare_wake(text: str) -> None:
            self.conversation.on_bare_wake(text)
            self.wake.reopen_window()

        user, assistant = LLMContextAggregatorPair(
            self.conversation.context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(),
                user_turn_strategies=UserTurnStrategies(
                    start=[self.wake, VADUserTurnStartStrategy(enable_interruptions=False)],
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=cfg.vad.end_of_turn_s)],
                ),
                empty_user_turn=None,
            ),
        )
        llm = CappedOpenRouterLLM(
            cfg.llm, api_key=api_key, system_instruction=IDLE_PROMPT, on_call=self.conversation.on_llm_call
        )
        transport = ReachyTransport(hub, cfg.voice, cfg.tts)
        pipeline = Pipeline(
            [
                transport.input(),
                ParakeetSTT(cfg.stt, cfg.wake),
                user,
                OwnerTurns(
                    on_bare_wake=bare_wake, on_turn=self.conversation.on_turn, woken_turn=lambda: self.wake.woken_turn
                ),
                llm,
                line,
                pocket_tts(cfg.tts),
                transport.output(),
                assistant,
            ]
        )
        self.worker = PipelineWorker(
            pipeline,
            params=PipelineParams(audio_in_sample_rate=rate, audio_out_sample_rate=hub.output_sample_rate),
            idle_timeout_secs=None,
        )

    async def run(self, stop: threading.Event, on_ready: Callable[[], None]) -> None:
        runner = WorkerRunner(handle_sigint=False)

        @self.worker.event_handler("on_pipeline_started")
        async def _started(_worker, _frame) -> None:
            on_ready()

        async def watch() -> None:
            while not stop.is_set():
                await asyncio.sleep(self.cfg.voice.stop_poll_s)
            await runner.cancel(reason="quit")

        await runner.add_workers(self.worker)
        watcher = asyncio.create_task(watch())
        try:
            await runner.run()
        finally:
            watcher.cancel()
