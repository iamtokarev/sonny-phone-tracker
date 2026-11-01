"""The agent's context and the voice log records (spec §3 "Idle conversation", §7 conductor and failures, §10)."""

import json

import pytest

from sonny.config import load_config
from sonny.events import EndReason, Event, EventBus, EventType
from sonny.focus.conductor import Conductor, wakeup_message, wakeup_payload
from sonny.session_log import SessionLog
from sonny.voice.activity import AgentActivity
from sonny.voice.llm import LlmCall
from sonny.voice.pipeline import Conducting, Conversation, SpokenLine
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


class Time:
    t = 1000.0

    def __call__(self) -> float:
        return self.t


def conversation(
    log: SessionLog, bus: EventBus | None = None, session=lambda: None, time=None, failed_owner_turn=lambda: None
) -> Conversation:
    return Conversation(
        log,
        bus or EventBus(),
        Conductor(lambda: {"session": None}),
        AgentActivity(120),
        session,
        keep_s=CFG.idle.conversation_keep_s,
        clock=time or Time(),
        on_failed_owner_turn=failed_owner_turn,
    )


def owner_says(c: Conversation, text: str) -> None:
    """What the user aggregator and the turn filter do with a finished owner turn."""
    c.context.add_message({"role": "user", "content": text})
    c.on_turn(text)


def test_a_failed_turn_is_logged_and_cut_from_the_context(log):
    c = conversation(log)
    owner_says(c, "how are you")
    c.context.add_message({"role": "assistant", "content": "Bright and bored."})
    owner_says(c, "tell me a joke")
    c.on_llm_call(call(ok=False, error="cap"))
    c.context.add_message({"role": "assistant", "content": "Why did the"})  # partial text, added at response end
    owner_says(c, "hello?")

    assert contents(c) == ["how are you", "Bright and bored.", "hello?"]
    failed = next(r for r in records(log) if r["type"] == "llm_call")
    assert (failed["ok"], failed["error"], failed["latency_s"]) == (False, "cap", 1.4)


def test_a_new_conversation_starts_with_an_empty_context(log):
    t = Time()
    c = conversation(log, time=t)
    owner_says(c, "how are you")
    c.on_llm_call(call())
    c.on_line(SpokenLine("Bright and bored.", 1.0, 2.0, interrupted=False))
    t.t += CFG.idle.conversation_keep_s + 1
    c.on_wake(NEW)
    assert c.context.get_messages() == []
    assert [r["type"] for r in records(log)][-2:] == ["idle_conversation_started", "wake"]


def test_a_wake_while_a_reply_is_on_its_way_keeps_the_context(log):
    c = conversation(log)
    owner_says(c, "tell me about your day, in some detail")
    c.on_wake(NEW)  # the window opened at the first wake ran out during a long turn
    assert len(c.context.get_messages()) == 1


def test_wake_and_owner_turns_reach_the_bus_and_the_log(log):
    bus = EventBus()
    seen = bus.subscribe()
    c = conversation(log, bus)
    c.on_wake(NEW)
    owner_says(c, "how are you")
    c.on_wake(WakeSeen(0.93, ignored=True, barge_in=False, new_conversation=False))

    assert [(e.type, e.data) for e in (seen.get_nowait(), seen.get_nowait())] == [
        (EventType.WAKE, {"score": 0.97}),
        (EventType.OWNER_SPOKE, {"text": "how are you"}),
    ]
    assert seen.empty()  # an ignored self-wake is logged only
    assert records(log)[-1] | {"ts": 0} == {"ts": 0, "type": "wake", "score": 0.93, "ignored": "self_wake"}


def test_a_line_cut_off_by_a_barge_in_stays_in_the_context(log):
    c = conversation(log)
    owner_says(c, "tell me a story about a cat")
    c.on_llm_call(call())
    story = "Once upon a time, a cat sat on a keyboard."
    c.on_line(SpokenLine(story, 1.0, 2.0, interrupted=True))  # cut mid-sentence: the aggregator keeps nothing
    owner_says(c, "Hey Sonny, stop it.")
    assert contents(c) == ["tell me a story about a cat", story, "Hey Sonny, stop it."]


def test_a_cut_off_line_the_aggregator_kept_in_part_is_not_added_again(log):
    c = conversation(log)
    owner_says(c, "Hey sonny, stuff.")
    c.on_llm_call(call())
    line = "Stuff, huh? You'll have to be more specific than that."
    c.on_line(SpokenLine(line, 1.0, 2.0, interrupted=True))
    c.context.add_message({"role": "assistant", "content": "Stuff, huh?"})  # what was spoken, added after
    owner_says(c, "Hey, Sonny, stop.")
    assert contents(c) == ["Hey sonny, stuff.", "Stuff, huh?", "Hey, Sonny, stop."]


