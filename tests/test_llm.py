"""The app-level cap on each LLM call (spec §7 "Limits and guards in code"), against a hung fake client."""

import asyncio
import dataclasses
import time

from pipecat.frames.frames import ErrorFrame, LLMContextFrame, LLMFullResponseEndFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import run_test

from sonny.config import load_config
from sonny.voice.llm import CappedOpenRouterLLM, LlmCall, _after_tool_results

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


def test_the_cap_spans_the_whole_wake_up_across_a_tool_call():
    calls: list[LlmCall] = []
    cfg = dataclasses.replace(CFG.llm, wakeup_cap_s=CAP_S)
    llm = CappedOpenRouterLLM(cfg, api_key="test-key", system_instruction="Say hi.", on_call=calls.append)
    llm._client = HungClient()
    after_tool = LLMContext(
        [
            {"role": "user", "content": "pause please"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "pause"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"paused": true}'},
        ]
    )
    llm._wakeup_start = time.monotonic() - (CAP_S - 0.1)  # the first call of this wake-up took most of the cap

    t0 = time.monotonic()
    asyncio.run(run_test(llm, frames_to_send=[LLMContextFrame(after_tool)]))
    assert time.monotonic() - t0 < CAP_S
    assert calls[0].error == "cap"


def test_a_line_said_with_the_tool_call_doesnt_restart_the_cap():
    call = {"id": "c1", "type": "function", "function": {"name": "pause"}}
    with_line = LLMContext(
        [
            {"role": "user", "content": "pause please"},
            {"role": "assistant", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"paused": true}'},
            {"role": "assistant", "content": "Enjoy the coffee."},  # the aggregator adds the line after it is spoken
        ]
    )
    assert _after_tool_results(with_line)
    assert not _after_tool_results(LLMContext([*with_line.get_messages(), {"role": "user", "content": "hi"}]))
