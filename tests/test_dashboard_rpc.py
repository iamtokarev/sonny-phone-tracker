"""The state feed on `/rpc` (spec §4): `sonny.snapshot` and `sonny.event` over Pollen's JsonRpcServer."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sonny.config import load_config
from sonny.dashboard.feed import UiFeed
from sonny.dashboard.rpc import mount_feed

CFG = load_config().dashboard


def test_a_page_gets_the_snapshot_then_each_new_envelope_as_sonny_event():
    app, feed = FastAPI(), UiFeed(CFG)
    feed.on_record({"ts": 100.0, "type": "session.started", "task": "Email", "duration_min": 10})
    mount_feed(app, feed)
    with TestClient(app) as client, client.websocket_connect("/rpc") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}})
        reply = ws.receive_json()
        assert reply["id"] == 1
        snap = reply["result"]
        assert (snap["seq"], snap["session"]["task"], snap["attention"]) == (1, "Email", "focused")

        feed.on_record({"ts": 130.0, "type": "attention", "state": "phone_use", "was": "focused"})
        note = ws.receive_json()
        assert note["method"] == "sonny.event"
        assert note["params"] == {
            "seq": 2,
            "t": 130.0,
            "type": "attention",
            "data": {"state": "phone_use", "was": "focused"},
        }
