"""Framing rules (spec §5 "In a Focus Session"): when to re-frame, by how much, and when to sweep."""

import pytest

from sonny_focus_companion.attention import Box, Detections
from sonny_focus_companion.config import load_config
from sonny_focus_companion.framing import Framing, Reframe, Sweep

CFG = load_config()
W = 1280
FPS = 10


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def person_at(cx: float, conf: float = 0.9) -> Box:
    """A person box whose centre sits at `cx` of the frame width."""
    half = 0.3 * W
    return Box(conf, cx * W - half, 0, cx * W + half, 720)


def run(framing: Framing, clock: Clock, seconds: float, person: Box | None, moving: bool = False) -> list:
    """Feed `seconds` of frames; return the moves asked for."""
    moves = []
    for _ in range(round(seconds * FPS)):
        clock.t += 1 / FPS
        if (move := framing.observe(Detections(W, person=person, head_moving=moving))) is not None:
            moves.append(move)
    return moves


@pytest.fixture
def setup():
    clock = Clock()
    return Framing(CFG, clock), clock


def test_off_centre_for_the_hold_time_asks_for_one_yaw_move_that_centres_the_box(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    moves = run(framing, clock, CFG.reframe.hold_s + 0.5, person_at(0.7))
    assert len(moves) == 1
    # 0.2 of the frame width to the right is 0.2 / 0.0082 = 24.4° of yaw; the box moves right as yaw goes negative
    assert moves[0] == Reframe(pytest.approx(-0.2 / CFG.reframe.gain_per_deg))


def test_the_move_comes_only_after_the_box_stayed_off_centre_for_the_hold_time(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    assert run(framing, clock, CFG.reframe.hold_s - 0.3, person_at(0.3)) == []
    assert run(framing, clock, 0.5, person_at(0.3)) == [Reframe(pytest.approx(0.2 / CFG.reframe.gain_per_deg))]


def test_normal_shifts_under_the_threshold_never_move_the_head(setup):
    framing, clock = setup
    assert run(framing, clock, 30, person_at(0.5 + CFG.reframe.offcentre - 0.02)) == []


def test_coming_back_to_centre_restarts_the_hold(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    run(framing, clock, CFG.reframe.hold_s - 0.5, person_at(0.75))
    run(framing, clock, 0.3, person_at(0.5))
    assert run(framing, clock, CFG.reframe.hold_s - 0.5, person_at(0.75)) == []


def test_frames_taken_during_a_head_move_restart_the_hold(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    run(framing, clock, CFG.reframe.hold_s - 0.5, person_at(0.75))
    assert run(framing, clock, 1, person_at(0.75), moving=True) == []
    assert run(framing, clock, CFG.reframe.hold_s - 0.5, person_at(0.75)) == []


def test_a_declined_reframe_waits_for_a_new_hold(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    [move] = run(framing, clock, CFG.reframe.hold_s + 0.1, person_at(0.75))  # asked for on the last frame
    framing.declined(move)  # the head was busy with an emotion move
    assert run(framing, clock, CFG.reframe.hold_s - 0.5, person_at(0.75)) == []
    assert len(run(framing, clock, 0.6, person_at(0.75))) == 1


def test_a_sweep_starts_after_the_set_time_with_no_person(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    assert run(framing, clock, CFG.sweep.after_no_person_s - 0.5, None) == []
    assert run(framing, clock, 1, None) == [Sweep()]


def test_a_sweep_starts_at_session_start_with_nobody_in_frame(setup):
    framing, clock = setup
    assert run(framing, clock, CFG.sweep.start_look_s + 0.2, None) == [Sweep()]


def test_session_start_counts_from_the_first_still_frame(setup):
    """The glide to the framing pose comes first; its frames don't count."""
    framing, clock = setup
    assert run(framing, clock, 3, None, moving=True) == []
    assert run(framing, clock, CFG.sweep.start_look_s - 0.3, None) == []
    assert run(framing, clock, 0.5, None) == [Sweep()]


def test_a_sweep_that_finds_nobody_is_not_repeated_until_someone_is_seen(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    assert run(framing, clock, CFG.sweep.after_no_person_s + 0.5, None) == [Sweep()]
    run(framing, clock, 5, None, moving=True)  # the sweep
    assert run(framing, clock, 60, None) == []
    run(framing, clock, 1, person_at(0.5))
    assert run(framing, clock, CFG.sweep.after_no_person_s + 0.5, None) == [Sweep()]


def test_after_a_sweep_the_first_person_is_framed_at_once(setup):
    """The sweep stops as the person enters the frame edge; one re-frame centres them, with no hold."""
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    run(framing, clock, CFG.sweep.after_no_person_s + 0.5, None)
    run(framing, clock, 2, person_at(0.9), moving=True)  # found mid-sweep; the head stopped past centre
    moves = run(framing, clock, 0.1, person_at(0.9))
    assert moves == [Reframe(pytest.approx(-0.4 / CFG.reframe.gain_per_deg))]
    assert run(framing, clock, CFG.reframe.hold_s + 1, person_at(0.5)) == []


def test_after_a_sweep_even_a_centred_person_gets_a_reframe_so_the_body_turns_to_face_them(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    run(framing, clock, CFG.sweep.after_no_person_s + 0.5, None)
    run(framing, clock, 1, person_at(0.505), moving=True)
    [move] = run(framing, clock, 0.1, person_at(0.505))
    assert isinstance(move, Reframe) and abs(move.yaw_deg) < 1


def test_after_a_sweep_an_unsure_person_is_not_the_owner_back(setup):
    """A weak box mid-room (conf 0.37-0.5 on Sonny) neither gets framed nor ends the absence: no second sweep."""
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    run(framing, clock, CFG.sweep.after_no_person_s + 0.5, None)
    run(framing, clock, 5, None, moving=True)  # the sweep found nobody
    unsure = person_at(0.8, conf=CFG.sweep.found_conf - 0.05)
    assert run(framing, clock, 30, unsure) == []
    assert run(framing, clock, 30, None) == []
    moves = run(framing, clock, 0.1, person_at(0.8))
    assert moves == [Reframe(pytest.approx(-0.3 / CFG.reframe.gain_per_deg))]


def test_a_declined_sweep_is_asked_for_again(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    [move] = run(framing, clock, CFG.sweep.after_no_person_s + 0.05, None)
    framing.declined(move)
    assert run(framing, clock, 0.1, None) == [Sweep()]


def test_an_unsure_person_box_is_nobody(setup):
    framing, clock = setup
    run(framing, clock, 2, person_at(0.5))
    unsure = person_at(0.5, conf=CFG.person.min_conf - 0.1)
    assert run(framing, clock, CFG.sweep.after_no_person_s + 0.5, unsure) == [Sweep()]


def test_a_reframe_that_does_not_bring_the_box_toward_centre_is_not_repeated(setup):
    """On Sonny a box stayed at x 0.33 through ~40 re-frames. If a re-frame leaves the box where it was, that box
    doesn't move with the head (or isn't the owner): framing stops chasing it until it moves or is lost."""
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    assert len(run(framing, clock, CFG.reframe.hold_s + 0.15, person_at(0.25))) == 1
    run(framing, clock, 1, person_at(0.25), moving=True)  # the move
    assert run(framing, clock, 30, person_at(0.25)) == []
    assert run(framing, clock, CFG.reframe.hold_s + 0.5, person_at(0.8)) != []  # it moved: framing again


def test_the_check_clears_once_the_box_is_lost(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    run(framing, clock, CFG.reframe.hold_s + 0.15, person_at(0.25))
    run(framing, clock, 1, person_at(0.25), moving=True)
    run(framing, clock, 5, person_at(0.25))
    run(framing, clock, 2, None)
    assert len(run(framing, clock, CFG.reframe.hold_s + 0.5, person_at(0.25))) == 1


def test_a_reframe_that_centres_the_box_keeps_framing_normal(setup):
    framing, clock = setup
    run(framing, clock, 1, person_at(0.5))
    run(framing, clock, CFG.reframe.hold_s + 0.15, person_at(0.25))
    run(framing, clock, 1, person_at(0.5), moving=True)
    run(framing, clock, 5, person_at(0.52))
    assert len(run(framing, clock, CFG.reframe.hold_s + 0.5, person_at(0.25))) == 1
