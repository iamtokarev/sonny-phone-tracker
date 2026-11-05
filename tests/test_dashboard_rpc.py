"""The state feed on `/rpc` (spec §4): `sonny.snapshot` and `sonny.event` over Pollen's JsonRpcServer."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sonny.config import load_config
from sonny.dashboard.feed import DashboardFeed
from sonny.dashboard.rpc import mount_feed

CFG = load_config().dashboard


def test_a_page_gets_the_snapshot_then_each_new_envelope_as_sonny_event():
    app, feed = FastAPI(), DashboardFeed(CFG)
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


def snapshot_seq(client: TestClient, **headers) -> int:
    with client.websocket_connect("/rpc", headers=headers) as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}})
        return ws.receive_json()["result"]["seq"]


@pytest.mark.parametrize(
    "origin",
    [
        f"http://127.0.0.1:{CFG.port}",
        f"http://localhost:{CFG.port}",
        f"http://127.0.0.1:{CFG.dev_port}",  # the Vite dev server proxies the page's sockets
        f"http://localhost:{CFG.dev_port}",
    ],
)
def test_the_dashboard_and_its_dev_server_may_open_the_feed(origin):
    app = FastAPI()
    mount_feed(app, DashboardFeed(CFG))
    with TestClient(app) as client:
        assert snapshot_seq(client, origin=origin) == 0


def test_a_client_without_an_origin_is_not_a_browser_page_and_may_connect():
    app = FastAPI()
    mount_feed(app, DashboardFeed(CFG))
    with TestClient(app) as client:
        assert snapshot_seq(client) == 0


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", f"http://127.0.0.1:{CFG.port + 1}", f"https://127.0.0.1:{CFG.port}", "null"],
)
def test_any_other_site_is_refused_before_the_socket_opens(origin):
    app = FastAPI()
    mount_feed(app, DashboardFeed(CFG))
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect) as refused:
        snapshot_seq(client, origin=origin)
    assert refused.value.code == 1008
