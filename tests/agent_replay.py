"""Agent-scenario replay (spec §7 "Agent instructions"), ported from the ticket 09/20 harness.

Scripted owner lines and clock time run through the real session clock, tool handlers, conductor and
conversation context, on scripted time. The agent is either scripted (`ScriptedAgent`, for the unit
tests) or the configured model on OpenRouter (`LiveAgent`, opt-in). Speech is the agent's plain text: a
reply's text is Sonny's line; an empty reply is silence. No robot, no audio, no Pipecat pipeline: each
wake-up runs to the end before time moves on, the way the conductor holds events during one.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from sonny.config import Config
from sonny.events import EndReason, EventBus
from sonny.focus.clock import FocusClock
from sonny.focus.conductor import Conductor, wakeup_payload
from sonny.focus.tools import SessionTools
from sonny.session_log import SessionLog
from sonny.voice.activity import AgentActivity
from sonny.voice.pipeline import Conducting, Conversation
from sonny.voice.prompt import agent_prompt

MAX_STEPS_PER_WAKEUP = 6  # LLM calls; a wake-up that needs more never ends on its own
FILLER = re.compile(r"^\W*(done|ok|okay|noted|sure|silence|\(.*\)|\[.*\]|\*.*\*)\W*$", re.I)
TOOL_TALK = re.compile(r"set_hook|grant_excuse|start_session|end_session|\bhook\b|\btool\b|calling ", re.I)
NAME = re.compile(r"sonny", re.I)

# The agent: given the messages (system prompt first) and the tools, one assistant message as the
# OpenAI API returns it: {"content": str | None, "tool_calls": [...] | None}.
Agent = Callable[[list[dict], list[dict]], dict]


@dataclass
class Wakeup:
    event: str
    payload: dict
    lines: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict, dict]] = field(default_factory=list)  # name, args, result
    step_cap: bool = False

    def called(self, name: str) -> list[tuple[dict, dict]]:
        return [(args, result) for n, args, result in self.calls if n == name]

    def ok_calls(self, name: str) -> list[dict]:
        return [args for args, result in self.called(name) if "refused" not in result and "error" not in result]


class ScriptedTime:
    def __init__(self) -> None:
        self.t = 10_000.0

    def __call__(self) -> float:
        return self.t


class Replay:
    def __init__(self, cfg: Config, agent: Agent, log_dir: Path) -> None:
        self.cfg, self.agent = cfg, agent
        self.time = ScriptedTime()
        self.log = SessionLog(log_dir)
        bus = EventBus()
        self.clock = FocusClock(cfg, self.log, bus, now=self.time)
        conductor = Conductor(self.clock.snapshot)
        self.tools = SessionTools(
            cfg, self.clock, lambda type, **f: (self.log.session or self.log.idle).write(type, **f)
        )
        self.conversation = Conversation(
            self.log, bus, conductor, AgentActivity(cfg.conductor.stuck_after_s), lambda: self.clock.number, self.tools
        )
        self.conducting = Conducting(self.clock, bus, conductor, self.conversation, busy=lambda: False)
        self.system = {"role": "system", "content": agent_prompt(cfg)}
        self.tool_specs = [{"type": "function", "function": f.to_default_dict()} for f in self.tools.schemas]
        self.wakeups: list[Wakeup] = []
        self.transcript: list[str] = []

    def _write(self, type: str, **fields) -> None:
        (self.log.session or self.log.idle).write(type, **fields)

    @property
    def context(self):
        return self.conversation.context

    # ---------------------------------------------------------------- driving

    def owner(self, text: str) -> Wakeup:
        """The owner says `text`, as the user aggregator and `OwnerTurns` hand it on."""
        self.context.add_message({"role": "user", "content": text})
        self.conversation.on_turn(text)
        return self._run(self.context.get_messages()[-1])

    def advance(self, seconds: float, step: float = 1.0) -> None:
        """Move time, delivering each wake-up the conductor hands over on the way."""
        end = self.time.t + seconds
        while True:
            if (message := self.conducting.step()) is not None:
                self.context.add_message(message)
                self._run(message)
            if self.time.t >= end:
                break
            self.time.t = min(end, self.time.t + step)

    def start_quietly(self, task: str, minutes: int) -> None:
        """A session that is already running, its session.started wake-up delivered."""
        self.tools.call("start_session", {"task": task, "duration_minutes": minutes})
        self.advance(0)

    def _run(self, message: object) -> Wakeup:
        payload = wakeup_payload(message) or {}
        w = Wakeup(payload.get("event", "?"), payload)
        self.wakeups.append(w)
        self.transcript.append(f"\n### {w.event} {json.dumps({k: v for k, v in payload.items() if k != 'state'})}")
        self.transcript.append(f"state: {json.dumps(payload.get('state'))}")
        for _ in range(MAX_STEPS_PER_WAKEUP):
            reply = self.agent([self.system, *self.context.get_messages()], self.tool_specs)
            text = (reply.get("content") or "").strip()
            calls = reply.get("tool_calls") or []
            if text or calls:  # an empty reply (silence) leaves no turn behind, as in Pipecat
                self.context.add_message(
                    {"role": "assistant", "content": text or None} | ({"tool_calls": calls} if calls else {})
                )
            if text:
                w.lines.append(text)
                self.transcript.append(f"- SAYS: {text}")
            if not calls:
                return w
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args, result = {}, {"error": "arguments are not valid JSON"}
                else:
                    result = self.tools.call(name, args)
                w.calls.append((name, args, result))
                self.transcript.append(f"- {name}({json.dumps(args)}) -> {json.dumps(result)}")
                self.context.add_message({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
        w.step_cap = True
        self.transcript.append("- (step cap reached)")
        return w

    # ---------------------------------------------------------------- reading

    def of(self, event: str) -> list[Wakeup]:
        return [w for w in self.wakeups if w.event == event]

    def calls(self, name: str) -> list[dict]:
        return [args for w in self.wakeups for args in w.ok_calls(name)]

    def close(self) -> None:
        self.log.end_session(EndReason.APP_QUIT)
        self.log.close()


Check = tuple[str, bool, str]  # name, passed, detail


def generic_checks(r: Replay) -> list[Check]:
    lines = [line for w in r.wakeups for line in w.lines]
    stray = [line for line in lines if FILLER.match(line) or TOOL_TALK.search(line)]
    errors = [res for w in r.wakeups for _, _, res in w.calls if "error" in res]
    return [
        ("every wake-up ended on its own", not any(w.step_cap for w in r.wakeups), ""),
        ("at most one line per wake-up", all(len(w.lines) <= 1 for w in r.wakeups), ""),
        ("no stray text", not stray, "; ".join(stray)[:160]),
        ("never says its name", not any(NAME.search(line) for line in lines), ""),
        ("silent on session.started", not any(w.lines for w in r.of("session.started")), ""),
        ("no malformed tool calls", not errors, str(errors)[:160]),
    ]


# ---------------------------------------------------------------- scenarios


def sc_setup(r: Replay) -> list[Check]:
    first = r.owner("Hey Sonny, let's focus. I want to write the intro of my thesis for 40 minutes.")
    if not r.calls("start_session"):
        r.owner("Yes, sounds good, let's go.")
    r.advance(1)
    starts = r.calls("start_session")
    return [
        ("start_session called", bool(starts), ""),
        ("duration 40", any(a.get("duration_minutes") == 40 for a in starts), str(starts)),
        ("confirmed in one line", bool(first.lines), ""),
        ("session.started delivered", bool(r.of("session.started")), ""),
    ]


def sc_setup_no_duration(r: Replay) -> list[Check]:
    first = r.owner("Hey Sonny, I need to get through my email inbox.")
    for reply in ("Whatever you think, sure.", "Yes. Start it."):
        if not r.calls("start_session"):
            r.owner(reply)
    starts = r.calls("start_session")
    return [
        ("start_session within 3 turns", bool(starts), ""),
        ("asked or suggested a Duration aloud", bool(first.lines), ""),
        ("explicit duration passed", bool(starts) and starts[0].get("duration_minutes") is not None, str(starts)),
    ]


def sc_extend_and_end(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(44 * 60)
    ask = r.owner("Hey Sonny, I'm almost there, give me fifteen more minutes.")
    r.advance(16 * 60)
    ends = r.of("session.ended")
    return [
        ("extend(15)", any(a.get("minutes") == 15 for a in ask.ok_calls("extend")), str(ask.calls)),
        ("time's up spoken", bool(ends) and bool(ends[0].lines), ""),
        ("time's up after the extension", bool(ends) and ends[0].payload["state"]["session"]["duration_min"] == 60, ""),
    ]


def sc_extend_after_times_up(r: Replay) -> list[Check]:
    r.start_quietly("slides", 20)
    r.advance(20 * 60 + 30)
    ask = r.owner("Hey Sonny, oh no, I need five more minutes.")
    session = r.clock.snapshot()["session"]
    return [
        ("time's up spoken", any(w.lines for w in r.of("session.ended")), ""),
        ("extend in the grace", any(a.get("minutes") == 5 for a in ask.ok_calls("extend")), str(ask.calls)),
        ("same session continues", bool(session) and session["task"] == "slides" and not session["time_up"], ""),
    ]


def sc_pause_resume(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(600)
    r.owner("Hey Sonny, pause for a bit, I'm grabbing a coffee.")
    r.advance(400)
    r.owner("Okay, I'm back, let's continue.")
    session = r.clock.snapshot()["session"]
    return [
        ("pause()", bool(r.calls("pause")), ""),
        ("resume()", bool(r.calls("resume")), ""),
        ("clock frozen", bool(session) and round(session["remaining_min"]) == 35, str(session)),
    ]


def sc_excuse(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(200)
    ask = r.owner("Sorry Sonny, my mum is calling, it's urgent, I need to take this.")
    granted = ask.ok_calls("grant_excuse")
    minutes = (granted[0].get("minutes") if granted else None) or r.cfg.excuse.default_min
    r.advance(minutes * 60 + 30)
    return [
        ("granted the Excuse", bool(granted), str(ask.calls)),
        ("acknowledged", bool(ask.lines), ""),
        ("excuse.expired woke the agent", bool(r.of("excuse.expired")), ""),
    ]


def sc_check_in(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(60)
    ask = r.owner("Hey Sonny, tell me when there are ten minutes left.")
    r.advance(36 * 60)
    fired = r.of("session.remaining")
    hooks = [a for a in ask.ok_calls("set_hook") if a.get("event") == "session.remaining"]
    return [
        ("set_hook session.remaining 10", any(a.get("remaining_minutes") == 10 for a in hooks), str(ask.calls)),
        ("fired with 10 min left", bool(fired) and fired[0].payload["state"]["session"]["remaining_min"] == 10, ""),
        ("spoke when it fired", bool(fired) and bool(fired[0].lines), ""),
    ]


def sc_time_left(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(20 * 60)
    ask = r.owner("Hey Sonny, how much time do I have left?")
    said = " ".join(ask.lines).lower()
    return [
        ("answered from the state (25 min)", bool(re.search(r"\b25\b|twenty[- ]five", said)), said[:120]),
        ("no session tools", not ask.calls, str(ask.calls)),
    ]


def sc_unclear_number(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(60)
    ask = r.owner("Hey Sonny, tell me when the minutes are left.")
    return [
        ("no hook guessed", not ask.ok_calls("set_hook"), str(ask.calls)),
        ("asked back", any("?" in line for line in ask.lines), " ".join(ask.lines)[:120]),
    ]


def sc_end_early_and_off_task(r: Replay) -> list[Check]:
    r.start_quietly("write the methods section", 45)
    r.advance(300)
    off = r.owner("Hey Sonny, what's the capital of Australia?")
    r.owner("Actually, stop the session, I'm done for today.")
    return [
        ("off-task: one brief line, no tools", bool(off.lines) and not off.calls, str(off.calls)),
        ("end_session()", bool(r.calls("end_session")), ""),
        ("session over", r.clock.snapshot()["session"] is None, ""),
    ]


SCENARIOS: dict[str, Callable[[Replay], list[Check]]] = {
    "setup": sc_setup,
    "setup-no-duration": sc_setup_no_duration,
    "extend-end": sc_extend_and_end,
    "extend-after-times-up": sc_extend_after_times_up,
    "pause": sc_pause_resume,
    "excuse": sc_excuse,
    "check-in": sc_check_in,
    "time-left": sc_time_left,
    "unclear-number": sc_unclear_number,
    "end-early": sc_end_early_and_off_task,
}


def run_scenario(cfg: Config, agent: Agent, name: str, log_dir: Path) -> tuple[list[Check], Replay]:
    r = Replay(cfg, agent, log_dir)
    try:
        checks = SCENARIOS[name](r) + generic_checks(r)
    finally:
        r.close()
    return checks, r


# ---------------------------------------------------------------- agents


class ScriptedAgent:
    """Answers each wake-up with what `respond(payload)` returns: (line, [(tool, args), ...]). After the
    tool results come back it replies with nothing, as the speech contract asks."""

    def __init__(self, respond: Callable[[dict], tuple[str, list[tuple[str, dict]]]]) -> None:
        self._respond = respond
        self._ids = 0

    def __call__(self, messages: list[dict], _tools: list[dict]) -> dict:
        last = messages[-1]
        if last["role"] == "tool":
            return {"content": None}
        text, calls = self._respond(wakeup_payload(last) or {})
        tool_calls = []
        for name, args in calls:
            self._ids += 1
            tool_calls.append(
                {
                    "id": f"call_{self._ids}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            )
        return {"content": text, "tool_calls": tool_calls or None}


class LiveAgent:
    """The configured model on OpenRouter, with the app's temperature and SDK retries."""

    def __init__(self, cfg: Config, api_key: str) -> None:
        from openai import OpenAI

        self.cfg = cfg
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1", api_key=api_key, max_retries=cfg.llm.sdk_max_retries
        )

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        response = self.client.chat.completions.create(
            model=self.cfg.llm.model,
            messages=cast(Any, messages),
            tools=cast(Any, tools),
            temperature=self.cfg.llm.temperature,
            timeout=60,
        )
        m = response.choices[0].message
        calls = [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in m.tool_calls or []
            if c.type == "function"
        ]
        return {"content": m.content, "tool_calls": calls or None}
