"""Agent scenarios replayed through the real clock, tools, conductor and context (spec §7).

The scripted agent checks the code side. The live runs check the prompt and model against the same
scenarios; they cost money and take minutes, so they are opt-in:

    SONNY_FOCUS_COMPANION_LIVE=1 uv run pytest -m live -s tests/test_agent_scenarios.py

`SONNY_FOCUS_COMPANION_LIVE_RUNS` sets the runs per scenario (default 3). Transcripts go to `logs/agent-replay/`.
"""

import os
from collections import defaultdict
from pathlib import Path

import pytest
from dotenv import find_dotenv, load_dotenv

from sonny_focus_companion.config import load_config
from tests.agent_replay import SCENARIOS, Replay, ScriptedAgent, generic_checks, run_scenario

CFG = load_config()
LIVE_PASS_SHARE = 0.99  # the ticket 09/20 contract level: 196/198 checks


def competent(payload: dict) -> tuple[str, list[tuple[str, dict]]]:
    """A scripted agent that does what the prompt asks, for the scenarios' owner lines."""
    event, session = payload["event"], payload["state"]["session"]
    if event == "owner.spoke":
        said = payload["text"].lower()
        if "time do i have left" in said:
            return f"{round(session['remaining_min'])} minutes left.", []
        remaining_10 = {"event": "session.remaining", "note": "ten left", "remaining_minutes": 10}
        for phrase, line, calls in [
            (
                "40 minutes",
                "Forty minutes on the intro, go.",
                [("start_session", {"task": "intro", "duration_minutes": 40})],
            ),
            ("email inbox", "How about thirty minutes?", []),
            ("whatever you think", "Thirty it is.", [("start_session", {"task": "email", "duration_minutes": 30})]),
            ("fifteen more", "Fifteen more, you've got this.", [("extend", {"minutes": 15})]),
            ("five more", "Five more, make them count.", [("extend", {"minutes": 5})]),
            ("pause", "Enjoy the coffee.", [("pause", {})]),
            ("i'm back", "Welcome back, onwards.", [("resume", {})]),
            ("mum is calling", "Go, take it.", [("grant_excuse", {"reason": "mum calling"})]),
            ("ten minutes left", "Will do.", [("set_hook", remaining_10)]),
            ("the minutes are left", "How many minutes left should I tell you at?", []),
            ("australia", "Canberra. Now, back to the methods.", []),
            ("stop the session", "Okay, ending it here.", [("end_session", {})]),
        ]:
            if phrase in said:
                return line, calls
        return "", []
    if event in ("phone_use.started", "phone_use.continuing", "away.started", "away.continuing"):
        return nudge(payload["state"]["episode"])
    if event == "excuse.expired" and payload["state"]["attention"] != "focused":
        return "", []  # the fresh episode's wake-up follows
    return {
        "session.remaining": ("Ten minutes left, finish strong.", []),
        "session.ended": ("Time's up, nicely done!", []),
        "excuse.expired": ("Welcome back to it.", []),
        "focused.resumed": ("There you are, nice.", [("play_emotion", {"name": "cheerful1"})]),
    }.get(event, ("", []))


PHONE_NUDGES = [  # subtle, medium, strong, then empathy
    ("twitch", "Psst, the methods are waiting."),
    ("wiggle", "Come on, phone down."),
    ("reprimand3", "Oh come on, really?"),
    ("calming1", "Everything okay? I can pause us if you need."),
]


def nudge(episode: dict) -> tuple[str, list[tuple[str, dict]]]:
    """A Nudge as the prompt asks: escalating, a follow-up after each one, few Away lines."""
    n = len(episode["nudges"])
    if episode["kind"] == "away":
        calls = []
        if episode["away_nudges"] < CFG.away.max_nudges_per_episode - 1:
            calls = [("set_hook", {"event": "away.continuing", "note": "still away", "after_seconds": 120})]
        return "Come back when you can, the methods miss you.", calls
    move, line = PHONE_NUDGES[min(n, len(PHONE_NUDGES) - 1)]
    after = {"event": "phone_use.continuing", "note": "still on the phone", "after_seconds": 30 + 15 * n}
    return line, [("play_emotion", {"name": move}), ("set_hook", after)]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_passes_with_a_scripted_agent(name, tmp_path):
    checks, r = run_scenario(CFG, ScriptedAgent(competent), name, tmp_path)
    failed = [(n, d) for n, ok, d in checks if not ok]
    assert not failed, "\n".join(r.transcript)


