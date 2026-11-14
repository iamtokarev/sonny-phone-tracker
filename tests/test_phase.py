"""The agent's phase for the Dashboard (Dashboard spec §6): scripted frame sequences through the observer."""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from sonny_focus_companion.config import load_config
from sonny_focus_companion.dashboard.feed import DashboardFeed
from sonny_focus_companion.dashboard.protocol import Envelope
from sonny_focus_companion.session_log import SessionLog
from sonny_focus_companion.voice.phase import AgentPhases


def phases() -> tuple[AgentPhases, list[str]]:
    seen: list[str] = []
    return AgentPhases(seen.append), seen


def push(p: AgentPhases, *frames: Frame) -> None:
    async def run() -> None:
        for frame in frames:
            await p.on_push_frame(FramePushed(None, None, frame, FrameDirection.DOWNSTREAM, 0))  # type: ignore[arg-type]

    asyncio.run(run())


def test_a_woken_question_goes_listening_thinking_speaking_idle():
    p, seen = phases()
    push(p, UserStartedSpeakingFrame(), InterruptionFrame())  # the wake starts a turn (and barges in on nothing)
    push(p, UserStoppedSpeakingFrame())
    p.begin()  # the owner's turn reaches the LLM
    push(p, LLMFullResponseStartFrame(), LLMTextFrame("Twelve minutes."), LLMFullResponseEndFrame())
    push(p, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame())
    assert seen == ["listening", "thinking", "speaking", "idle"]
    assert p.phase == "idle"


def test_a_barge_in_goes_from_speaking_back_to_listening():
    p, seen = phases()
    p.begin()  # a conductor wake-up
    push(p, LLMFullResponseStartFrame(), BotStartedSpeakingFrame())
    push(p, UserStartedSpeakingFrame(), InterruptionFrame(), BotStoppedSpeakingFrame())  # "Hey Sonny" over the line
    push(p, UserStoppedSpeakingFrame())
    p.begin()
    push(p, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame())
    assert seen == ["thinking", "speaking", "listening", "thinking", "speaking", "idle"]


def test_an_interruption_outside_a_turn_ends_the_reply():
    p, seen = phases()
    p.begin()
    push(p, LLMFullResponseStartFrame(), InterruptionFrame())
    assert seen == ["thinking", "idle"]


def test_each_frame_is_reported_once_however_many_hops_it_takes():
    p, seen = phases()
    push(p, BotStartedSpeakingFrame(), BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), BotStoppedSpeakingFrame())
    assert seen == ["speaking", "idle"]


def test_a_tool_follow_up_after_the_line_shows_thinking_again():
    p, seen = phases()
    p.begin()
    push(p, BotStartedSpeakingFrame(), BotStoppedSpeakingFrame())
    push(p, LLMFullResponseStartFrame())  # the call after the tool results
    assert seen == ["thinking", "speaking", "idle", "thinking"]


def test_a_new_response_does_not_hide_the_line_being_spoken():
    p, seen = phases()
    p.begin()
    push(p, BotStartedSpeakingFrame(), LLMFullResponseStartFrame())
    assert seen == ["thinking", "speaking"]


def test_a_free_agent_settles_to_idle_once_the_wake_window_closes():
    p, seen = phases()
    push(p, UserStartedSpeakingFrame(), UserStoppedSpeakingFrame())  # "Hey Sonny" alone: a bare wake
    p.settle(window_open=True)
    assert p.phase == "listening"  # the window is open for the rest of the question
    p.settle(window_open=False)
    assert seen == ["listening", "idle"]


def test_a_silent_reply_settles_to_idle():
    p, seen = phases()
    p.begin()
    push(p, LLMFullResponseStartFrame(), LLMFullResponseEndFrame())
    p.settle(window_open=True)  # the window being open doesn't make a free agent listen
    assert seen == ["thinking", "idle"]


def test_logged_phases_reach_the_dashboard_feed_and_its_snapshot(tmp_path):
    feed = DashboardFeed(load_config().dashboard)
    log = SessionLog(tmp_path, on_record=feed.on_record)
    p = AgentPhases(lambda phase: log.idle.write("agent.phase", phase=phase))
    push(p, UserStartedSpeakingFrame())
    p.begin()
    push(p, BotStartedSpeakingFrame())
    snap = feed.snapshot()
    assert snap["agent"] == {"phase": "speaking"}
    assert [Envelope.model_validate(e).root.data.phase for e in snap["recent"]] == ["listening", "thinking", "speaking"]
    log.close()
