"""RF-DETR Nano on one frame (spec §2 "Vision worker"): a BGR frame in, its best `cell phone` and `person` boxes out.

Two backends with identical detections (ticket 07): native CoreML on the GPU (~18 ms per 720p frame), and torch MPS
fp16 (~26 ms), the fallback when CoreML won't load. The CoreML model is exported from RF-DETR Nano's weights on the
first run and kept at `vision.model_path`. The heavy imports happen only when a detector loads.

CoreML on the GPU slows from ~15 to ~39 ms per frame about 30 s after the model loads, when frames come at 30 fps
(back-to-back it stays fast; torch MPS doesn't do it). A fresh load is fast again, so the model is reloaded in the
background every `vision.reload_s`.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from sonny.attention import Box, Detections
from sonny.config import Vision

PERSON, PHONE = 1, 77  # COCO-91 ids, as RF-DETR returns them


class Detector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> Detections: ...


def best_boxes(xyxy: np.ndarray, conf: np.ndarray, class_id: np.ndarray, frame_width: int) -> Detections:
    """The most confident box of each class the attention rules read."""

    def best(cls: int) -> Box | None:
        hits = np.flatnonzero(class_id == cls)
        if not hits.size:
            return None
        i = hits[np.argmax(conf[hits])]
        return Box(float(conf[i]), *(float(v) for v in xyxy[i]))

    return Detections(frame_width, phone=best(PHONE), person=best(PERSON))


def load_detector(cfg: Vision) -> Detector:
    """The configured backend; CoreML falls back to MPS with one terminal line."""
    if cfg.backend == "coreml":
        try:
            return CoreMLDetector(_coreml_model(Path(cfg.model_path)), cfg.threshold, cfg.reload_s)
        except Exception as e:
            print(f"vision: CoreML didn't load ({type(e).__name__}: {e}); using torch MPS fp16", flush=True)
    elif cfg.backend != "mps":
        raise ValueError(f"vision.backend must be coreml or mps, not {cfg.backend!r}")
    return MpsDetector(cfg.threshold)


def _coreml_model(path: Path) -> Path:
    if path.exists():
        return path
    from rfdetr import RFDETRNano

    print(f"vision: exporting RF-DETR Nano to CoreML at {path} (first run only)...", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    exported = Path(RFDETRNano(device="cpu").export(output_dir=str(path.parent), format="coreml", verbose=False))
    if exported.resolve() != path.resolve():
        exported.rename(path)
    return path


class CoreMLDetector:
    name = "CoreML on GPU"

    def __init__(self, path: Path, threshold: float, reload_s: float) -> None:
        import coremltools as ct
        from rfdetr.export._runtime.decode import decode_detections
        from rfdetr.export._runtime.preprocess import preprocess_to_nchw

        self._decode, self._preprocess = decode_detections, preprocess_to_nchw
        self._model = Refreshed(
            lambda: ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_GPU), reload_s
        )
        spec = self._model.get().get_spec()
        inp = spec.description.input[0]
        self._input = inp.name
        _, self._c, self._h, self._w = (int(n) for n in inp.type.multiArrayType.shape)
        self._outputs = [o.name for o in spec.description.output]
        self._threshold = threshold

    def detect(self, frame: np.ndarray) -> Detections:
        from PIL import Image

        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        out: dict[str, Any] = self._model.get().predict(
            {self._input: self._preprocess(image, self._h, self._w, self._c)}
        )
        boxes = next(n for n in self._outputs if out[n].shape[-1] == 4)  # boxes are (1, Q, 4), logits (1, Q, 91)
        logits = next(n for n in self._outputs if n != boxes)
        d = self._decode(out[boxes][0], out[logits][0], image.size, threshold=self._threshold, background_class_id=None)
        return best_boxes(d.xyxy, d.confidence, d.class_id, image.size[0])


class Refreshed[T]:
    """An object loaded again in the background once it is `every_s` old; the old one serves until the new one is
    in. A failed reload keeps the old one and tries again `every_s` later."""

    def __init__(self, load: Callable[[], T], every_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._load, self._every, self._clock = load, every_s, clock
        self._current = load()
        self._loaded_at = clock()
        self._thread: threading.Thread | None = None

    def get(self) -> T:
        if self._thread is None and self._clock() - self._loaded_at >= self._every:
            self._thread = threading.Thread(target=self._reload, name="sonny-model-reload", daemon=True)
            self._thread.start()
        return self._current

    def join(self, timeout: float) -> None:
        if (thread := self._thread) is not None:
            thread.join(timeout)

    def _reload(self) -> None:
        try:
            self._current = self._load()
        except Exception as e:
            print(f"vision: model reload failed ({type(e).__name__}: {e}); keeping the loaded one", flush=True)
        self._loaded_at = self._clock()
        self._thread = None


class MpsDetector:
    name = "torch MPS fp16"

    def __init__(self, threshold: float) -> None:
        import torch
        from rfdetr import RFDETRNano

        self._model = RFDETRNano(device="mps")
        self._model.inference(dtype=torch.float16)
        self._threshold = threshold

    def detect(self, frame: np.ndarray) -> Detections:
        d = self._model.predict(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), threshold=self._threshold)
        return best_boxes(d.xyxy, d.confidence, d.class_id, frame.shape[1])
