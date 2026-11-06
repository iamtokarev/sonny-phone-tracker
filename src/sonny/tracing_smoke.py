"""Send synthetic agent traces to LangSmith and verify them; no robot or hosted LLM."""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv
from openai import AsyncOpenAI

from sonny.config import load_config
from sonny.events import EndReason, EventBus
from sonny.focus.clock import FocusClock
from sonny.focus.conductor import Conductor
from sonny.focus.tools import SessionTools
from sonny.session_log import SessionLog
from sonny.tracing import AgentTracing
from sonny.voice.prompt import agent_prompt


class SyntheticHead:
    def enter_session(self) -> None:
        pass

    def request(self, name: str) -> dict:
        return {"unavailable": "synthetic smoke test"}


async def synthetic_session(tracing: AgentTracing, path: Path) -> list:
    """Use real state/tools and the wrapped SDK, with an in-memory HTTP provider."""
    cfg = load_config()
    log = SessionLog(path)
    clock = FocusClock(cfg, log, EventBus())
    conductor = Conductor(clock.snapshot)
    tools = SessionTools(cfg, clock, lambda *args, **fields: None, head=SyntheticHead())
    schemas = [{"type": "function", "function": f.to_default_dict()} for f in tools.schemas]
    calls = 0

    async def respond(request):
        nonlocal calls
        calls += 1
        tool_calls = (
            [
                {
                    "id": "synthetic-start",
                    "type": "function",
                    "function": {
                        "name": "start_session",
                        "arguments": '{"task":"LangSmith synthetic smoke test"}',
                    },
                }
            ]
            if calls == 1
            else None
        )
        return httpx.Response(
            200,
            json={
                "id": f"synthetic-{calls}",
                "object": "chat.completion",
                "created": 1,
                "model": cfg.llm.model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Starting the synthetic session."
                            if calls == 1
                            else "This is a synthetic spoken reply."
                            if calls == 3
                            else "",
                            "tool_calls": tool_calls,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    client = tracing.wrap_client(
        AsyncOpenAI(
            api_key="synthetic-unused",
            base_url="https://synthetic.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
    )
    traces = []
    messages = [{"role": "system", "content": agent_prompt(cfg)}]
    try:
        for text in ("Start the synthetic smoke-test session.", "How much time is left?"):
            message = conductor.owner_wakeup(text)
            trace = tracing.begin(message, clock.number)
            if trace is None:
                raise RuntimeError("Tracing must be enabled with valid LangSmith configuration")
            traces.append(trace)
            messages.append(message)
            tools.begin_wakeup("owner.spoke")
            with trace.operation():
                while True:
                    start = time.monotonic()
                    reply = await client.chat.completions.create(
                        model=cfg.llm.model,
                        messages=messages,
                        tools=schemas,
                        temperature=cfg.llm.temperature,
                    )
                    trace.call(
                        {
                            "model": cfg.llm.model,
                            "ok": True,
                            "latency_s": time.monotonic() - start,
                            "first_text_s": None,
                            "attempts": 1,
                            "error": None,
                        }
                    )
                    assistant = reply.choices[0].message
                    if assistant.content:
                        trace.text(assistant.content)
                        # Simulated speaker timing: exercises the speech export
                        # contract without audio hardware or a hosted model.
                        spoken_at = time.time()
                        trace.line(
                            {
                                "text": assistant.content,
                                "tts_start": spoken_at,
                                "tts_end": spoken_at,
                                "interrupted": False,
                            }
                        )
                    messages.append(assistant.model_dump(exclude_none=True))
                    if not assistant.tool_calls:
                        break
                    for call in assistant.tool_calls:
                        result = tools.call(
                            call.function.name, json.loads(call.function.arguments), tool_call_id=call.id
                        )
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
            trace.finish()
    finally:
        await client.close()
        log.end_session(EndReason.APP_QUIT)
        log.close()
    return traces


async def verify(tracing: AgentTracing, traces: list) -> list[str]:
    agent = tracing.client.agents.retrieve(tracing.address["id"])
    environment = next(e for e in agent.environments if e.environment == tracing.address["environment"])
    project_id = environment.tracer_session_id
    deadline = time.monotonic() + 30
    while True:
        try:
            urls = []
            for index, trace in enumerate(traces):
                response = await tracing.client.traces.list_runs(
                    str(trace.root.id),
                    project_id=project_id,
                    selects=["ID", "NAME", "PARENT_RUN_IDS", "METADATA", "END_TIME", "INPUTS", "OUTPUTS"],
                )
                runs = response.items or []
                assert len(runs) == (4 if index == 0 else 2), "Missing trace runs"
                assert all(run.end_time is not None for run in runs)
                assert all(run.metadata["thread_id"] == traces[0].thread_id for run in runs)
                children = [r for r in runs if str(r.id) != str(trace.root.id)]
                assert all(r.parent_run_ids == [str(trace.root.id)] for r in children)
                root = next(r for r in runs if str(r.id) == str(trace.root.id))
                assert root.inputs["messages"] == trace.root.inputs["messages"]
                assert root.outputs["messages"] == trace.root.outputs["messages"]
                assert root.outputs["messages"][0]["role"] == "assistant"
                assert root.outputs["output"] == root.outputs["spoken_lines"][0]["text"]
                assert root.outputs["output"], "Missing Sonny's spoken reply"
                url = await tracing.client.runs.get_url(
                    str(trace.root.id),
                    project_id=project_id,
                    trace_id=str(trace.root.id),
                )
                urls.append(url.url)
            return urls
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("Could not read back the complete synthetic LangSmith traces") from None
            await asyncio.sleep(1)


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    tracing = AgentTracing.from_env(tags=("synthetic", "tracing-smoke"))
    try:
        if tracing.client is None:
            raise RuntimeError("Set LANGSMITH_TRACING=true and the LangSmith credentials and agent/environment")
        with tempfile.TemporaryDirectory(prefix="sonny-tracing-smoke-") as directory:
            traces = asyncio.run(synthetic_session(tracing, Path(directory)))
        if not tracing.flush():
            raise RuntimeError("LangSmith export did not flush within five seconds")
        urls = asyncio.run(verify(tracing, traces))
        print(f"Verified {len(urls)} synthetic traces in one session thread: {traces[0].thread_id}")
        for url in urls:
            print(url)
    finally:
        tracing.close()


if __name__ == "__main__":
    main()
