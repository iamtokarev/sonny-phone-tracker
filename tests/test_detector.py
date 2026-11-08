"""The detector's seam: best box per class, and the backend choice. The models themselves run only on the Mac,
in the Sonny check; ticket 07 measured them."""

import dataclasses
import threading
from collections.abc import Callable

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


def test_coreml_that_will_not_load_falls_back_to_mps(monkeypatch, logs, tmp_path):
    def broken(*_args):
        raise RuntimeError("no GPU")

    monkeypatch.setattr(detector, "CoreMLDetector", broken)
    monkeypatch.setattr(detector, "MpsDetector", Stub)
    (tmp_path / "m.mlpackage").mkdir()
    cfg = dataclasses.replace(CFG.vision, model_path=str(tmp_path / "m.mlpackage"))
    assert isinstance(load_detector(cfg), Stub)
    assert "using torch MPS fp16" in logs.text


def test_a_detector_loads_only_after_the_prewarm_imports(monkeypatch):
    """Importing rfdetr's model while prewarm imports transformers fails: its lazy imports aren't thread-safe."""
    monkeypatch.setattr(detector, "MpsDetector", Stub)
    loaded: list[object] = []
    with detector.IMPORTS:  # what `sonny.main.prewarm` holds while it imports
        t = threading.Thread(
            target=lambda: loaded.append(load_detector(dataclasses.replace(CFG.vision, backend="mps")))
        )
        t.start()
        t.join(0.2)
        assert loaded == []
    t.join(5)
    assert isinstance(loaded[0], Stub)


def test_prewarm_holds_the_import_lock_until_its_imports_finish(monkeypatch):
    from types import SimpleNamespace

    import sonny.main as main

    started: list[Callable[[], None]] = []
    fake = SimpleNamespace(Thread=lambda target, **_kw: SimpleNamespace(start=lambda: started.append(target)))
    monkeypatch.setattr(main, "threading", fake)
    main.prewarm(dataclasses.replace(CFG, vision=dataclasses.replace(CFG.vision, backend="mps")))
    assert detector.IMPORTS.locked()  # taken before the thread starts: no detector load slips in first
    started[0]()  # the thread's imports
    assert not detector.IMPORTS.locked()


def test_an_unknown_backend_is_a_config_error():
    with pytest.raises(ValueError, match=r"vision\.backend"):
        load_detector(dataclasses.replace(CFG.vision, backend="cuda"))


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_the_coreml_model_is_reloaded_in_the_background_before_the_gpu_slows_down():
    """CoreML on the GPU slows from ~15 to ~39 ms per frame about 30 s after the model loads, when frames come at
    30 fps; a fresh load is fast again. The reload runs beside detection, which keeps using the old model."""
    clock, loads, gate = Clock(), [], threading.Event()

    def load():
        if loads:
            gate.wait(5)
        loads.append(object())
        return loads[-1]

    model = detector.Refreshed(load, every_s=20, clock=clock)
    first = model.get()
    clock.t = 19
    assert model.get() is first and len(loads) == 1
    clock.t = 21
    assert model.get() is first  # the reload has started; the old model serves meanwhile
    assert model.get() is first and len(loads) == 1  # one reload at a time
    gate.set()
    model.join(5)
    assert model.get() is loads[1]
    clock.t = 40
    assert model.get() is loads[1]  # 19 s after the reload landed: not due yet


def test_a_failed_reload_keeps_the_old_model(logs):
    clock, calls = Clock(), []

    def load():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("disk gone")
        return "first"

    model = detector.Refreshed(load, every_s=20, clock=clock)
    clock.t = 21
    model.get()
    model.join(5)
    assert model.get() == "first"
    assert "reload failed" in logs.text
