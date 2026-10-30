"""The app-level cap on each LLM call (spec §7 "Limits and guards in code"), against a hung fake client."""

import asyncio
import dataclasses
import time

from pipecat.frames.frames import ErrorFrame, LLMContextFrame, LLMFullResponseEndFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from sonny.config import load_config
from sonny.voice.llm import CappedOpenRouterLLM, LlmCall

CFG = load_config()
CAP_S = 0.3


class HungCompletions:
    """An OpenRouter that accepts the request and never answers."""

    def __init__(self) -> None:
        self.requests = 0

    async def create(self, **_params):
        self.requests += 1
        await asyncio.Event().wait()


class HungClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": HungCompletions()})()


def test_a_hung_call_is_abandoned_at_the_cap_and_logged():
    calls: list[LlmCall] = []
    cfg = dataclasses.replace(CFG.llm, wakeup_cap_s=CAP_S)
    llm = CappedOpenRouterLLM(cfg, api_key="test-key", system_instruction="Say hi.", on_call=calls.append)
    llm._client = HungClient()  # the OpenAI SDK client: the network boundary

    t0 = time.monotonic()
    down, up = asyncio.run(
        run_test(llm, frames_to_send=[LLMContextFrame(LLMContext([{"role": "user", "content": "how are you"}]))])
    )
    took = time.monotonic() - t0

    assert CAP_S <= took < CAP_S + 1
    assert [c.ok for c in calls] == [False]
    assert calls[0].model == CFG.llm.model and calls[0].error == "cap"
    assert CAP_S <= calls[0].latency_s < CAP_S + 0.2
    assert not any(isinstance(f, LLMTextFrame) for f in down)  # nothing to speak
    assert any(isinstance(f, LLMFullResponseEndFrame) for f in down)
    assert not any(isinstance(f, ErrorFrame) for f in up)  # a failed turn is not a pipeline error


def test_the_sdk_retries_twice_on_errors():
    llm = CappedOpenRouterLLM(CFG.llm, api_key="test-key", system_instruction="Say hi.", on_call=lambda _: None)
    assert llm._client.max_retries == CFG.llm.sdk_max_retries == 2
