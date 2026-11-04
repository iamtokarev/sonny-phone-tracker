"""The Dashboard's camera feed (spec §5): `/ws/camera` messages, latest frame wins, and no encoding nobody watches."""

import dataclasses
import json
import struct
import time

import cv2
import numpy as np
import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from sonny.attention import AttentionState, Box, Detections
from sonny.config import load_config
from sonny.dashboard.camera import CameraFeed, encode_jpeg

CFG = load_config()


def frame(w: int = 1280, h: int = 720) -> np.ndarray:
    return np.full((h, w, 3), 128, np.uint8)


def parse(message: bytes) -> tuple[dict, np.ndarray]:
    (n,) = struct.unpack("<I", message[:4])
    header = json.loads(message[4 : 4 + n])
    image = cv2.imdecode(np.frombuffer(message[4 + n :], np.uint8), cv2.IMREAD_COLOR)
    return header, image


def serve(feed: CameraFeed) -> TestClient:
    app = FastAPI()
    feed.mount(app)
    return TestClient(app)


def test_the_header_carries_the_frames_best_boxes_in_source_pixels():
    feed = CameraFeed(CFG.dashboard)
    d = Detections(1280, phone=Box(0.81, 600.5, 300, 680, 450), person=Box(0.93, 260, 0, 1020, 720))
    with serve(feed).websocket_connect("/ws/camera") as ws:
        feed.offer(frame(), 1_000.25, d, AttentionState.PHONE_USE)
        header, image = parse(ws.receive_bytes())
    assert header == {
        "seq": 1,
        "t": 1_000.25,
        "w": 1280,
        "h": 720,
        "boxes": [
            {"cls": "phone", "conf": 0.81, "x0": 600.5, "y0": 300, "x1": 680, "y1": 450},
            {"cls": "person", "conf": 0.93, "x0": 260, "y0": 0, "x1": 1020, "y1": 720},
        ],
        "attention": "phone_use",
        "watching": True,
    }
    assert image.shape == (540, 960, 3)  # sent small; the page scales the boxes from w and h


class CountingEncoder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, image, max_w, max_h, quality) -> bytes:
        self.calls += 1
        return encode_jpeg(image, max_w, max_h, quality)


def slow(fps: float = 2) -> tuple[CameraFeed, CountingEncoder]:
    """A feed whose senders wait long enough between frames that a burst of offers lands in one wait."""
    encoder = CountingEncoder()
    return CameraFeed(dataclasses.replace(CFG.dashboard, camera_fps=fps, camera_idle_fps=fps), encoder), encoder


def test_a_slow_tab_gets_the_newest_frame_never_a_backlog():
    feed, encoder = slow()
    with serve(feed).websocket_connect("/ws/camera") as ws:
        feed.offer(frame(), 1.0, Detections(1280))
        assert parse(ws.receive_bytes())[0]["seq"] == 1
        for n in range(2, 11):
            feed.offer(frame(), float(n), Detections(1280))
        assert parse(ws.receive_bytes())[0]["seq"] == 10
    assert encoder.calls == 2


def test_nothing_is_encoded_while_no_tab_is_connected():
    feed, encoder = slow()
    client = serve(feed)
    for n in range(5):
        feed.offer(frame(), float(n), Detections(1280))
    with client.websocket_connect("/ws/camera") as ws:
        assert parse(ws.receive_bytes())[0]["seq"] == 5  # a new tab starts from the newest frame
    for n in range(5, 10):
        feed.offer(frame(), float(n), Detections(1280))
    assert encoder.calls == 1


def test_two_tabs_share_one_encode_per_frame():
    feed, encoder = slow()
    client = serve(feed)
    with client.websocket_connect("/ws/camera") as a, client.websocket_connect("/ws/camera") as b:
        feed.offer(frame(), 1.0, Detections(1280))
        assert parse(a.receive_bytes())[0]["seq"] == parse(b.receive_bytes())[0]["seq"] == 1
    assert encoder.calls == 1


def test_a_paused_tab_gets_nothing_encoded_until_it_resumes():
    feed, encoder = slow()
    with serve(feed).websocket_connect("/ws/camera") as ws:
        feed.offer(frame(), 1.0, Detections(1280))
        parse(ws.receive_bytes())
        ws.send_text(json.dumps({"type": "pause"}))  # read long before the sender's next turn, 0.5 s on
        for n in range(2, 5):
            feed.offer(frame(), float(n), Detections(1280))
        time.sleep(0.7)
        assert encoder.calls == 1
        ws.send_text(json.dumps({"type": "resume"}))
        assert parse(ws.receive_bytes())[0]["seq"] == 4
    assert encoder.calls == 2


def test_outside_a_session_frames_go_raw_with_no_boxes():
    feed = CameraFeed(CFG.dashboard)
    with serve(feed).websocket_connect("/ws/camera") as ws:
        feed.offer(frame(), 5.0)
        header, _ = parse(ws.receive_bytes())
    assert (header["watching"], header["boxes"], header["attention"]) == (False, [], None)


@pytest.mark.parametrize(
    "origin",
    [
        f"http://127.0.0.1:{CFG.dashboard.port}",
        f"http://localhost:{CFG.dashboard.port}",
        "http://127.0.0.1:5173",  # the Vite dev server
        "http://localhost:5173",
    ],
)
def test_the_dashboards_own_pages_may_watch(origin):
    feed = CameraFeed(CFG.dashboard)
    with serve(feed).websocket_connect("/ws/camera", headers={"origin": origin}) as ws:
        feed.offer(frame(), 1.0)
        assert parse(ws.receive_bytes())[0]["seq"] == 1


@pytest.mark.parametrize(
    "origin", ["https://evil.example", f"http://127.0.0.1:{CFG.dashboard.port + 1}", "null", "http://127.0.0.1"]
)
def test_another_sites_page_is_refused_before_any_frame(origin):
    feed, encoder = slow()
    with pytest.raises(WebSocketDisconnect) as refused:  # noqa: SIM117
        with serve(feed).websocket_connect("/ws/camera", headers={"origin": origin}) as ws:
            feed.offer(frame(), 1.0)
            ws.receive_bytes()
    assert refused.value.code == 1008
    assert encoder.calls == 0
