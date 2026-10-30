import threading

from sonny.events import EndReason, Event, EventBus, EventType


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_every_subscriber_gets_every_event():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish(Event(EventType.WAKE, {"score": 0.95}))
    bus.publish(Event(EventType.SESSION_STARTED))
    assert [e.type for e in drain(a)] == [EventType.WAKE, EventType.SESSION_STARTED]
    assert [e.type for e in drain(b)] == [EventType.WAKE, EventType.SESSION_STARTED]


def test_subscriber_can_filter_by_type():
    bus = EventBus()
    wakes = bus.subscribe({EventType.WAKE})
    bus.publish(Event(EventType.SESSION_STARTED))
    bus.publish(Event(EventType.WAKE, {"score": 0.91}))
    assert [e.data["score"] for e in drain(wakes)] == [0.91]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish(Event(EventType.WAKE))
    assert q.empty()


def test_fan_out_from_many_threads_loses_nothing():
    bus = EventBus()
    subs = [bus.subscribe() for _ in range(3)]
    n, threads = 500, 4

    def burst():
        for _ in range(n):
            bus.publish(Event(EventType.TIMER))

    workers = [threading.Thread(target=burst) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert [q.qsize() for q in subs] == [n * threads] * 3


def test_event_names_match_spec():
    assert EventType.OWNER_SPOKE == "owner.spoke"
    assert EventType.PHONE_USE_CONTINUING == "phone_use.continuing"
    assert EventType.LINK_RESTORED == "link.restored"
    assert EndReason.APP_QUIT == "app_quit"
    assert len(EventType) == 16


def test_event_stamps_time():
    assert Event(EventType.WAKE).t > 0
