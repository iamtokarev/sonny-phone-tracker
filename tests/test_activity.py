"""When the agent is busy with a wake-up, so the conductor holds events (spec §7 "One wake-up at a time")."""

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)

from sonny.voice.activity import AgentActivity

STUCK_S = 120


class Time:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def activity() -> tuple[AgentActivity, Time]:
    t = Time()
    return AgentActivity(STUCK_S, clock=t), t


def feed(a: AgentActivity, *frames) -> None:
    for f in frames:
        a.observe(f)


def call(name: str, id: str) -> FunctionCallFromLLM:
    return FunctionCallFromLLM(function_name=name, tool_call_id=id, arguments={}, context=None)


def result(name: str, id: str) -> FunctionCallResultFrame:
    return FunctionCallResultFrame(function_name=name, tool_call_id=id, arguments={}, result={"ok": True})


def test_idle_until_a_wake_up_begins():
    a, _ = activity()
    assert not a.busy
    a.begin()
    assert a.busy  # the LLM hasn't started yet


def test_a_silent_reply_ends_the_wake_up_with_the_response():
    a, _ = activity()
    a.begin()
    feed(a, LLMFullResponseStartFrame())
    assert a.busy
    feed(a, LLMFullResponseEndFrame())
    assert not a.busy


def test_a_spoken_line_holds_until_sonny_stops_speaking():
    a, _ = activity()
    a.begin()
    feed(a, LLMFullResponseStartFrame(), LLMTextFrame("Twelve minutes left."), LLMFullResponseEndFrame())
    assert a.busy  # TTS hasn't started yet
    feed(a, BotStartedSpeakingFrame())
    assert a.busy
    feed(a, BotStoppedSpeakingFrame())
    assert not a.busy


def test_a_tool_call_holds_until_the_call_after_its_results_ends():
    a, _ = activity()
    a.begin()
    feed(
        a,
        LLMFullResponseStartFrame(),
        FunctionCallsStartedFrame(function_calls=[call("pause", "1"), call("set_hook", "2")]),
        LLMFullResponseEndFrame(),
        result("pause", "1"),
    )
    assert a.busy
    feed(a, result("set_hook", "2"))
    assert a.busy  # the results go back to the LLM
    feed(a, LLMFullResponseStartFrame(), LLMFullResponseEndFrame())
    assert not a.busy


def test_a_barge_in_hands_over_to_the_owner_turn():
    a, _ = activity()
    a.begin()
    feed(a, LLMFullResponseStartFrame(), LLMTextFrame("Once upon a time"), BotStartedSpeakingFrame())
    feed(a, InterruptionFrame())
    assert not a.busy


def test_a_frame_that_never_comes_doesnt_hold_events_for_ever():
    a, t = activity()
    a.begin()
    t.t = STUCK_S + 1
    assert not a.busy


def test_a_line_that_never_plays_stops_holding_events_soon():
    a, t = activity()
    a.begin()
    feed(a, LLMFullResponseStartFrame(), LLMTextFrame("Hi."), LLMFullResponseEndFrame())
    t.t = STUCK_S + 1
    assert not a.busy


def test_sonny_speaking_holds_events_however_long_the_line():
    a, t = activity()
    a.begin()
    feed(a, LLMFullResponseStartFrame(), LLMTextFrame("Once upon a time"), BotStartedSpeakingFrame())
    t.t = STUCK_S * 10
    assert a.busy
