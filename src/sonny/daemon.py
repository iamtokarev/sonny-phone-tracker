"""Startup checks against Sonny's daemon HTTP API (spec §2 "Startup", steps 1-2).

They run before `ReachyMini` opens, so a refusal never starts a media session.
"""

import json
import urllib.error
import urllib.request
from importlib.metadata import version
from typing import Any

# Dashboard-app states that mean it can still drive Sonny (reachy_mini.apps.manager.AppState).
LIVE_APP_STATES = {"starting", "running", "stopping"}


class StartupError(Exception):
    """A one-line reason the app refuses to start."""


class DaemonClient:
    def __init__(self, base_url: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str) -> Any:
        url = self.base_url + path
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                return json.loads(r.read() or b"null")
        except (OSError, ValueError) as e:
            reason = e.reason if isinstance(e, urllib.error.URLError) else e
            raise StartupError(f"Can't reach Sonny's daemon at {url}: {reason}") from e

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
