import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sonny.daemon import DaemonClient, StartupError, check_startup

SDK = "1.11.0"


@pytest.fixture
def daemon():
    """A stub daemon on localhost serving whatever `routes` holds (path -> JSON body)."""
    routes: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in routes:
                self.send_error(404)
                return
            body = json.dumps(routes[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    routes["/api/daemon/status"] = {"version": SDK, "state": "running"}
    routes["/api/apps/current-app-status"] = None
    yield routes, DaemonClient(f"http://127.0.0.1:{server.server_port}", timeout_s=2)
    server.shutdown()


def app_status(name, state):
    return {"info": {"name": name, "source_kind": "hf_space"}, "state": state, "error": None}


def test_passes_with_matching_versions_and_no_dashboard_app(daemon):
    _, client = daemon
    check_startup(client, SDK)


def test_version_mismatch_names_both_versions(daemon):
    routes, client = daemon
    routes["/api/daemon/status"] = {"version": "1.12.0"}
    with pytest.raises(StartupError) as e:
        check_startup(client, SDK)
    assert "1.12.0" in str(e.value) and SDK in str(e.value)
    assert "\n" not in str(e.value)


def test_unknown_daemon_version_is_a_mismatch(daemon):
    routes, client = daemon
    routes["/api/daemon/status"] = {"version": None}
    with pytest.raises(StartupError, match="unknown"):
        check_startup(client, SDK)


@pytest.mark.parametrize("state", ["starting", "running", "stopping"])
def test_refuses_while_a_dashboard_app_is_live(daemon, state):
    routes, client = daemon
    routes["/api/apps/current-app-status"] = app_status("marionette", state)
    with pytest.raises(StartupError, match="stop marionette in Sonny's dashboard first"):
        check_startup(client, SDK)


@pytest.mark.parametrize("state", ["done", "error"])
def test_a_finished_dashboard_app_does_not_block(daemon, state):
    routes, client = daemon
    routes["/api/apps/current-app-status"] = app_status("marionette", state)
    check_startup(client, SDK)


def test_version_is_checked_before_the_app_slot(daemon):
    routes, client = daemon
    routes["/api/daemon/status"] = {"version": "1.9.0"}
    routes["/api/apps/current-app-status"] = app_status("marionette", "running")
    with pytest.raises(StartupError, match=r"1\.9\.0"):
        check_startup(client, SDK)


def test_unreachable_daemon_is_one_line():
    client = DaemonClient("http://127.0.0.1:9", timeout_s=0.5)
    with pytest.raises(StartupError, match="Can't reach Sonny's daemon") as e:
        check_startup(client, SDK)
    assert "\n" not in str(e.value)
