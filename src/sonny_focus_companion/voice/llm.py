"""The agent's LLM (spec §7 "Limits and guards in code"): OpenRouter with three layers of failure handling.

1. The OpenAI SDK retries errors itself (`max_retries`, with backoff).
2. Pipecat re-issues a request that gives no output within `retry_timeout_s` (`retry_on_timeout`).
3. The app abandons a wake-up still running at `wakeup_cap_s`. A wake-up with a tool call makes two calls
   (the call after the tool results continues it), so the cap spans both.

A call that still fails is reported through `on_call` and produces no text, so Sonny says nothing;
it never surfaces as a pipeline error. The app never switches model.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pipecat.frames.frames import Frame, FunctionCallCancelFrame, LLMContextFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openrouter.llm import OpenRouterLLMService

from sonny_focus_companion.config import Llm
from sonny_focus_companion.tracing import TRACE_KEY, AgentTracing, current_wakeup, wakeup_context


@dataclass(frozen=True)
class LlmCall:
    model: str
    ok: bool
    latency_s: float
    first_text_s: float | None  # None when no text came (silence, a tool call, or a failure)
    attempts: int  # HTTP requests sent: 1, plus each SDK retry and timeout re-issue
    error: str | None  # "cap", "interrupted", or the last error


class CappedOpenRouterLLM(OpenRouterLLMService):
    def __init__(
        self,
        cfg: Llm,
        *,
        api_key: str,
        system_instruction: str,
        on_call: Callable[[LlmCall], None],
        tracing: AgentTracing | None = None,
    ) -> None:
        self._cfg = cfg  # read by create_client(), which the base __init__ calls
        self._on_call = on_call
        self._tracing = tracing or AgentTracing()
        self._tool_traces = {}
        self._call_start: float | None = None
        self._wakeup_start = 0.0
        self._first_text_s: float | None = None
        self._attempts = 0
        super().__init__(
            api_key=api_key,
            settings=OpenRouterLLMService.Settings(
                model=cfg.model, system_instruction=system_instruction, temperature=cfg.temperature
            ),
            retry_on_timeout=True,
            retry_timeout_secs=cfg.retry_timeout_s,
        )

    def create_client(self, api_key=None, base_url=None, **kwargs) -> AsyncOpenAI:
        """An OpenAI client like Pipecat's, with the SDK retry count and a hook counting every request sent."""
        return self._tracing.wrap_client(
            AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=self._cfg.sdk_max_retries,
                http_client=DefaultAsyncHttpxClient(event_hooks={"request": [self._count_attempt]}),
            )
        )

    async def queue_frame(self, frame, direction=FrameDirection.DOWNSTREAM, callback=None):
        if isinstance(frame, LLMContextFrame):
            frame.metadata.setdefault(TRACE_KEY, self._tracing.for_messages(frame.context.get_messages()))
        await super().queue_frame(frame, direction, callback)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        trace = frame.metadata.get(TRACE_KEY)
        with wakeup_context(trace):
            await super().process_frame(frame, direction)

    async def run_function_calls(self, function_calls):
        trace = current_wakeup()
        for call in function_calls:
            self._tool_traces[call.tool_call_id] = trace
            if trace is not None:
                trace.retain()
        try:
            await super().run_function_calls(function_calls)
        except BaseException:
            for call in function_calls:
                if (pending := self._tool_traces.pop(call.tool_call_id, None)) is not None:
                    pending.release()
            raise

    async def _run_function_call(self, runner_item):
        trace = self._tool_traces.pop(runner_item.tool_call_id, None)
        try:
            with trace.operation() if trace else wakeup_context(None):
                await super()._run_function_call(runner_item)
        finally:
            if trace is not None:
                trace.release()

    async def _count_attempt(self, _request) -> None:
        self._attempts += 1

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        trace = current_wakeup()
        frame.metadata.setdefault(TRACE_KEY, trace)
        if (
            isinstance(frame, FunctionCallCancelFrame)
            and (pending := self._tool_traces.pop(frame.tool_call_id, None)) is not None
        ):
            frame.metadata[TRACE_KEY] = pending
            pending.release()
        if isinstance(frame, LLMTextFrame) and trace is not None:
            trace.text(frame.text)
        if isinstance(frame, LLMTextFrame) and self._call_start is not None and self._first_text_s is None:
            self._first_text_s = time.monotonic() - self._call_start
        await super().push_frame(frame, direction)

    async def _process_context(self, context: LLMContext) -> None:
        trace = current_wakeup()
        with trace.operation() if trace else nullcontext():
            await self._capped_context(context)

    async def _capped_context(self, context: LLMContext) -> None:
        self._call_start, self._first_text_s, self._attempts = time.monotonic(), None, 0
        if not _after_tool_results(context):
            self._wakeup_start = self._call_start
        cap_s = self._cfg.wakeup_cap_s - (self._call_start - self._wakeup_start)
        error: str | None = None
        try:
            await asyncio.wait_for(super()._process_context(context), max(cap_s, 0))
        except TimeoutError as e:
            hit_cap = time.monotonic() - self._call_start >= cap_s
            error = "cap" if hit_cap else f"{type(e).__name__}: {e}"
        except asyncio.CancelledError:
            error = "interrupted"
            raise
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            call = LlmCall(
                model=self._cfg.model,
                ok=error is None,
                latency_s=time.monotonic() - self._call_start,
                first_text_s=self._first_text_s,
                attempts=self._attempts,
                error=error,
            )
            if (trace := current_wakeup()) is not None:
                trace.call(asdict(call))
            self._on_call(call)
            self._call_start = None


def _after_tool_results(context: LLMContext) -> bool:
    """The call continues a wake-up: tool results came after its last user message (an owner turn or an
    event). A line said with the tool calls may come after them, once it has been spoken."""
    for message in reversed(context.get_messages()):
        role = message.get("role") if isinstance(message, dict) else None
        if role == "tool":
            return True
        if role == "user":
            return False
    return False
