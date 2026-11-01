"""The voice pipeline (spec §8): Reachy mic → STT → Wake Word turns → LLM → Pocket TTS → Reachy speaker,
with the conductor feeding session events to the same LLM (spec §7).

An idle conversation is a short-lived context: a wake with no window open starts a fresh one. A Focus
Session keeps one context. Every wake, wake-up, LLM call, tool call and spoken line goes to the log (spec §10).
"""

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from itertools import takewhile

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext, LLMStandardMessage
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from sonny.config import Config
from sonny.events import Event, EventBus, EventType
from sonny.focus.clock import FocusClock
from sonny.focus.conductor import Conductor, wakeup_payload
from sonny.focus.tools import SessionTools
from sonny.media import MediaHub
from sonny.session_log import SessionLog
from sonny.voice.activity import ActivityTap, AgentActivity
from sonny.voice.llm import CappedOpenRouterLLM, LlmCall
from sonny.voice.models import ParakeetSTT, load_wake_scorer, pocket_tts
from sonny.voice.prompt import agent_prompt
from sonny.voice.transport import ReachyTransport
from sonny.voice.turns import OwnerTurns, WakeSeen, WakeWordTurnStart


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


class Conversation:
    """The agent's context and the log records of the voice loop.

    Owner turns and conductor events reach the context as JSON wake-ups (`Conductor`). The context of an
    idle conversation is dropped when it ends: a wake that finds no window open, no reply on its way, and
    nothing said for `keep_s` starts a fresh one. (The window decides what Sonny listens to; `keep_s` how
    long it remembers, so a wake just after a silent "stop" still continues the conversation.) A Focus
    Session keeps one context from the conversation that started it until the session closes. A failed
    wake-up is cut from the context at the next owner turn, after the assistant aggregator has added
    whatever partial reply it got (spec §7); a failed owner turn also asks for the confused move.
    """

    def __init__(
        self,
        log: SessionLog,
        bus: EventBus,
        conductor: Conductor,
        activity: AgentActivity,
        session: Callable[[], int | None],
        tools: SessionTools | None = None,
        *,
        keep_s: float,
        clock: Callable[[], float] = time.monotonic,
        on_failed_owner_turn: Callable[[], object],
    ) -> None:
        self.context = LLMContext(tools=tools.tools) if tools is not None else LLMContext()
        self._tools = tools
        self._log, self._bus, self._conductor, self._activity = log, bus, conductor, activity
        self._session = session  # the open Focus Session's number, or None
        self._context_session: int | None = None  # the session this context belongs to
        self._keep_s, self._clock = keep_s, clock
        self._on_failed_owner_turn = on_failed_owner_turn  # spec §7: a confused move, no words
        self._last_said = -float("inf")  # clock time of the last turn, wake-up, LLM call or line
        self._replying = False  # a wake-up went to the LLM and Sonny hasn't finished answering it
        self._failed_from: int | None = None  # where a failed wake-up starts in the context
        self._cut_line: str | None = None  # the last line, cut off by a barge-in

    def _write(self, type: str, **fields) -> None:
        (self._log.session or self._log.idle).write(type, **fields)

    def _emit(self, event: Event) -> None:
        self._bus.publish(event)
        (self._log.session or self._log.idle).write_event(event)

    def on_wake(self, seen: WakeSeen) -> None:
        if seen.ignored:
            self._write(EventType.WAKE, score=seen.score, ignored="self_wake")
            return
        ended = self._clock() - self._last_said > self._keep_s
        if seen.new_conversation and ended and not self._replying and self._context_session is None:
            self.context.set_messages([])
            self._failed_from = None
            self._write("idle_conversation_started")
        self._emit(Event(EventType.WAKE, {"score": seen.score}))
        if seen.barge_in:
            self._write("barge_in")

    def on_bare_wake(self, text: str) -> None:
        self._write("bare_wake", text=text)

    def on_turn(self, text: str) -> None:
        """An owner turn is the last message: it becomes an `owner.spoke` wake-up."""
        if self._cut_line is not None:
            self._keep_cut_off_line(self._cut_line)
            self._cut_line = None
        if self._failed_from is not None:
            messages = list(self.context.get_messages())
            self.context.set_messages(messages[: self._failed_from] + messages[-1:])  # keep only the new turn
            self._failed_from = None
        self._emit(Event(EventType.OWNER_SPOKE, {"text": text}))
        message = self._conductor.owner_wakeup(text)
        self.context.set_messages([*list(self.context.get_messages())[:-1], message])
        self._begin(message)

    def deliver(self, message: LLMStandardMessage) -> None:
        """A conductor wake-up is about to be appended to the context."""
        if (wakeup_payload(message) or {}).get("event") == EventType.SESSION_STARTED:
            self._start_session_context()
        self._begin(message)

    def _begin(self, message: LLMStandardMessage) -> None:
        self._write("wakeup", payload=wakeup_payload(message))
        self._last_said = self._clock()
        if self._tools is not None:
            self._tools.begin_wakeup()
        self._replying = True
        self._activity.begin()

    def _start_session_context(self) -> None:
        """A session's context holds the conversation that started it. After a Task switch, that is only the
        turn that asked for it: the old session's history goes with the old session."""
        session = self._session()
        if self._context_session is not None and self._context_session != session:
            messages = list(self.context.get_messages())
            owner = [i for i, m in enumerate(messages) if _is_owner_turn(m)]
            self.context.set_messages(messages[owner[-1] :] if owner else [])
            self._failed_from = None
        self._context_session = session

    def on_idle(self) -> None:
        """No wake-up is running. A context whose session has closed is dropped. (After a Task switch a new
        session is open; its `session.started` trims the context instead.)"""
        if self._context_session is not None and self._session() is None:
            self.context.set_messages([])
            self._context_session = None
            self._failed_from = self._cut_line = None

    def on_llm_call(self, call: LlmCall) -> None:
        self._write("llm_call", **asdict(call))
        self._last_said = self._clock()
        if not call.ok or call.first_text_s is None:
            self._replying = False  # nothing will be said
        if not call.ok and call.error != "interrupted":
            messages = self.context.get_messages()
            turns = [i for i, m in enumerate(messages) if _is_turn(m)]
            self._failed_from = turns[-1] if turns else None
            if turns and _is_owner_turn(messages[turns[-1]]):
                self._on_failed_owner_turn()

    def on_line(self, line: SpokenLine) -> None:
        self._write("line", **asdict(line))
        self._last_said = self._clock()
        self._replying = False
        if line.interrupted and line.text:
            self._cut_line = line.text

    def _keep_cut_off_line(self, text: str) -> None:
        """Make sure the agent sees the line the owner's new turn cut off, so it knows "stop" means stop
        talking. The assistant aggregator keeps only whole sentences already spoken (Pocket TTS sends
        text per sentence), and adds them after the line stops; so this runs at the next owner turn.
        """
        messages = list(self.context.get_messages())
        new_turn = len(messages) - 1  # the owner turn being reported is the last message
        replies = takewhile(lambda m: not _is_turn(m), reversed(messages[:new_turn]))
        if any(text.startswith(spoken) for m in replies if (spoken := _assistant_text(m))):
            return  # the aggregator kept what was spoken
        messages.insert(new_turn, {"role": "assistant", "content": text})
        self.context.set_messages(messages)


