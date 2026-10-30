"""The agent's LLM (spec §7 "Limits and guards in code"): OpenRouter with three layers of failure handling.

1. The OpenAI SDK retries errors itself (`max_retries`, with backoff).
2. Pipecat re-issues a request that gives no output within `retry_timeout_s` (`retry_on_timeout`).
3. The app abandons any call still running at `wakeup_cap_s`.

A call that still fails is reported through `on_call` and produces no text, so Sonny says nothing;
it never surfaces as a pipeline error. The app never switches model.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pipecat.frames.frames import Frame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openrouter.llm import OpenRouterLLMService

from sonny.config import Llm


@dataclass(frozen=True)
class LlmCall:
    model: str
    ok: bool
    latency_s: float
    first_text_s: float | None  # None when no text came (silence, a tool call, or a failure)
    attempts: int  # HTTP requests sent: 1, plus each SDK retry and timeout re-issue
    error: str | None  # "cap", "interrupted", or the last error


class CappedOpenRouterLLM(OpenRouterLLMService):
    def __init__(self, cfg: Llm, *, api_key: str, system_instruction: str, on_call: Callable[[LlmCall], None]) -> None:
        self._cfg = cfg  # read by create_client(), which the base __init__ calls
        self._on_call = on_call
        self._call_start: float | None = None
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
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=self._cfg.sdk_max_retries,
            http_client=DefaultAsyncHttpxClient(event_hooks={"request": [self._count_attempt]}),
        )

    async def _count_attempt(self, _request) -> None:
        self._attempts += 1

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, LLMTextFrame) and self._call_start is not None and self._first_text_s is None:
            self._first_text_s = time.monotonic() - self._call_start
        await super().push_frame(frame, direction)

    async def _process_context(self, context: LLMContext) -> None:
        self._call_start, self._first_text_s, self._attempts = time.monotonic(), None, 0
        error: str | None = None
        try:
            await asyncio.wait_for(super()._process_context(context), self._cfg.wakeup_cap_s)
        except TimeoutError as e:
            hit_cap = time.monotonic() - self._call_start >= self._cfg.wakeup_cap_s
            error = "cap" if hit_cap else f"{type(e).__name__}: {e}"
        except asyncio.CancelledError:
            error = "interrupted"
            raise
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            self._on_call(
                LlmCall(
                    model=self._cfg.model,
                    ok=error is None,
                    latency_s=time.monotonic() - self._call_start,
                    first_text_s=self._first_text_s,
                    attempts=self._attempts,
                    error=error,
                )
            )
            self._call_start = None
