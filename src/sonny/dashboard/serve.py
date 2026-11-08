"""What Sonny (`sonny.main`) and the dev replayer (`sonny.dashboard.dev`) share to serve the Dashboard."""

from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from sonny.config import Dashboard
from sonny.dashboard.camera import CameraFeed
from sonny.dashboard.feed import DashboardFeed
from sonny.dashboard.origin import HOST
from sonny.dashboard.rpc import mount_feed


def dashboard_url(cfg: Dashboard) -> str:
    """Where the Dashboard is served: this Mac only (ADR 0005)."""
    return f"http://{HOST}:{cfg.port}"


def announce(static_dir: Path, url: str) -> bool:
    """Print the Dashboard's URL, or how to build it when `static_dir` has no page yet; whether it is built."""
    if not (static_dir / "index.html").exists():
        logger.warning("Dashboard not built (run bun run build in web/)")
        return False
    logger.info("Dashboard: {}", url)
    return True


def mount_routes(app: FastAPI, feed: DashboardFeed, camera: CameraFeed) -> None:
    """The Dashboard's sockets: `/ws/camera` and `/rpc` (the base class serves `/` and `/static`)."""
    camera.mount(app)
    mount_feed(app, feed)