def _is_turn(message: object) -> bool:
    """A message that starts a wake-up: an owner turn or a conductor event."""
    return isinstance(message, dict) and message.get("role") == "user"


def _is_owner_turn(message: object) -> bool:
    return (wakeup_payload(message) or {}).get("event") == EventType.OWNER_SPOKE


def _assistant_text(message: object) -> str | None:
    if isinstance(message, dict) and message.get("role") == "assistant" and isinstance(message.get("content"), str):
        return message["content"]
    return None


class Conducting:
    """One conductor tick: read the clock, queue what it published, and hand over the next wake-up once
    the agent is free."""

    def __init__(
        self,
        clock: FocusClock,
        bus: EventBus,
        conductor: Conductor,
        conversation: Conversation,
        busy: Callable[[], bool],
        on_free: Callable[[], None] = lambda: None,
    ) -> None:
        self._clock, self._conductor, self._conversation, self._busy = clock, conductor, conversation, busy
        self._on_free = on_free  # nothing to deliver and the agent is free: the head may settle
        self._events = bus.subscribe()

    def step(self) -> LLMStandardMessage | None:
        """The wake-up message to append to the context now, if any."""
        self._clock.tick()
        while not self._events.empty():
            self._conductor.offer(self._events.get_nowait())
        if self._busy():
            return None
        message = self._conductor.next_wakeup()
        if message is None:
            self._conversation.on_idle()  # only now: a session's last wake-up still needs its context
            self._on_free()
        else:
            self._conversation.deliver(message)
        return message


class VoicePipeline:
    """Loads the models and builds the pipeline; `run()` holds conversations and Focus Sessions until stopped."""

    def __init__(self, cfg: Config, hub: MediaHub, log: SessionLog, bus: EventBus, api_key: str) -> None:
        self.cfg = cfg
        self.clock = FocusClock(cfg, log, bus)
        conductor = Conductor(self.clock.snapshot)
        tools = SessionTools(cfg, self.clock, lambda type, **f: (log.session or log.idle).write(type, **f))
        self.activity = AgentActivity(cfg.conductor.stuck_after_s)
        self.conversation = Conversation(
            log,
            bus,
            conductor,
            self.activity,
            lambda: self.clock.number,
            tools=tools,
            keep_s=cfg.idle.conversation_keep_s,
        )
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
            cfg.llm, api_key=api_key, system_instruction=agent_prompt(cfg), on_call=self.conversation.on_llm_call
        )
        for name in tools.names:
            llm.register_function(name, tools.handle)
        self.conducting = Conducting(
            self.clock,
            bus,
            conductor,
            self.conversation,
            busy=lambda: self.activity.busy or self.wake.in_turn or self.wake.bot_speaking,
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
                ActivityTap(self.activity),
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

        async def conduct() -> None:
            while True:
                if (message := self.conducting.step()) is not None:
                    await self.worker.queue_frame(LLMMessagesAppendFrame([message], run_llm=True))
                await asyncio.sleep(self.cfg.conductor.tick_s)

        await runner.add_workers(self.worker)
        tasks = [asyncio.create_task(watch()), asyncio.create_task(conduct())]
        try:
            await runner.run()
        finally:
            for task in tasks:
                task.cancel()
