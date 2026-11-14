"""The state feed's wire types (spec §4): the Pydantic models, what the feed sends, and the generated TypeScript."""

import re
from pathlib import Path

import pytest

from sonny_focus_companion.dashboard.feed import DashboardFeed, Readings
from sonny_focus_companion.dashboard.protocol import Envelope, Snapshot, envelope_types, schema_hash
from sonny_focus_companion.events import EventType
from tests.test_dashboard_feed import CFG, SESSION, read, recorded_logs

GENERATED = Path(__file__).parents[1] / "web" / "src" / "protocol.gen.ts"


def test_the_generated_typescript_matches_the_models():
    banner = re.search(r"schema sha256: ([0-9a-f]{64})", GENERATED.read_text())
    assert banner is not None, f"{GENERATED} has no schema hash"
    assert banner[1] == schema_hash(), "web/src/protocol.gen.ts is stale: run `bun run gen:protocol` in web/"


def test_every_bus_event_type_is_an_envelope_type():
    assert {str(t) for t in EventType} <= envelope_types()


def test_what_the_feed_sends_and_snapshots_fits_the_protocol():
    feed = DashboardFeed(CFG)
    sent: list[dict] = []
    feed.listen(sent.append)
    for record in SESSION[:6]:
        feed.on_record(record)
    feed.tick(Readings(link_down="media_drop", frames=10, frame_t=1.0))
    feed.on_record({"ts": 300.0, "type": "agent.phase", "phase": "speaking"})
    for envelope in sent:
        Envelope.model_validate(envelope)
    snap = Snapshot.model_validate(feed.snapshot())
    assert (snap.attention, snap.agent.phase, snap.link.down) == ("phone_use", "speaking", "media_drop")


@pytest.mark.parametrize("path", recorded_logs() or [None], ids=lambda p: p.name if p else "no-recorded-logs")
def test_every_record_in_a_recorded_session_log_fits_the_protocol(path):
    if path is None:
        pytest.skip("no recorded logs/session-*.jsonl on this machine")
    feed = DashboardFeed(CFG)
    sent: list[dict] = []
    feed.listen(sent.append)
    for record in read(path):
        feed.on_record(record)
    for envelope in sent:
        Envelope.model_validate(envelope)
    Snapshot.model_validate(feed.snapshot())


def test_the_wire_names_are_the_enums_values_so_they_cannot_drift():
    from typing import get_args

    from sonny_focus_companion.attention import AttentionState
    from sonny_focus_companion.dashboard.protocol import AttentionStateName, EndReasonName
    from sonny_focus_companion.events import EndReason

    assert get_args(AttentionStateName) == tuple(s.value for s in AttentionState)
    assert get_args(EndReasonName) == tuple(r.value for r in EndReason)


def test_no_wire_type_shadows_the_attention_rules_detections():
    import sonny_focus_companion.dashboard.protocol as protocol

    assert not hasattr(protocol, "Detections"), (
        "sonny_focus_companion.attention.Detections is the detector's; name the envelope apart"
    )
