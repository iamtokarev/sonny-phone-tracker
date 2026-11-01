"""The session tools the agent calls (spec §7 "Tools"): refusals and bad arguments come back as reasons."""

import pytest

from sonny.config import load_config
from sonny.events import EventBus
from sonny.focus.clock import FocusClock
from sonny.focus.tools import SessionTools
from sonny.session_log import SessionLog
from tests.fakes import StubHead

CFG = load_config()


@pytest.fixture
def tools(tmp_path):
    log = SessionLog(tmp_path)
    written: list[tuple[str, dict]] = []
    yield (
        SessionTools(
            CFG, FocusClock(CFG, log, EventBus()), lambda type, **f: written.append((type, f)), head=StubHead()
        ),
        written,
    )
    log.close()


def test_every_tool_has_a_schema():
    names = [
        f.name
        for f in SessionTools(CFG, FocusClock(CFG, SessionLog("unused"), EventBus()), print, head=StubHead()).schemas
    ]
    assert names == [
        "start_session",
        "extend",
        "pause",
        "resume",
        "end_session",
        "grant_excuse",
        "set_hook",
        "list_hooks",
        "cancel_hook",
        "play_emotion",
    ]


def test_a_call_returns_its_result_and_is_logged(tools):
    tools, written = tools
    assert tools.call("start_session", {"task": "email", "duration_minutes": 40})["duration_min"] == 40
    assert written[-1] == (
        "tool_call",
        {
            "name": "start_session",
            "args": {"task": "email", "duration_minutes": 40},
            "result": {"started": True, "task": "email", "duration_min": 40},
        },
    )


def test_a_refused_call_returns_its_reason(tools):
    tools, written = tools
    assert tools.call("pause", {}) == {"refused": "no Focus Session is running"}
    assert written[-1][1]["result"] == {"refused": "no Focus Session is running"}


@pytest.mark.parametrize(
    ("name", "args"),
    [("start_session", {}), ("extend", {"minutes": "lots"}), ("pause", {"now": True}), ("speak", {"text": "hi"})],
)
def test_bad_arguments_return_an_error(tools, name, args):
    tools, _ = tools
    tools.call("start_session", {"task": "email"})
    tools.begin_wakeup()
    assert tools.call(name, args)["error"].startswith("bad arguments")


def test_a_session_change_runs_once_per_wake_up(tools):
    tools, _ = tools
    tools.call("start_session", {"task": "email", "duration_minutes": 20})
    tools.begin_wakeup()
    assert tools.call("extend", {"minutes": 15})["remaining_min"] == 35
    assert tools.call("extend", {}) == {"refused": "extend already ran in this wake-up; nothing more to do"}
    assert "hook_id" in tools.call("set_hook", {"event": "timer", "note": "a", "after_seconds": 60})
    assert "hook_id" in tools.call("set_hook", {"event": "timer", "note": "b", "after_seconds": 120})
    tools.begin_wakeup()
    assert tools.call("extend", {})["remaining_min"] == 45


def test_play_emotion_offers_the_18_moves_with_their_meanings_and_intensity():
    schema = next(
        f
        for f in SessionTools(CFG, FocusClock(CFG, SessionLog("unused"), EventBus()), print, head=StubHead()).schemas
        if f.name == "play_emotion"
    )
    names = schema.properties["name"]["enum"]
    assert len(names) == 18 and {"perk", "inquiring2", "calming1", "proud3", "success2"} <= set(names)
    assert "Antennas droop slowly: disappointed." in schema.description
    assert "droop (medium)" in schema.description


def test_play_emotion_asks_the_head_owner_and_logs_the_answer(tools):
    tools, written = tools
    assert tools.call("play_emotion", {"name": "yes1"}) == {"playing": "yes1", "intensity": "medium"}
    assert tools.call("play_emotion", {"name": "yes1"})["playing"] == "yes1"  # not once per wake-up: busy guards
    assert written[-1][1]["name"] == "play_emotion"


def test_starting_a_session_hands_the_head_over_before_the_session_starts(tmp_path):
    head = StubHead()
    seen: list[list[str]] = []

    class Watching(EventBus):
        def publish(self, event):
            seen.append(list(head.calls))

    log = SessionLog(tmp_path)
    tools = SessionTools(CFG, FocusClock(CFG, log, Watching()), lambda *a, **k: None, head=head)
    tools.call("start_session", {"task": "email"})
    assert seen[0] == ["enter_session"]  # already done when session.started is published
    log.close()