def test_the_generic_checks_catch_a_misbehaving_agent(tmp_path):
    def rude(payload):
        if payload["event"] == "owner.spoke":
            return "Sonny says done.", [("set_hook", {"event": "timer", "note": "x", "after_seconds": "soon"})]
        return "", []

    r = Replay(CFG, ScriptedAgent(rude), tmp_path)
    r.start_quietly("email", 25)
    r.owner("Hey Sonny, hi")
    failed = {n for n, ok, _ in generic_checks(r) if not ok}
    r.close()
    assert failed == {"never says its name", "no malformed tool calls"}


def test_the_nudge_checks_catch_an_agent_that_never_follows_up(tmp_path):
    def forgetful(payload):
        if payload["event"] == "phone_use.started":
            return "Phone down, please.", [("play_emotion", {"name": "reprimand1"})]
        return competent(payload)

    checks, r = run_scenario(CFG, ScriptedAgent(forgetful), "phone-use", tmp_path)
    failed = {n for n, ok, _ in checks if not ok}
    assert failed == {"follow-up after every Nudge", "kept Nudging over 4 min (>= 3)"}, "\n".join(r.transcript)


def test_code_holds_a_pushy_agent_to_two_away_lines(tmp_path):
    def pushy(payload):
        if payload["event"] in ("away.started", "away.continuing"):
            again = {"event": "away.continuing", "note": "again", "after_seconds": 20}
            return "Where did you go?", [("set_hook", again)]
        return competent(payload)

    checks, r = run_scenario(CFG, ScriptedAgent(pushy), "away", tmp_path)
    passed = {n for n, ok, _ in checks if ok}
    lines = [line for w in r.wakeups if w.event.startswith("away.") for line in w.lines]
    assert len(lines) == CFG.away.max_nudges_per_episode, "\n".join(r.transcript)
    assert "at most 2 Away lines per episode" in passed


def test_a_wake_up_that_never_stops_calling_tools_hits_the_step_cap(tmp_path):
    r = Replay(CFG, lambda _m, _t: {"content": None, "tool_calls": [_list_hooks()]}, tmp_path)
    w = r.owner("Hey Sonny, list my hooks forever")
    r.close()
    assert w.step_cap


def _list_hooks() -> dict:
    return {"id": "c", "type": "function", "function": {"name": "list_hooks", "arguments": "{}"}}


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("SONNY_FOCUS_COMPANION_LIVE"), reason="live model runs are opt-in: SONNY_FOCUS_COMPANION_LIVE=1"
)
def test_live_model_holds_the_contract(tmp_path):
    from tests.agent_replay import LiveAgent

    load_dotenv(find_dotenv(usecwd=True))
    agent = LiveAgent(CFG, os.environ[CFG.llm.api_key_env])
    runs = int(os.environ.get("SONNY_FOCUS_COMPANION_LIVE_RUNS", "3"))
    out = Path(CFG.log.dir) / "agent-replay" / CFG.llm.model.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)
    by_check: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for name in SCENARIOS:
        for i in range(runs):
            checks, r = run_scenario(CFG, agent, name, tmp_path / f"{name}-{i}")
            for check, ok, _ in checks:
                by_check[(name, check)].append(ok)
            marks = [f"- {'PASS' if ok else 'FAIL'} {n} {d}" for n, ok, d in checks]
            (out / f"{name}-{i}.md").write_text("\n".join([f"# {name} / run {i}", *r.transcript, "", *marks]))
            print(name, i, " ".join("P" if ok else "F" for _, ok, _ in checks), flush=True)
    passed = sum(sum(v) for v in by_check.values())
    total = sum(len(v) for v in by_check.values())
    for (name, check), oks in by_check.items():
        if not all(oks):
            print(f"  {name} / {check}: {sum(oks)}/{len(oks)}")
    print(f"{CFG.llm.model}: {passed}/{total} checks passed; transcripts in {out}")
    assert passed / total >= LIVE_PASS_SHARE
