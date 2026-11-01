"""The real head seam (`ReachyRobot`) against a stub daemon: the REST routes for emotions, moves, motors and
volume, and the SDK calls for tracking and poses."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from sonny.config import load_config
from sonny.daemon import DaemonClient, DaemonError
from sonny.robot import ReachyRobot

CFG = load_config()
DATASET = CFG.head.emotions_dataset


@pytest.fixture
def daemon():
    """Serves `routes` (method and path -> JSON body) and records every request."""
    routes: dict[tuple[str, str], object] = {}
    seen: list[tuple[str, str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((method, self.path, body))
            if (method, self.path) not in routes:
                self.send_error(404)
                return
            data = json.dumps(routes[(method, self.path)]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._reply("GET")

        def do_POST(self):
            self._reply("POST")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield routes, seen, DaemonClient(f"http://127.0.0.1:{server.server_port}", timeout_s=2)
    server.shutdown()


class SdkMini:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a, k))


def test_an_emotion_plays_through_the_rest_route_and_runs_while_listed(daemon):
    routes, seen, client = daemon
    routes[("POST", f"/api/move/play/recorded-move-dataset/{DATASET}/yes1")] = {"uuid": "abc"}
    routes[("GET", "/api/move/running")] = [{"uuid": "abc"}]
    robot = ReachyRobot(SdkMini(), client, DATASET)
    uuid = robot.play_emotion("yes1")
    assert uuid == "abc" and robot.move_running("abc")
    routes[("GET", "/api/move/running")] = []
    assert not robot.move_running("abc")
    routes[("POST", "/api/move/stop")] = {"message": "stopped"}
    robot.stop_move("abc")
    assert seen[-1] == ("POST", "/api/move/stop", {"uuid": "abc"})


def test_motors_are_up_only_when_the_daemon_runs_with_motors_enabled(daemon):
    routes, _, client = daemon
    robot = ReachyRobot(SdkMini(), client, DATASET)
    routes[("GET", "/api/daemon/status")] = {"state": "running", "error": None}
    routes[("GET", "/api/motors/status")] = {"mode": "enabled"}
    assert robot.motors_up()
    routes[("GET", "/api/motors/status")] = {"mode": "disabled"}
    assert not robot.motors_up()
    routes[("GET", "/api/motors/status")] = {"mode": "enabled"}
    routes[("GET", "/api/daemon/status")] = {"state": "error", "error": "Motor communication error"}
    assert not robot.motors_up()


def test_motors_are_down_when_the_daemon_is_unreachable():
    robot = ReachyRobot(SdkMini(), DaemonClient("http://127.0.0.1:9", timeout_s=0.5), DATASET)
    assert not robot.motors_up()
    with pytest.raises(DaemonError):
        robot.play_emotion("yes1")


def test_volume_reads_and_sets_through_rest(daemon):
    routes, seen, client = daemon
    routes[("GET", "/api/volume/current")] = {"volume": 40, "device": "x", "platform": "y"}
    routes[("POST", "/api/volume/set")] = {"volume": 62, "device": "x", "platform": "y"}
    robot = ReachyRobot(SdkMini(), client, DATASET)
    assert robot.volume() == 40
    robot.set_volume(62)
    assert seen[-1] == ("POST", "/api/volume/set", {"volume": 62})


def test_poses_and_tracking_go_through_the_sdk(daemon):
    mini = SdkMini()
    robot = ReachyRobot(mini, daemon[2], DATASET)
    robot.start_tracking(0.0)
    robot.stop_tracking()
    robot.goto(0.0, 5.0, [-0.17, 0.17], 1.0)
    robot.goto_antennas([0.0, 0.7], 0.15)
    robot.sleep()
    names = [c[0] for c in mini.calls]
    assert names == ["start_head_tracking", "stop_head_tracking", "goto_target", "goto_target", "goto_sleep"]
    assert mini.calls[0][2] == {"weight": 0.0}
    _, _, pose_kw = mini.calls[2]
    pitch = np.degrees(-np.arcsin(pose_kw["head"][2, 0]))
    assert round(float(pitch), 3) == 5.0 and pose_kw["antennas"] == [-0.17, 0.17] and pose_kw["duration"] == 1.0
    assert mini.calls[3][2] == {"antennas": [0.0, 0.7], "duration": 0.15, "body_yaw": None}
