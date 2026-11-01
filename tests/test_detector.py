"""The detector's seam: best box per class, and the backend choice. The models themselves run only on the Mac,
in the Sonny check; ticket 07 measured them."""

import dataclasses

import numpy as np
import pytest

import sonny.detector as detector
from sonny.attention import Box
from sonny.config import load_config
from sonny.detector import PERSON, PHONE, best_boxes, load_detector

CFG = load_config()


def test_best_boxes_keeps_the_most_confident_phone_and_person():
    xyxy = np.array([[0, 0, 10, 10], [1, 1, 5, 5], [2, 2, 6, 6], [3, 3, 9, 9], [0, 0, 1, 1]], dtype=np.float32)
    conf = np.array([0.6, 0.4, 0.9, 0.7, 0.99], dtype=np.float32)
    class_id = np.array([PERSON, PHONE, PERSON, PHONE, 62])  # 62: a tv, ignored
    d = best_boxes(xyxy, conf, class_id, 1280)
    assert d.frame_width == 1280 and not d.head_moving
    assert d.person == Box(pytest.approx(0.9), 2, 2, 6, 6)
    assert d.phone == Box(pytest.approx(0.7), 3, 3, 9, 9)


def test_no_box_of_a_class_is_none():
    d = best_boxes(np.zeros((0, 4)), np.zeros(0), np.zeros(0, dtype=int), 1280)
    assert d.phone is None and d.person is None


class Stub:
    def __init__(self, *args) -> None:
        self.args = args


def test_coreml_that_will_not_load_falls_back_to_mps(monkeypatch, capsys, tmp_path):
    def broken(*_args):
        raise RuntimeError("no GPU")

    monkeypatch.setattr(detector, "CoreMLDetector", broken)
    monkeypatch.setattr(detector, "MpsDetector", Stub)
    (tmp_path / "m.mlpackage").mkdir()
    cfg = dataclasses.replace(CFG.vision, model_path=str(tmp_path / "m.mlpackage"))
    assert isinstance(load_detector(cfg), Stub)
    assert "using torch MPS fp16" in capsys.readouterr().out


def test_an_unknown_backend_is_a_config_error():
    with pytest.raises(ValueError, match=r"vision\.backend"):
        load_detector(dataclasses.replace(CFG.vision, backend="cuda"))
