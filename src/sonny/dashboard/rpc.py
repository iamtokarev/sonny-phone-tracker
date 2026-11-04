"""The state feed on `/rpc` (spec §4): Pollen's `JsonRpcServer`, as the conversation app mounts it.

`sonny.snapshot` returns `UiFeed.snapshot()`; every new envelope goes to each connected page as a
`sonny.event` notification. Envelopes sent while no page is connected are dropped here (the snapshot's
`recent` still has them). The page only watches: no method changes what Sonny does (ADR 0005).
"""

from fastapi import FastAPI
from reachy_mini.apps.jsonrpc_server import JsonRpcServer

from sonny.dashboard.feed import UiFeed


def mount_feed(app: FastAPI, feed: UiFeed, path: str = "/rpc") -> JsonRpcServer:
    rpc = JsonRpcServer()
    rpc.register("sonny.snapshot", lambda params: feed.snapshot())
    feed.listen(lambda envelope: rpc.broadcast_threadsafe("sonny.event", envelope))
    rpc.mount(app, path)
    return rpc
