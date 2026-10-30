"""The idle conversation's context and log records (spec §3 "Idle conversation", §7 failures, §10)."""

import json

import pytest

from sonny.config import load_config
from sonny.events import EventBus, EventType
from sonny.session_log import SessionLog
from sonny.voice.llm import LlmCall
from sonny.voice.pipeline import IdleConversation, SpokenLine
from sonny.voice.turns import WakeSeen

CFG = load_config()
NEW = WakeSeen(0.97, ignored=False, barge_in=False, new_conversation=True)


def call(ok: bool = True, text: bool = True, error: str | None = None) -> LlmCall:
    return LlmCall(CFG.llm.model, ok, latency_s=1.4, first_text_s=1.1 if text else None, attempts=1, error=error)


@pytest.fixture
def log(tmp_path):
    log = SessionLog(tmp_path)
    yield log
    log.close()


def records(log: SessionLog) -> list[dict]:
    return [json.loads(line) for line in log.idle.path.read_text().splitlines()]


def owner_says(c: IdleConversation, text: str) -> None:
    """What the user aggregator and the turn filter do with a finished owner turn."""
    c.context.add_message({"role": "user", "content": text})
    c.on_turn(text)


def test_a_failed_turn_is_logged_and_cut_from_the_context(log):
    c = IdleConversation(log, EventBus())
    owner_says(c, "how are you")
    c.context.add_message({"role": "assistant", "content": "Bright and bored."})
    before = list(c.context.get_messages())
    owner_says(c, "tell me a joke")
    c.on_llm_call(call(ok=False, error="cap"))
    c.context.add_message({"role": "assistant", "content": "Why did the"})  # partial text, added at response end
    owner_says(c, "hello?")

    assert c.context.get_messages() == [*before, {"role": "user", "content": "hello?"}]
    failed = next(r for r in records(log) if r["type"] == "llm_call")
    assert (failed["ok"], failed["error"], failed["latency_s"]) == (False, "cap", 1.4)


def test_a_new_conversation_starts_with_an_empty_context(log):
    c = IdleConversation(log, EventBus())
    owner_says(c, "how are you")
    c.on_llm_call(call())
    c.on_line(SpokenLine("Bright and bored.", 1.0, 2.0, interrupted=False))
    c.on_wake(NEW)
    assert c.context.get_messages() == []
    assert [r["type"] for r in records(log)][-2:] == ["idle_conversation_started", "wake"]


def test_a_wake_while_a_reply_is_on_its_way_keeps_the_context(log):
    c = IdleConversation(log, EventBus())
    owner_says(c, "tell me about your day, in some detail")
    c.on_wake(NEW)  # the window opened at the first wake ran out during a long turn
    assert len(c.context.get_messages()) == 1


def test_wake_and_owner_turns_reach_the_bus_and_the_log(log):
    bus = EventBus()
    seen = bus.subscribe()
    c = IdleConversation(log, bus)
    c.on_wake(NEW)
    owner_says(c, "how are you")
    c.on_wake(WakeSeen(0.93, ignored=True, barge_in=False, new_conversation=False))

    assert [(e.type, e.data) for e in (seen.get_nowait(), seen.get_nowait())] == [
        (EventType.WAKE, {"score": 0.97}),
        (EventType.OWNER_SPOKE, {"text": "how are you"}),
    ]
    assert seen.empty()  # an ignored self-wake is logged only
    assert records(log)[-1] | {"ts": 0} == {"ts": 0, "type": "wake", "score": 0.93, "ignored": "self_wake"}
