"""Sonny's daemon HTTP API: the startup checks (spec §2 "Startup", steps 1-2) and the client the head
owner's REST routes use.

The startup checks run before `ReachyMini` opens, so a refusal never starts a media session.
"""

import json
import urllib.error
import urllib.request
from importlib.metadata import version
from typing import Any

# Dashboard-app states that mean it can still drive Sonny (reachy_mini.apps.manager.AppState).
LIVE_APP_STATES = {"starting", "running", "stopping"}


class DaemonError(Exception):
    """The daemon couldn't be reached or answered with an error; one line."""


class StartupError(Exception):
    """A one-line reason the app refuses to start."""


class DaemonClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def request(self, path: str, body: Any = None, method: str | None = None) -> Any:
        """GET `path`, or POST `body` as JSON; the JSON reply. Raises DaemonError."""
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method or ("GET" if data is None else "POST"))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read() or b"null")
        except (OSError, ValueError) as e:
            reason = e.reason if isinstance(e, urllib.error.URLError) else e
            raise DaemonError(f"Can't reach Sonny's daemon at {url}: {reason}") from e

    def _get(self, path: str) -> Any:
        try:
            return self.request(path)
        except DaemonError as e:
            raise StartupError(str(e)) from e

    def status(self) -> dict[str, Any]:
        return self._get("/api/daemon/status")

    def current_app(self) -> dict[str, Any] | None:
        return self._get("/api/apps/current-app-status")


def sdk_version() -> str:
    return version("reachy_mini")


def check_startup(client: DaemonClient, sdk: str) -> None:
    """Raise StartupError on an SDK/daemon version mismatch or a live dashboard app.

    Never stops a dashboard app itself."""
    daemon = client.status().get("version") or "unknown"
    if daemon.strip() != sdk.strip():
        raise StartupError(
            f"Sonny's daemon runs reachy-mini {daemon} but this app has SDK {sdk}; install matching versions."
        )
    app = client.current_app()
    if app and app.get("state") in LIVE_APP_STATES:
        name = app.get("info", {}).get("name", "?")
        raise StartupError(f"A dashboard app is running on Sonny: stop {name} in Sonny's dashboard first.")
