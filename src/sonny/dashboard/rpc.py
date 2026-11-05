"""The state feed on `/rpc` (spec §4): Pollen's `JsonRpcServer`, as the conversation app mounts it.

`sonny.snapshot` returns `DashboardFeed.snapshot()`; every new envelope goes to each connected page as a
`sonny.event` notification. Envelopes sent while no page is connected are dropped here (the snapshot's
`recent` still has them). The page only watches: no method changes what Sonny does (ADR 0005).

The route is Sonny's own rather than `JsonRpcServer.mount`'s, so it can refuse other sites' pages before the
socket opens (`sonny.dashboard.origin`); an accepted socket is served by the server as `mount` would.
"""

from fastapi import FastAPI, WebSocket
from reachy_mini.apps.jsonrpc_server import JsonRpcServer

from sonny.dashboard.feed import DashboardFeed
from sonny.dashboard.origin import allowed_origin


def mount_feed(app: FastAPI, feed: DashboardFeed, path: str = "/rpc") -> JsonRpcServer:
    rpc = JsonRpcServer()
    rpc.register("sonny.snapshot", lambda params: feed.snapshot())
    feed.listen(lambda envelope: rpc.broadcast_threadsafe("sonny.event", envelope))

    @app.websocket(path)
    async def _rpc(websocket: WebSocket) -> None:
        if not allowed_origin(websocket, feed.cfg):
            await websocket.close(code=1008)
            return
        # Private: what `JsonRpcServer.mount` runs (reachy-mini ==1.11.0); tests/test_reachy_private_api.py guards it.
        await rpc._serve(websocket)

    return rpc