def contents(c: Conversation) -> list:
    """Each message's text: what the owner said, for an owner turn's wake-up."""
    return [
        (wakeup_payload(m) or {}).get("text", m["content"]) for m in c.context.get_messages() if isinstance(m, dict)
    ]


class Sessions:
    """Which Focus Session is open, as the clock says."""

    def __init__(self) -> None:
        self.number: int | None = None

    def __call__(self) -> int | None:
        return self.number


def test_a_session_keeps_the_conversation_that_started_it_until_it_closes(log):
    sessions = Sessions()
    c = conversation(log, session=sessions)
    owner_says(c, "let's do email for 20 minutes")
    sessions.number = 1  # start_session ran
    c.on_idle()
    c.deliver(wakeup("session.started"))
    c.on_idle()
    assert contents(c)[0] == "let's do email for 20 minutes"
    sessions.number = None  # the session closed
    c.on_idle()
    assert contents(c) == []


def test_a_task_switch_keeps_only_the_turn_that_asked_for_it(log):
    sessions = Sessions()
    c = conversation(log, session=sessions)
    sessions.number = 1
    c.deliver(wakeup("session.started"))
    owner_says(c, "how long is left")
    c.context.add_message({"role": "assistant", "content": "Ten minutes."})
    owner_says(c, "yes, switch to slides")
    sessions.number = 2  # start_session ended the old session and started a new one
    c.on_idle()
    c.deliver(wakeup("session.started"))
    assert contents(c) == ["yes, switch to slides"]


def wakeup(event: str) -> dict:
    return wakeup_message({"event": event, "state": {}})


def test_the_wake_up_for_a_session_end_still_sees_the_session(log):

    sessions, bus = Sessions(), EventBus()
    conductor = Conductor(lambda: {"session": None})
    c = Conversation(
        log, bus, conductor, AgentActivity(120), sessions, keep_s=60, clock=Time(), on_failed_owner_turn=lambda: None
    )
    clock = type("Clock", (), {"tick": lambda self: None})()
    freed: list[bool] = []
    conducting = Conducting(
        clock,  # type: ignore[arg-type]
        bus,
        conductor,
        c,
        busy=lambda: False,
        on_free=lambda: freed.append(True),
    )
    sessions.number = 1
    c.deliver(wakeup("session.started"))
    owner_says(c, "pause please")
    sessions.number = None  # 30 min paused: the clock ended the session
    bus.publish(Event(EventType.SESSION_ENDED, {"reason": EndReason.PAUSE_TIMEOUT}))
    message = conducting.step()
    assert message is not None and freed == []
    c.context.add_message(message)
    assert contents(c)[0] == "pause please"
    assert conducting.step() is None  # that wake-up is over: the context goes with the session
    assert contents(c) == []
    assert freed == [True]  # and the head may leave the session


def test_a_wake_soon_after_a_silent_stop_keeps_the_conversation(log):
    t = Time()
    c = conversation(log, time=t)
    owner_says(c, "tell me a story about a cat")
    c.on_llm_call(call())
    c.on_line(SpokenLine("Once upon a time, a cat", 1.0, 2.0, interrupted=True))
    owner_says(c, "Hey Sonny, stop.")
    c.on_llm_call(call(text=False))  # silence: no line reopens the window
    t.t += CFG.idle.conversation_keep_s - 1
    c.on_wake(NEW)  # the window has closed
    owner_says(c, "what were you just telling me about?")
    assert contents(c)[0] == "tell me a story about a cat"


def test_a_failed_owner_turn_asks_for_the_confused_move(log):
    confused: list[str] = []
    c = conversation(log, failed_owner_turn=lambda: confused.append("inquiring2"))
    owner_says(c, "how are you")
    c.on_llm_call(call(ok=False, error="cap"))
    assert confused == ["inquiring2"]
    owner_says(c, "hello?")
    c.on_llm_call(call(ok=False, error="interrupted"))  # a barge-in is not a failure
    assert confused == ["inquiring2"]


def test_a_failed_event_wake_up_plays_no_move(log):
    confused: list[str] = []
    c = conversation(log, failed_owner_turn=lambda: confused.append("inquiring2"))
    message = wakeup_message({"event": "timer", "hooks": [], "state": {}})
    c.context.add_message(message)
    c.deliver(message)
    c.on_llm_call(call(ok=False, error="cap"))
    assert confused == []
