"""Trace contracts at the SDK export boundary and the real Pipecat model boundary."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.tests.utils import SleepFrame, run_test

from sonny_focus_companion.config import load_config
from sonny_focus_companion.events import EventBus
from sonny_focus_companion.focus.clock import FocusClock
from sonny_focus_companion.focus.conductor import Conductor, wakeup_message
from sonny_focus_companion.focus.tools import SessionTools
from sonny_focus_companion.session_log import SessionLog
from sonny_focus_companion.tracing import TRACE_KEY, AgentTracing, wakeup_context
from sonny_focus_companion.voice.activity import ActivityTap, AgentActivity
from sonny_focus_companion.voice.llm import CappedOpenRouterLLM, LlmCall
from sonny_focus_companion.voice.pipeline import Conversation, LineTracker
from tests.fakes import StubHead

CFG = load_config()


class RecordingClient:
    otel_exporter = None

    def __init__(self):
        self.runs = []

    def create_run(self, **fields):
        self.runs.append(deepcopy(fields))

    def flush(self, timeout=None):
        pass


@pytest.fixture
def tracing():
    tracer = AgentTracing(RecordingClient())
    yield tracer
    tracer.close()


class Rig:
    def __init__(self, path, tracing):
        self.now = 0.0
        self.log = SessionLog(path)
        self.bus = EventBus()
        self.clock = FocusClock(CFG, self.log, self.bus, now=lambda: self.now)
        self.tools = SessionTools(CFG, self.clock, lambda *a, **kw: None, head=StubHead())
        self.conductor = Conductor(self.clock.snapshot, self.clock.nudges)
        self.activity = AgentActivity(120)
        self.conversation = Conversation(
            self.log,
            self.bus,
            self.conductor,
            self.activity,
            lambda: self.clock.number,
            self.tools,
            keep_s=10,
            clock=lambda: self.now,
            on_failed_owner_turn=lambda: None,
            tracing=tracing,
        )

    def owner(self, text):
        self.conversation.context.add_message({"role": "user", "content": text})
        self.conversation.on_turn(text)
        return self.conversation.trace

    def end(self):
        self.conversation.on_llm_call(LlmCall(CFG.llm.model, True, 0.1, None, 1, None))
        self.activity.observe(LLMFullResponseStartFrame())
        self.activity.observe(LLMFullResponseEndFrame())


def roots(tracing):
    assert tracing.flush()
    return [r for r in tracing.client.runs if r["name"] == "sonny-focus-companion.wakeup"]


def test_session_start_switch_extension_and_terminal_wakeup_share_the_right_threads(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    first = rig.owner("start slides")
    with first.operation():
        first.root.create_child("initial model", "llm").end(outputs={"tool_calls": ["start_session"]})
        rig.tools.call("start_session", {"task": "slides", "duration_minutes": 1}, tool_call_id="start")
    rig.end()
    first_id = first.thread_id
    assert first.session == 1
    assert first_id != tracing.idle_thread
    for name in ("pause", "resume"):
        trace = rig.owner(name)
        with trace.operation():
            rig.tools.call(name, {})
        rig.end()
        assert trace.thread_id == first_id
    rig.now = CFG.session.duration_min_min * 60 + 1
    rig.clock.tick()
    extend = rig.owner("extend")
    with extend.operation():
        assert rig.tools.call("extend", {"minutes": 1})["continued"]
    rig.end()
    assert extend.thread_id == first_id

    switch = rig.owner("switch to email")
    with switch.operation():
        switch.root.create_child("switch model", "llm").end()
        rig.tools.call("start_session", {"task": "email"}, tool_call_id="switch")
    rig.end()
    assert switch.thread_id != first_id
    assert switch.root.metadata["previous_thread_id"] == first_id
    rig.conversation.deliver(wakeup_message({"event": "session.started", "state": rig.clock.snapshot()}))
    rig.end()
    terminal = rig.owner("end")
    with terminal.operation():
        rig.tools.call("end_session", {})
    rig.end()
    assert rig.clock.number is None
    rig.conversation.deliver(wakeup_message({"event": "session.ended", "state": rig.clock.snapshot()}))
    rig.end()
    assert rig.conversation.trace.thread_id == switch.thread_id == terminal.thread_id
    captured = roots(tracing)
    assert len({r["extra"]["metadata"]["thread_id"] for r in captured}) == 2
    for root in captured:
        descendants = [r for r in tracing.client.runs if r["trace_id"] == root["trace_id"]]
        assert all(r["extra"]["metadata"]["thread_id"] == root["extra"]["metadata"]["thread_id"] for r in descendants)
    rig.log.close()


def test_idle_reset_new_process_and_payload_copy(tmp_path, tracing):
    from sonny_focus_companion.voice.turns import WakeSeen

    rig = Rig(tmp_path, tracing)
    first = rig.owner("hello")
    rig.end()
    second = rig.owner("hello again")
    rig.end()
    assert first.thread_id == second.thread_id
    rig.now = 11
    rig.conversation.on_wake(WakeSeen(0.9, ignored=False, barge_in=False, new_conversation=True))
    third = rig.owner("new conversation")
    rig.end()
    assert third.thread_id != first.thread_id
    third.message["content"] = "changed after capture"
    assert roots(tracing)[-1]["inputs"]["wakeup"]["text"] == "new conversation"
    other = AgentTracing()
    assert tracing.session_thread(1) != other.session_thread(1)
    rig.log.close()


def test_local_tree_is_exported_once_after_child_work_finishes(tracing):
    trace = tracing.begin(wakeup_message({"event": "timer", "state": {}}), 1)
    with trace.operation():
        trace.finish()
        assert roots(tracing) == []
    trace.finish()
    assert len(roots(tracing)) == 1


def test_interruption_keeps_late_work_on_original_handle(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    first = rig.owner("first")
    with first.operation():
        rig.activity.observe(InterruptionFrame())
        second = rig.owner("second")
        with wakeup_context(first):
            rig.tools.call("pause", {}, tool_call_id="late")
        rig.end()
    captured = roots(tracing)
    assert len(captured) == 2
    tool = next(r for r in tracing.client.runs if r["name"] == "pause")
    assert str(tool["parent_run_id"]) == str(first.root.id)
    assert str(tool["parent_run_id"]) != str(second.root.id)
    assert first.root.outputs["outcome"] == "interrupted"
    assert tool["extra"]["metadata"]["outcome"] == "refused"
    rig.log.close()


def test_spoken_output_waits_for_line_tracker(tracing):
    trace = tracing.begin(wakeup_message({"event": "timer", "state": {}}), 1)
    trace.text("Take a break.")
    trace.finish()
    assert roots(tracing) == []
    line = LineTracker(lambda line: None)

    async def observe():
        start = LLMFullResponseStartFrame()
        start.metadata[TRACE_KEY] = trace
        await run_test(
            line,
            frames_to_send=[
                start,
                LLMTextFrame("Take a break."),
                SleepFrame(0.02),
                BotStartedSpeakingFrame(),
                SleepFrame(0.02),
                BotStoppedSpeakingFrame(),
            ],
        )

    asyncio.run(observe())
    assert roots(tracing)[0]["outputs"]["spoken_lines"][0]["text"] == "Take a break."


@pytest.mark.parametrize("speaking_started", [False, True])
def test_silent_followup_keeps_spoken_reply_visible_in_thread(tracing, speaking_started):
    trace = tracing.begin(wakeup_message({"event": "owner.spoke", "text": "start", "state": {}}), 1)
    trace.text("Let's get started.")
    trace.finish()
    observed = []
    line = LineTracker(observed.append)

    async def run():
        first, followup = LLMFullResponseStartFrame(), LLMFullResponseStartFrame()
        first.metadata[TRACE_KEY] = followup.metadata[TRACE_KEY] = trace
        frames = [first, LLMTextFrame("Let's get started."), SleepFrame(0.02)]
        if speaking_started:
            frames.extend([BotStartedSpeakingFrame(), SleepFrame(0.02)])
        # The model continues after tool results, returning intentional silence.
        frames.extend([followup, LLMFullResponseEndFrame(), SleepFrame(0.02)])
        if not speaking_started:
            frames.extend([BotStartedSpeakingFrame(), SleepFrame(0.02)])
        frames.append(BotStoppedSpeakingFrame())
        await run_test(line, frames_to_send=frames)

    asyncio.run(run())
    assert observed[0].text == "Let's get started."
    root = roots(tracing)[0]
    assert root["inputs"]["messages"] == [{"role": "user", "content": "start"}]
    assert root["outputs"]["messages"] == [{"role": "assistant", "content": "Let's get started."}]
    assert root["outputs"]["output"] == "Let's get started."
    assert root["extra"]["metadata"]["ls_agent_type"] == "root"


def test_disabled_invalid_configuration_and_export_failure_do_not_escape(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert AgentTracing.from_env().begin(wakeup_message({"event": "timer"}), 1) is None
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_AGENT_ID", raising=False)
    assert AgentTracing.from_env().client is None

    class Broken(RecordingClient):
        def create_run(self, **fields):
            raise RuntimeError("offline")

    tracer = AgentTracing(Broken())
    tracer.begin(wakeup_message({"event": "timer"}), 1).finish()
    assert tracer.flush()
    tracer.close()
    tracer.close()


def completion_stream(*, calls=None, text=None):
    chunks = []
    if calls:
        chunks.append({"tool_calls": calls})
    if text:
        chunks.append({"content": text})
    chunks.append({})
    events = []
    for index, delta in enumerate(chunks):
        data = {
            "id": "synthetic",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": CFG.llm.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": "stop" if not delta else None}],
        }
        if index == len(chunks) - 1:
            data["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        events.append("data: " + json.dumps(data) + "\n\n")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="".join(events) + "data: [DONE]\n\n")


def test_pipecat_stream_captures_effective_prompt_tools_usage_and_cleans_up(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    trace = rig.owner("hello")
    requests = []

    async def run():
        llm = CappedOpenRouterLLM(
            CFG.llm,
            api_key="secret-never-export",
            system_instruction="Effective system prompt.",
            on_call=rig.conversation.on_llm_call,
            tracing=tracing,
        )
        original = llm._client

        async def respond(request):
            requests.append(json.loads(request.content))
            return completion_stream()

        http = httpx.AsyncClient(transport=httpx.MockTransport(respond), event_hooks={"request": [llm._count_attempt]})
        client = AsyncOpenAI(api_key="secret-never-export", base_url="https://synthetic.invalid/v1", http_client=http)
        llm._client = tracing.wrap_client(client)
        await run_test(
            Pipeline([llm, ActivityTap(rig.activity)]), frames_to_send=[LLMContextFrame(rig.conversation.context)]
        )
        await client.close()
        await original.close()
        assert http.is_closed

    asyncio.run(run())
    captured = roots(tracing)
    assert len(captured) == 1 and trace.finished
    model = next(r for r in tracing.client.runs if r["run_type"] == "llm")
    assert str(model["parent_run_id"]) == str(trace.root.id)
    assert model["inputs"]["messages"] == requests[0]["messages"]
    assert any(m.get("content") == "Effective system prompt." for m in model["inputs"]["messages"])
    assert model["inputs"]["tools"] == requests[0]["tools"]
    assert model["outputs"]["usage_metadata"]["input_tokens"] == 12
    assert model["extra"]["metadata"]["ls_provider"] == "openrouter"
    assert trace.root.outputs["llm_calls"][0]["attempts"] == 1
    assert "secret-never-export" not in repr(tracing.client.runs)
    rig.log.close()


def test_pipecat_timeout_is_recorded_even_when_application_catches_it(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    trace = rig.owner("hello")

    async def run():
        llm = CappedOpenRouterLLM(
            replace(CFG.llm, wakeup_cap_s=0.05),
            api_key="test",
            system_instruction="hello",
            on_call=rig.conversation.on_llm_call,
            tracing=tracing,
        )

        async def hang(request):
            await asyncio.Event().wait()

        original = llm._client
        llm._client = tracing.wrap_client(
            AsyncOpenAI(
                api_key="test",
                base_url="https://synthetic.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(hang)),
            )
        )
        await run_test(
            Pipeline([llm, ActivityTap(rig.activity)]), frames_to_send=[LLMContextFrame(rig.conversation.context)]
        )
        await llm._client.close()
        await original.close()

    asyncio.run(run())
    assert roots(tracing)[0]["outputs"]["outcome"] == "cap"
    assert trace.root.error == "cap"
    rig.log.close()


def test_pipecat_queued_tool_and_followup_stay_under_starting_trace(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    trace = rig.owner("start slides")
    requests = []

    async def run():
        llm = CappedOpenRouterLLM(
            CFG.llm,
            api_key="test",
            system_instruction="Use tools.",
            on_call=rig.conversation.on_llm_call,
            tracing=tracing,
        )
        llm.register_function("start_session", rig.tools.handle)
        original = llm._client

        async def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return completion_stream(
                    calls=[
                        {
                            "index": 0,
                            "id": "start",
                            "type": "function",
                            "function": {"name": "start_session", "arguments": '{"task":"slides"}'},
                        }
                    ]
                )
            return completion_stream()

        client = AsyncOpenAI(
            api_key="test",
            base_url="https://synthetic.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        llm._client = tracing.wrap_client(client)
        _, assistant = LLMContextAggregatorPair(rig.conversation.context)
        await run_test(
            Pipeline([llm, ActivityTap(rig.activity), assistant]),
            frames_to_send=[LLMContextFrame(rig.conversation.context), SleepFrame(0.15)],
        )
        await client.close()
        await original.close()

    asyncio.run(run())
    assert len(requests) == 2
    assert any(m["role"] == "tool" and "started" in m["content"] for m in requests[1]["messages"])
    assert len(roots(tracing)) == 1
    assert trace.session == 1
    children = [r for r in tracing.client.runs if r.get("parent_run_id")]
    assert [r["run_type"] for r in children].count("llm") == 2
    assert [r["name"] for r in children].count("start_session") == 1
    assert all(str(r["parent_run_id"]) == str(trace.root.id) for r in children)
    assert all(r["extra"]["metadata"]["thread_id"] == tracing.session_thread(1) for r in children)
    rig.log.close()


def test_stuck_and_shutdown_finalize_without_duplicate_exports(tmp_path, tracing):
    rig = Rig(tmp_path, tracing)
    trace = rig.owner("hello")
    rig.activity._clock = lambda: float("inf")
    assert not rig.activity.busy
    assert roots(tracing)[0]["outputs"]["outcome"] == "stuck"
    pending = rig.owner("unfinished")
    child = pending.root.create_child("unfinished tool", "tool")
    tracing.close()
    assert pending.root.outputs["outcome"] == "shutdown"
    assert child.end_time is not None and child.error == "shutdown"
    trace.finish()
    assert len(tracing.client.runs) == 3
    rig.log.close()


def test_queued_tool_retains_original_trace_before_handler_starts(tmp_path, tracing, monkeypatch):
    from pipecat.frames.frames import FunctionCallFromLLM
    from pipecat.services.openrouter.llm import OpenRouterLLMService

    rig = Rig(tmp_path, tracing)
    first = rig.owner("first")

    async def queued(self, calls):
        pass

    async def execute(self, item):
        rig.tools.call("pause", {}, tool_call_id=item.tool_call_id)

    monkeypatch.setattr(OpenRouterLLMService, "run_function_calls", queued)
    monkeypatch.setattr(OpenRouterLLMService, "_run_function_call", execute)

    async def run():
        llm = CappedOpenRouterLLM(
            CFG.llm,
            api_key="test",
            system_instruction="test",
            on_call=lambda call: None,
            tracing=tracing,
        )
        with first.operation():
            await llm.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name="pause",
                        tool_call_id="queued",
                        arguments={},
                        context=rig.conversation.context,
                    )
                ]
            )
        second = rig.owner("second")
        assert not first.finished
        await llm._run_function_call(SimpleNamespace(tool_call_id="queued"))
        assert first.finished and not second.finished
        rig.end()
        await llm._client.close()

    asyncio.run(run())
    assert len(roots(tracing)) == 2
    child = next(r for r in tracing.client.runs if r["name"] == "pause")
    assert str(child["parent_run_id"]) == str(first.root.id)
    rig.log.close()


def test_interrupted_speech_is_recorded_before_activity_exports(tracing):
    trace = tracing.begin(wakeup_message({"event": "timer"}), 1)
    trace.text("Not finished")
    activity = AgentActivity(120)
    activity.begin(on_finish=trace.finish)
    start = LLMFullResponseStartFrame()
    start.metadata[TRACE_KEY] = trace
    line = LineTracker(lambda line: None)

    async def run():
        await run_test(
            Pipeline([ActivityTap(activity), line]),
            frames_to_send=[
                start,
                LLMTextFrame("Not finished"),
                SleepFrame(0.02),
                BotStartedSpeakingFrame(),
                SleepFrame(0.02),
                InterruptionFrame(),
                SleepFrame(0.02),
            ],
        )

    asyncio.run(run())
    output = roots(tracing)[0]["outputs"]
    assert output["outcome"] == "interrupted"
    assert output["spoken_lines"][0]["interrupted"]


@pytest.mark.parametrize("cap", [True, False])
def test_stream_is_closed_and_partial_output_preserved_on_cap_or_cancellation(tmp_path, tracing, cap):
    rig = Rig(tmp_path, tracing)
    trace = rig.owner("hello")

    class SlowStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            chunk = {
                "id": "partial",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": CFG.llm.model,
                "choices": [{"index": 0, "delta": {"content": "Partial words"}, "finish_reason": None}],
            }
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    async def run():
        llm = CappedOpenRouterLLM(
            replace(CFG.llm, wakeup_cap_s=0.05 if cap else 2),
            api_key="test",
            system_instruction="hello",
            on_call=rig.conversation.on_llm_call,
            tracing=tracing,
        )
        stream = SlowStream()

        async def respond(request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        original = llm._client
        llm._client = tracing.wrap_client(
            AsyncOpenAI(
                api_key="test",
                base_url="https://synthetic.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
            )
        )
        try:
            await run_test(
                Pipeline(
                    [
                        llm,
                        ActivityTap(rig.activity),
                        LineTracker(
                            rig.conversation.on_line,
                            pending_trace=lambda: rig.conversation.trace,
                        ),
                    ]
                ),
                frames_to_send=[
                    LLMContextFrame(rig.conversation.context),
                    SleepFrame(0.15),
                    InterruptionFrame(),
                    SleepFrame(0.03),
                ],
            )
            assert stream.closed
        finally:
            await llm._client.close()
            await original.close()

    asyncio.run(run())
    assert roots(tracing)[0]["outputs"]["outcome"] == ("cap" if cap else "interrupted")
    assert trace.root.outputs["generated_text"] == "Partial words"
    assert all(r.get("end_time") is not None for r in tracing.client.runs)
    rig.log.close()
