"""The Dashboard's camera feed (spec §5): what Sonny's camera sees, with the boxes the attention rules read.

- **In.** The vision worker `offer`s every new frame: in a Focus Session the detected frame with its best `phone`
  and `person` boxes and the Attention State; otherwise the raw frame with `watching: false` and no boxes (the
  detector never runs for the page). An offer only fills a one-slot mailbox, so it costs the vision thread nothing.
- **Out.** `/ws/camera`, binary messages `u32 header_len (little-endian) | JSON header | JPEG`, the header being

      {"seq": int, "t": float, "w": int, "h": int,
       "boxes": [{"cls": "phone" | "person", "conf": float, "x0": float, "y0": float, "x1": float, "y1": float}],
       "attention": "focused" | "phone_use" | "away" | null, "watching": bool}

  `seq` counts offered frames from 1 (gaps are skipped frames), `t` is the frame's arrival time (epoch s, the
  same Mac's clock), `w` and `h` are the source frame's size. Boxes are in source-frame pixels; the JPEG is scaled
  to fit `camera_width` x `camera_height`, so the page scales the boxes by its own width over `w`.
- **Latest frame wins.** Each client's sender takes the newest frame after each send, at most `camera_fps` a
  second while watching and `camera_idle_fps` otherwise; a slow tab skips frames instead of queueing them.
- **Encode only for watchers.** JPEGs are made only for a connected, unpaused client, off the event loop
  (`asyncio.to_thread`), once per frame however many tabs are open.
- **Privacy.** The page sends the text message `{"type": "pause"}` (or closes the socket) and nothing more is
  encoded or sent to it until `{"type": "resume"}`. Sonny's watching is unchanged.
"""

import asyncio
import json
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from sonny.attention import AttentionState, Box, Detections
from sonny.config import Dashboard

Encode = Callable[[np.ndarray, int, int, int], bytes]


def encode_jpeg(image: np.ndarray, max_w: int, max_h: int, quality: int) -> bytes:
    """A BGR frame as a JPEG scaled to fit `max_w` x `max_h` (never up)."""
    h, w = image.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return jpeg.tobytes()


@dataclass(frozen=True)
class _Frame:
    seq: int
    t: float
    image: np.ndarray
    header: dict


class CameraFeed:
    def __init__(self, cfg: Dashboard, encode: Encode = encode_jpeg) -> None:
        self.cfg = cfg
        self._encode = encode
        self._lock = threading.Lock()  # the mailbox and the client list: the vision thread offers, the loop sends
        self._latest: _Frame | None = None
        self._seq = 0
        self._clients: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()
        self._encoded: tuple[int, bytes] | None = None  # the last message made, shared by every client
        self._encoding = threading.Lock()  # one encode per frame across clients

    def offer(
        self,
        image: np.ndarray,
        t: float,
        detections: Detections | None = None,
        attention: AttentionState | None = None,
    ) -> None:
        """The newest camera frame; `detections` when the detector ran on it (a Focus Session), else None.
        Thread-safe and cheap: it replaces the mailbox's frame and wakes the senders."""
        boxes = []
        if detections is not None:
            boxes = [_box(c, b) for c, b in (("phone", detections.phone), ("person", detections.person)) if b]
        with self._lock:
            self._seq += 1
            h, w = image.shape[:2]
            header = {
                "seq": self._seq,
                "t": t,
                "w": w,
                "h": h,
                "boxes": boxes,
                "attention": None if attention is None else str(attention),
                "watching": detections is not None,
            }
            self._latest = _Frame(self._seq, t, image, header)
            clients = list(self._clients)
        for loop, wake in clients:
            loop.call_soon_threadsafe(wake.set)

    def mount(self, app: FastAPI) -> None:
        app.add_api_websocket_route("/ws/camera", self.serve)

    async def serve(self, ws: WebSocket) -> None:
        if not _allowed_origin(ws, self.cfg.port):
            await ws.close(code=1008)  # policy violation: another site's page in the owner's browser
            return
        await ws.accept()
        wake = asyncio.Event()
        client = (asyncio.get_running_loop(), wake)
        paused = asyncio.Event()  # set while the page has paused its feed
        with self._lock:
            self._clients.add(client)
        if self._latest is not None:
            wake.set()
        sender = asyncio.create_task(self._send(ws, wake, paused))
        try:
            while True:
                kind = _message_type(await ws.receive_text())
                if kind == "pause":
                    paused.set()
                elif kind == "resume":
                    paused.clear()
                    wake.set()
        except WebSocketDisconnect:
            pass
        finally:
            with self._lock:
                self._clients.discard(client)
            sender.cancel()

    async def _send(self, ws: WebSocket, wake: asyncio.Event, paused: asyncio.Event) -> None:
        sent_seq, sent_at = 0, float("-inf")
        try:
            while True:
                await wake.wait()
                wake.clear()
                frame = self._latest
                if paused.is_set() or frame is None or frame.seq == sent_seq:
                    continue
                fps = self.cfg.camera_fps if frame.header["watching"] else self.cfg.camera_idle_fps
                if (wait := 1 / fps - (time.monotonic() - sent_at)) > 0:
                    await asyncio.sleep(wait)
                    frame = self._latest  # the newest by now
                    if paused.is_set() or frame is None:
                        continue
                await ws.send_bytes(await self._message(frame))
                sent_seq, sent_at = frame.seq, time.monotonic()
        except (WebSocketDisconnect, RuntimeError):
            pass  # the page went away mid-send; `serve` cleans up

    async def _message(self, frame: _Frame) -> bytes:
        return await asyncio.to_thread(self._made, frame)

    def _made(self, frame: _Frame) -> bytes:
        """The message for `frame`, encoded once however many clients ask (a worker thread, never the loop)."""
        with self._encoding:
            if self._encoded is None or self._encoded[0] != frame.seq:
                cfg = self.cfg
                jpeg = self._encode(frame.image, cfg.camera_width, cfg.camera_height, cfg.camera_jpeg_quality)
                header = json.dumps(frame.header).encode()
                self._encoded = (frame.seq, struct.pack("<I", len(header)) + header + jpeg)
            return self._encoded[1]


DEV_PORT = 5173  # the Vite dev server (spec §3), which proxies /ws to Sonny


def _allowed_origin(ws: WebSocket, port: int) -> bool:
    """Browsers let any site open a WebSocket to 127.0.0.1, so a browser's `Origin` must be the Dashboard's own
    (or the Vite dev server's). No `Origin` at all is a local non-browser client, which no header check stops."""
    origin = ws.headers.get("origin")
    if origin is None:
        return True
    hosts = ("127.0.0.1", "localhost")
    return origin in {f"http://{h}:{p}" for h in hosts for p in (port, DEV_PORT)}


def _box(cls: str, b: Box) -> dict:
    return {"cls": cls, "conf": b.conf, "x0": b.x0, "y0": b.y0, "x1": b.x1, "y1": b.y1}


def _message_type(text: str) -> str | None:
    try:
        message = json.loads(text)
    except ValueError:
        return None
    return message.get("type") if isinstance(message, dict) else None
