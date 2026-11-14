"""Sonny's head for the head owner (`sonny_focus_companion.head.Robot`): tracking and poses through the SDK, emotions,
moves, motor state and volume through the daemon REST routes (spec §5: emotions play on the daemon with
their bundled sound, so nothing streams over Wi-Fi at 100 Hz).
"""

import math
from typing import Any, Protocol
from urllib.parse import quote

from loguru import logger
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

from sonny_focus_companion.daemon import DaemonError


class Rest(Protocol):
    """The daemon's HTTP API (`sonny_focus_companion.daemon.DaemonClient`)."""

    def request(self, path: str, body: Any = None) -> Any: ...


class ReachyRobot:
    def __init__(self, mini: ReachyMini, daemon: Rest, emotions_dataset: str) -> None:
        self._mini, self._daemon, self._dataset = mini, daemon, emotions_dataset

    def wake_up(self) -> None:
        """The SDK waits only 1 s past the 2 s move for the daemon's "done". At startup the model loads and imports
        in other threads can delay that reply past the deadline although the move ran, so a late reply is not fatal."""
        try:
            self._mini.wake_up()
        except TimeoutError:
            logger.warning("The wake-up move's reply came late; carrying on.")

    def sleep(self) -> None:
        self._mini.goto_sleep()

    def start_tracking(self, weight: float) -> None:
        self._mini.start_head_tracking(weight=weight)

    def stop_tracking(self) -> None:
        self._mini.stop_head_tracking()

    def goto(
        self, yaw_deg: float, pitch_deg: float, antennas: list[float], duration_s: float, body_yaw_deg: float = 0.0
    ) -> None:
        """Blocks until the pose is reached. The head yaw is in the world frame; the body turns to `body_yaw_deg`
        (straight ahead by default), so the head ends `yaw_deg - body_yaw_deg` off the body's front."""
        head = create_head_pose(pitch=pitch_deg, yaw=yaw_deg, degrees=True)
        self._mini.goto_target(head=head, antennas=antennas, duration=duration_s, body_yaw=math.radians(body_yaw_deg))

    def aim(self, yaw_deg: float, pitch_deg: float, body_yaw_deg: float) -> None:
        """A head and body target, at once (the sweep's steps)."""
        head = create_head_pose(pitch=pitch_deg, yaw=yaw_deg, degrees=True)
        self._mini.set_target(head=head, body_yaw=math.radians(body_yaw_deg))

    def pose(self) -> tuple[float, float]:
        """The head yaw in the world and the body yaw reached, in degrees, read back from the robot."""
        head = self._mini.get_current_head_pose()
        body = self._mini.get_current_joint_positions()[0][0]
        return math.degrees(math.atan2(head[1][0], head[0][0])), math.degrees(body)

    def goto_antennas(self, antennas: list[float], duration_s: float) -> None:
        self._mini.goto_target(antennas=antennas, duration=duration_s, body_yaw=None)

    def play_emotion(self, name: str) -> str:
        reply = self._daemon.request(f"/api/move/play/recorded-move-dataset/{self._dataset}/{quote(name)}", {})
        if not isinstance(reply, dict) or "uuid" not in reply:
            raise DaemonError(f"the daemon didn't start {name}: {reply}")
        return reply["uuid"]

    def move_running(self, uuid: str) -> bool:
        return any(m.get("uuid") == uuid for m in self._daemon.request("/api/move/running") or [])

    def stop_move(self, uuid: str) -> None:
        self._daemon.request("/api/move/stop", {"uuid": uuid})

    def motors_up(self) -> bool:
        """The daemon runs without error and the motors are enabled."""
        try:
            status = self._daemon.request("/api/daemon/status") or {}
            motors = self._daemon.request("/api/motors/status") or {}
        except DaemonError:
            return False
        return status.get("state") == "running" and not status.get("error") and motors.get("mode") == "enabled"

    def volume(self) -> int:
        return int(self._daemon.request("/api/volume/current")["volume"])

    def set_volume(self, volume: int) -> None:
        self._daemon.request("/api/volume/set", {"volume": volume})
