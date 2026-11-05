"""What Sonny and the dev replayer share to serve the Dashboard: its URL, the not-built notice, its routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sonny.config import load_config
from sonny.dashboard.camera import CameraFeed
from sonny.dashboard.feed import DashboardFeed
from sonny.dashboard.serve import announce, dashboard_url, mount_routes

CFG = load_config().dashboard


def test_the_dashboard_url_is_on_127_0_0_1_at_the_configured_port():
    assert dashboard_url(CFG) == f"http://127.0.0.1:{CFG.port}"


def test_announce_prints_the_url_when_built_and_how_to_build_otherwise(tmp_path, capsys):
    assert announce(tmp_path, "http://127.0.0.1:8042") is False
    assert capsys.readouterr().out == "Dashboard not built (run npm run build in web/)\n"
    (tmp_path / "index.html").write_text("<!doctype html>")
    assert announce(tmp_path, "http://127.0.0.1:8042") is True
    assert capsys.readouterr().out == "Dashboard: http://127.0.0.1:8042\n"


def test_mount_routes_serves_the_feed_and_the_camera():
    app = FastAPI()
    mount_routes(app, DashboardFeed(CFG), CameraFeed(CFG))
    with TestClient(app) as client:
        with client.websocket_connect("/rpc") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "sonny.snapshot", "params": {}})
            assert ws.receive_json()["result"]["session"] is None
        with client.websocket_connect("/ws/camera"):
            pass
