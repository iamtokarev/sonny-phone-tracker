"""RF-DETR Nano on one frame (spec §2 "Vision worker"): a BGR frame in, its best `cell phone` and `person` boxes out.

Two backends with identical detections (ticket 07): native CoreML on the GPU (~18 ms per 720p frame), and torch MPS
fp16 (~26 ms), the fallback when CoreML won't load. The CoreML model is exported from RF-DETR Nano's weights on the
first run and kept at `vision.model_path`. The heavy imports happen only when a detector loads.
"""

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
            return CoreMLDetector(_coreml_model(Path(cfg.model_path)), cfg.threshold)
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

    def __init__(self, path: Path, threshold: float) -> None:
        import coremltools as ct
        from rfdetr.export._runtime.decode import decode_detections
        from rfdetr.export._runtime.preprocess import preprocess_to_nchw

        self._decode, self._preprocess = decode_detections, preprocess_to_nchw
        self._model = ct.models.MLModel(str(path), compute_units=ct.ComputeUnit.CPU_AND_GPU)
        spec = self._model.get_spec()
        inp = spec.description.input[0]
        self._input = inp.name
        _, self._c, self._h, self._w = (int(n) for n in inp.type.multiArrayType.shape)
        self._outputs = [o.name for o in spec.description.output]
        self._threshold = threshold

    def detect(self, frame: np.ndarray) -> Detections:
        from PIL import Image

        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        out: dict[str, Any] = self._model.predict({self._input: self._preprocess(image, self._h, self._w, self._c)})
        boxes = next(n for n in self._outputs if out[n].shape[-1] == 4)  # boxes are (1, Q, 4), logits (1, Q, 91)
        logits = next(n for n in self._outputs if n != boxes)
        d = self._decode(out[boxes][0], out[logits][0], image.size, threshold=self._threshold, background_class_id=None)
        return best_boxes(d.xyxy, d.confidence, d.class_id, image.size[0])


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
