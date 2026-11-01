"""The head owner (spec §3 head by mode, §5): one move at a time, the restore, #1453, and the move log."""

import dataclasses
import time

import pytest

from sonny.config import load_config
from sonny.head import HeadOwner
from tests.fakes import FakeRobot

CFG = load_config()


def wait_until(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


@pytest.fixture
def head():
    robot = FakeRobot()
    written: list[tuple[str, dict]] = []
    owner = HeadOwner(CFG, robot, lambda type, **f: written.append((type, f)))
    owner.start()
    yield owner, robot, written
    owner.quit()


def test_a_second_move_returns_busy_and_is_not_queued(head):
    owner, robot, _ = head
    assert owner.request("yes1") == {"playing": "yes1", "intensity": "medium"}
    owner.cue("yes1", after_line=lambda: False)
    robot.played.wait(2)
    assert "busy" in owner.request("cheerful1")
    robot.finish()
    wait_until(lambda: not owner.move_active)
    assert [c for c in robot.calls if c[0] == "play_emotion"] == [("play_emotion", "yes1")]


def play(owner: HeadOwner, name: str) -> None:
    assert "playing" in owner.request(name)
    owner.cue(name, after_line=lambda: False)


def test_the_restore_runs_when_the_daemon_fails(head):
    owner, robot, written = head
    owner.enter_session()
    wait_until(lambda: robot.names()[-1:] == ["goto"])
    robot.fail_play = OSError("daemon unreachable")
    play(owner, "reprimand1")
    wait_until(lambda: not owner.move_active)
    assert robot.calls[-1] == (
        "goto",
        0.0,
        CFG.frame.pitch_deg,
        [-CFG.head.rest_antenna_rad, CFG.head.rest_antenna_rad],
    )
    end = written[-1]
    assert end[0] == "move_end" and end[1]["name"] == "reprimand1" and not end[1]["ok"]
    assert "daemon unreachable" in end[1]["error"]


def test_a_move_logs_its_start_and_end(head):
    owner, robot, written = head
    play(owner, "yes1")
    robot.played.wait(2)
    robot.finish()
    wait_until(lambda: not owner.move_active)
    moves = [(t, f) for t, f in written if t.startswith("move_")]
    assert moves[0] == ("move_start", {"name": "yes1", "intensity": "medium", "source": "agent"})
    assert moves[1][0] == "move_end" and moves[1][1]["ok"] and moves[1][1]["error"] is None


def test_in_a_conversation_tracking_drops_to_weight_0_for_a_move_and_comes_back(head):
    owner, robot, _ = head
    owner.converse()
    wait_until(lambda: ("start_tracking", 1.0) in robot.calls)
    play(owner, "cheerful1")
    robot.played.wait(2)
    names = robot.calls[robot.calls.index(("start_tracking", 1.0)) + 1 :]
    assert names[0] == ("start_tracking", 0.0) and names[1] == ("play_emotion", "cheerful1")
    robot.finish()
    wait_until(lambda: not owner.move_active and robot.calls[-1] == ("start_tracking", 1.0))
    restore = robot.calls[robot.calls.index(("play_emotion", "cheerful1")) + 1 :]
    assert [c[0] for c in restore] == ["start_tracking", "goto", "start_tracking"]  # weight 0, neutral, weight 1


def test_a_conversation_ends_back_at_neutral_with_tracking_stopped(head):
    owner, robot, _ = head
    owner.converse()
    owner.settle(session_running=False, listening=True)
    wait_until(lambda: ("start_tracking", 1.0) in robot.calls)
    owner.settle(session_running=False, listening=False)
    wait_until(lambda: robot.names()[-1:] == ["goto"])
    after = robot.calls[robot.calls.index(("start_tracking", 1.0)) + 1 :]
    assert after == [("start_tracking", 0.0), ("stop_tracking",), ("goto", 0.0, 0.0, after[-1][3])]


def test_a_session_stops_tracking_before_returning_then_holds_the_level_pose(head):
    owner, robot, _ = head
    owner.converse()
    wait_until(lambda: ("start_tracking", 1.0) in robot.calls)
    n = len(robot.calls)
    owner.enter_session()
    assert robot.calls[n:] == [("start_tracking", 0.0), ("stop_tracking",)]  # done before it returns
    wait_until(lambda: robot.names()[-1:] == ["goto"])
    assert robot.calls[-1][1:3] == (0.0, CFG.frame.pitch_deg)
    owner.converse()  # a wake during a session leaves the head alone
    owner.settle(session_running=True, listening=True)
    assert "start_tracking" not in robot.names()[n + 2 :]


def test_after_a_session_the_head_glides_to_idle(head):
    owner, robot, _ = head
    owner.enter_session()
    owner.settle(session_running=False, listening=False)
    wait_until(lambda: robot.names().count("goto") == 2)
    assert owner.mode == "idle" and robot.calls[-1][1:3] == (0.0, 0.0)


def test_motors_down_makes_play_emotion_unavailable_without_trying_first():
    cfg = dataclasses.replace(CFG, head=dataclasses.replace(CFG.head, motors_check_s=0.05))
    robot, written = FakeRobot(), []
    owner = HeadOwner(cfg, robot, lambda type, **f: written.append((type, f)))
    robot.motors = False
    owner.start()
    time.sleep(0.2)  # a few idle checks
    assert "unavailable" in owner.request("yes1")
    robot.motors = True
    wait_until(lambda: "playing" in owner.request("perk"))
    owner.quit()
    assert "play_emotion" not in robot.names()


def test_a_move_whose_motors_drop_before_it_plays_is_skipped_not_started(head):
    owner, robot, written = head
    robot.motors = False
    play(owner, "yes1")
    wait_until(lambda: not owner.move_active)
    assert written[-1] == ("move_skipped", {"name": "yes1", "reason": "motors down"})
    assert "unavailable" in owner.request("yes1")


def test_a_granted_move_holds_no_line_until_its_cue(head):
    owner, _, _ = head
    owner.request("yes1")
    assert owner.move_active and not owner.move_cued  # a line said before the call still passes
    owner.cue("yes1", after_line=lambda: True)
    assert owner.move_cued


def test_a_move_waits_for_the_line_said_before_it(head):
    owner, robot, _ = head
    speaking = [True]
    assert "playing" in owner.request("yes1")
    owner.cue("yes1", after_line=lambda: speaking[0])
    assert not robot.played.wait(0.3)
    speaking[0] = False
    assert robot.played.wait(2)


def test_a_barge_in_cancels_a_move_that_has_not_started(head):
    owner, robot, _ = head
    owner.request("yes1")
    owner.cue("yes1", after_line=lambda: True)
    owner.cancel_pending()
    assert not owner.move_active
    assert not robot.played.wait(0.3)


def test_quit_interrupts_a_move_restores_then_sleeps():
    robot = FakeRobot()
    written: list = []
    owner = HeadOwner(CFG, robot, lambda type, **f: written.append((type, f)))
    owner.start()
    play(owner, "calming1")
    robot.played.wait(2)
    owner.quit()
    assert robot.names()[-4:] == ["stop_move", "goto", "stop_tracking", "sleep"]
    assert written[-1][1]["interrupted"] is True


def test_an_antenna_gesture_is_a_goto_sequence_ending_at_rest(head):
    owner, robot, _ = head
    play(owner, "twitch")
    wait_until(lambda: not owner.move_active)
    assert [c[1] for c in robot.calls if c[0] == "goto_antennas"] == [[0.0, 0.7], [0.0, 0.0], [0.0, 0.7], [0.0, 0.0]]
    assert robot.names()[-1] == "goto"


def test_startup_sets_the_volume_only_when_it_differs():
    robot = FakeRobot()
    robot.vol = 40
    owner = HeadOwner(CFG, robot, lambda *a, **k: None)
    owner.start()
    owner.quit()
    assert robot.calls[:2] == [("set_volume", 62), ("wake_up",)]
