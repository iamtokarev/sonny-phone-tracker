"""Replay ticket 07's labelled clips from Sonny's camera through the attention rules.

Opt-in: the per-frame detections are the owner's camera data and stay out of the repo, in the gitignored `.scratch/`.
They come from `.scratch/sonny-v1/assets/27-attention/extract_detections.py`, so no detector runs here. Point
SONNY_CLIP_DETECTIONS at the file, or keep it at its default path; the tests skip when it is missing.
"""

import itertools
import json
import os
import statistics
from dataclasses import replace
from pathlib import Path

import pytest

from sonny.attention import AttentionRules, Box, Detections
from sonny.config import load_config
from sonny.events import EventType

DEFAULT_PATH = Path(__file__).parents[1] / ".scratch/sonny-v1/assets/27-attention/out/clip_detections.json"
PATH = Path(os.environ.get("SONNY_CLIP_DETECTIONS") or DEFAULT_PATH)
IN_USE = ["phone_in_hand_chest", "phone_in_hand_low", "phone_texting_two_hands", "phone_to_ear"]
NOT_IN_USE = ["phone_on_desk", "hands_near_face", "leaning_back", "working_no_phone", "empty_chair"]

pytestmark = pytest.mark.skipif(not PATH.exists(), reason=f"no clip detections at {PATH}")


def clips() -> dict[str, list[dict]]:
    return {clip["stage"]: clip["frames"] for clip in json.loads(PATH.read_text())}


def box(raw: dict | None) -> Box | None:
    return None if raw is None else Box(raw["conf"], *raw["xyxy"])


def replay(frames: list[dict]) -> list[EventType]:
    """The events one clip causes, as if a session started on its first frame."""
    # The clips were captured at 10 fps with real video freezes, so the expected rate is the median frame gap's.
    fps = 1 / statistics.median(b["t"] - a["t"] for a, b in itertools.pairwise(frames))
    cfg = load_config()
    cfg = replace(cfg, window=replace(cfg.window, expected_fps=fps))
    now = frames[0]["t"]
    rules = AttentionRules(cfg, lambda: now)
    events = []
    for f in frames:
        now = f["t"]
        events += rules.observe(Detections(f["w"], box(f["phone"]), box(f["person"])))
    return [e.type for e in events]


@pytest.mark.parametrize("stage", IN_USE)
def test_phone_in_use_starts_phone_use(stage):
    assert replay(clips()[stage])[:1] == [EventType.PHONE_USE_STARTED]


@pytest.mark.parametrize("stage", NOT_IN_USE)
def test_no_phone_use_without_the_phone_in_use(stage):
    assert replay(clips()[stage]) == []


@pytest.mark.xfail(
    strict=True,
    reason="two-handed texting holds the phone still: its centre moves 0.28-0.63% of the frame width over 10 s, "
    "inside phone.parked_motion (0.5%), so the parked guard ends Phone Use; the desk phone moves 0.11-0.15%",
)
def test_texting_holds_phone_use_to_the_end():
    assert replay(clips()["phone_texting_two_hands"]) == [EventType.PHONE_USE_STARTED]
