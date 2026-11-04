"""Which pages may open Sonny's WebSockets (`/rpc`, `/ws/camera`).

Browsers apply no same-origin rule to WebSockets: any site open in the owner's browser could connect to
ws://127.0.0.1:<port> and read the transcript or the camera. So every socket route checks the handshake's
`Origin` before accepting, and closes with 1008 (the client sees HTTP 403) otherwise:

    if not allowed_origin(websocket, cfg.dashboard):
        await websocket.close(code=1008)
        return

Allowed: the Dashboard itself (`http://127.0.0.1:<port>`, `http://localhost:<port>`) and the Vite dev server
(the same two hosts on `dev_port`), which proxies the page's sockets with the browser's Origin. A handshake
with no `Origin` is allowed: browsers always send one, so it comes from a local non-browser client (a test,
a script), which could read the session logs on disk anyway.
"""

from starlette.websockets import WebSocket

from sonny.config import Dashboard


def dashboard_origins(cfg: Dashboard) -> frozenset[str]:
    return frozenset(
        f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (cfg.port, cfg.dev_port)
    )


def allowed_origin(websocket: WebSocket, cfg: Dashboard) -> bool:
    origin = websocket.headers.get("origin")
    return origin is None or origin in dashboard_origins(cfg)
