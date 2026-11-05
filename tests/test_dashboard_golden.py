"""One fixture, two languages: Sonny's `DashboardFeed._apply` and the page's `applyEnvelope` (web/src/stream/state.ts)
follow the same state rules, so the same envelopes must leave both with the same snapshot.

tests/fixtures/dashboard_golden.json holds hand-written `envelopes` (a Focus Session through Phone Use and Away,
a Nudge, Pause and resume, Blind, an extend before and after time's up, agent phases, the end) and, produced by
this file, the snapshot before them (`initial`) and after chosen ones (`expected`, keyed by `seq`), without
`server_now`. This test checks Python still produces them; web/src/stream/golden.test.ts applies the same
envelopes in TypeScript and compares. Its `recent_max` is small, so both sides trim `recent` the same way.

After editing the envelopes (or a deliberate rule change), regenerate and review the diff:

    uv run python -m tests.test_dashboard_golden
"""

import dataclasses
import json
from pathlib import Path
from typing import Any

from sonny.config import load_config
from sonny.dashboard.feed import DashboardFeed
from sonny.dashboard.protocol import Envelope, Snapshot

FIXTURE = Path(__file__).with_name("fixtures") / "dashboard_golden.json"
CHECKPOINTS = (1, 4, 5, 7, 10, 11, 12, 13, 15, 17, 18, 19, 21, 22)


def run(fixture: dict[str, Any]) -> tuple[list[dict], dict[str, Any], dict[str, dict[str, Any]]]:
    """The envelopes the feed sends for the fixture's, its snapshot before them, and after each checkpoint."""
    cfg = dataclasses.replace(load_config().dashboard, recent_max=fixture["recent_max"])
    feed = DashboardFeed(cfg)
    sent: list[dict] = []
    feed.listen(sent.append)

    def snap() -> dict[str, Any]:
        s = feed.snapshot()
        del s["server_now"]
        return s

    initial, expected = snap(), {}
    for e in fixture["envelopes"]:
        feed.on_record({"ts": e["t"], "type": e["type"], **e["data"]})
        if e["seq"] in CHECKPOINTS:
            expected[str(e["seq"])] = snap()
    return sent, initial, expected


def test_the_fixtures_envelopes_are_wire_envelopes():
    fixture = json.loads(FIXTURE.read_text())
    for e in fixture["envelopes"]:
        Envelope.model_validate(e)
    assert [e["seq"] for e in fixture["envelopes"]] == list(range(1, len(fixture["envelopes"]) + 1))


def test_python_reproduces_the_golden_snapshots():
    fixture = json.loads(FIXTURE.read_text())
    sent, initial, expected = run(fixture)
    assert sent == fixture["envelopes"]
    assert initial == fixture["initial"]
    assert expected == fixture["expected"], "the state rules changed: regenerate the fixture and check the diff"
    for s in [initial, *expected.values()]:
        Snapshot.model_validate({**s, "server_now": 0.0})


def main() -> None:
    fixture = json.loads(FIXTURE.read_text())
    _, fixture["initial"], fixture["expected"] = run(fixture)
    FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n")
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
