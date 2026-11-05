"""The Dashboard leans on two private reachy-mini attributes; this fails loudly when an upgrade moves them.

- `JsonRpcServer._serve(websocket)` (`sonny.dashboard.rpc`): what `JsonRpcServer.mount` runs, called from Sonny's
  own `/rpc` route so it can refuse other sites' pages before the socket opens.
- `ReachyMiniApp._get_instance_path()` (`sonny.dashboard.dev`): the base class reads it to find the `static`
  folder; the dev app overrides it to serve Sonny's.

reachy-mini is pinned `==1.11.0` in pyproject.toml. On an upgrade, check both still behave as above, then update
the pin here.
"""

import inspect
from importlib.metadata import version
from pathlib import Path

from reachy_mini import ReachyMiniApp
from reachy_mini.apps.jsonrpc_server import JsonRpcServer


def test_reachy_mini_is_the_version_these_checks_were_written_against():
    assert version("reachy-mini") == "1.11.0", "re-check the private APIs below, then update this pin"


def test_jsonrpc_server_still_serves_one_accepted_socket_with__serve():
    serve = getattr(JsonRpcServer, "_serve", None)
    assert serve is not None, "JsonRpcServer._serve is gone: sonny.dashboard.rpc must serve /rpc another way"
    assert inspect.iscoroutinefunction(serve)
    assert list(inspect.signature(serve).parameters) == ["self", "websocket"]
    assert "_serve" in inspect.getsource(JsonRpcServer.mount), "mount no longer runs _serve"


def test_reachy_mini_app_still_finds_its_static_folder_with__get_instance_path():
    get = getattr(ReachyMiniApp, "_get_instance_path", None)
    assert get is not None, "ReachyMiniApp._get_instance_path is gone: sonny.dashboard.dev must find static another way"
    sig = inspect.signature(get)
    assert list(sig.parameters) == ["self"]
    assert sig.return_annotation in (Path, "Path")
    assert "_get_instance_path()" in inspect.getsource(ReachyMiniApp.__init__), "the base class no longer reads it"
