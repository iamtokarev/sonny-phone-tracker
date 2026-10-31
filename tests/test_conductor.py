"""The conductor (spec §6 system hooks, §7 "Conductor and history"): what wakes the agent, the queue, and
the wake-up message."""

from sonny.events import EndReason, Event, EventType
from sonny.focus.conductor import Conductor, is_wakeup, wakeup_payload

SNAP = {"session": {"task": "email", "remaining_min": 12.0}}


def conductor() -> Conductor:
    return Conductor(lambda: SNAP)


def payloads(c: Conductor) -> list[dict]:
    out = []
    while (m := c.next_wakeup()) is not None:
        out.append(wakeup_payload(m))
    return out


def test_a_wake_up_is_a_json_message_with_the_event_note_and_state():
    c = conductor()
    c.offer(Event(EventType.TIMER, {"hook_id": 3, "note": "check the oven"}))
    message = c.next_wakeup()
    assert message is not None and message["role"] == "user" and is_wakeup(message)
    assert wakeup_payload(message) == {
        "event": "timer",
        "hooks": [{"id": 3, "note": "check the oven"}],
        "state": SNAP,
    }


def test_owner_turns_are_wake_ups_too():
    message = conductor().owner_wakeup("Hey Sonny, how long is left?")
    assert is_wakeup(message)
    assert wakeup_payload(message) == {"event": "owner.spoke", "text": "Hey Sonny, how long is left?", "state": SNAP}


def test_a_plain_transcript_is_not_a_wake_up():
    assert not is_wakeup({"role": "user", "content": "how are you"})
    assert not is_wakeup({"role": "assistant", "content": '{"event": "timer"}'})
    assert wakeup_payload({"role": "user", "content": "hi"}) is None


def test_system_hooks_decide_what_wakes_the_agent():
    c = conductor()
    woken = [
        Event(EventType.SESSION_ENDED, {"reason": EndReason.PAUSE_TIMEOUT}),
        Event(EventType.SESSION_STARTED, {"task": "email", "duration_min": 25}),
        Event(EventType.EXCUSE_EXPIRED, {"reason": "call"}),
        Event(EventType.SESSION_REMAINING, {"hook_id": 1, "note": "n"}),
    ]
    log_only = [
        Event(EventType.WAKE, {"score": 0.95}),
        Event(EventType.OWNER_SPOKE, {"text": "hi"}),  # goes through the pipeline, not the queue
        Event(EventType.PAUSE_EXPIRED, {}),  # delivered with its session.ended
        Event(EventType.SESSION_ENDED, {"reason": EndReason.ENDED_BY_OWNER}),
        Event(EventType.SESSION_ENDED, {"reason": EndReason.APP_QUIT}),
    ]
    assert [c.offer(e) for e in log_only] == [False] * 5
    assert [c.offer(e) for e in woken] == [True] * 4
    assert [p["event"] for p in payloads(c)] == [
        "session.ended",
        "session.started",
        "excuse.expired",
        "session.remaining",
    ]


def test_one_wake_up_at_a_time_in_arrival_order():
    c = conductor()
    c.offer(Event(EventType.SESSION_STARTED, {"task": "email", "duration_min": 25}))
    c.offer(Event(EventType.TIMER, {"hook_id": 1, "note": "a"}))
    assert c.pending == 2
    assert wakeup_payload(c.next_wakeup())["event"] == "session.started"
    assert c.pending == 1


def test_same_kind_events_collapse_into_one_wake_up():
    c = conductor()
    c.offer(Event(EventType.TIMER, {"hook_id": 1, "note": "stretch"}))
    c.offer(Event(EventType.EXCUSE_EXPIRED, {"reason": "call"}))
    c.offer(Event(EventType.TIMER, {"hook_id": 2, "note": "water"}))
    c.offer(Event(EventType.EXCUSE_EXPIRED, {"reason": "door"}))
    assert payloads(c) == [
        {"event": "timer", "hooks": [{"id": 1, "note": "stretch"}, {"id": 2, "note": "water"}], "state": SNAP},
        {"event": "excuse.expired", "reason": "door", "state": SNAP},
    ]


def test_a_session_end_drops_the_queued_hooks_of_that_session():
    c = conductor()
    c.offer(Event(EventType.SESSION_STARTED, {"task": "email", "duration_min": 25}))
    c.offer(Event(EventType.TIMER, {"hook_id": 1, "note": "stretch"}))
    c.offer(Event(EventType.EXCUSE_EXPIRED, {"reason": "call"}))
    c.offer(Event(EventType.SESSION_ENDED, {"reason": EndReason.COMPLETED}))
    assert [p["event"] for p in payloads(c)] == ["session.started", "session.ended"]


def test_the_state_is_read_when_the_wake_up_is_delivered():
    state = {"session": None}
    c = Conductor(lambda: state)
    c.offer(Event(EventType.SESSION_STARTED, {"task": "email", "duration_min": 25}))
    state = {"session": {"task": "email"}}
    assert wakeup_payload(c.next_wakeup())["state"] == {"session": {"task": "email"}}


def test_any_session_end_drops_the_queued_hooks_of_that_session():
    c = conductor()
    c.offer(Event(EventType.TIMER, {"hook_id": 1, "note": "stretch"}))
    c.offer(Event(EventType.EXCUSE_EXPIRED, {"reason": "call"}))
    c.offer(Event(EventType.SESSION_ENDED, {"reason": EndReason.ENDED_BY_OWNER}))
    assert c.next_wakeup() is None
