# R2: Vision pipeline for distraction, tracking and posture

**Researched:** 2026-10-10. **Scope:** computer-vision side of the Reachy Mini (wireless) focus companion. The Reachy Mini SDK itself is covered by another track; it is touched here only where it affects vision (frame transport, built-in face tracking, `look_at_image`).

Labels used below:
- **[verified]**: read in the primary source linked inline.
- **[estimate]**: my engineering estimate, not measured. Benchmark it before relying on it.
- **[unverified]**: from a secondary source or memory. Check it before relying on it.

---

## TL;DR

1. **Head following is already built into the SDK, so don't build it in v1.** Since about July 2026 the reachy_mini daemon runs YuNet face detection (ONNX Runtime, 1 CPU thread, 320 px input) on the robot's CM4. It adds a nearest-face tracker and an adaptive EMA smoother. You turn it on with `mini.start_head_tracking(weight=...)` and read the result with `mini.get_tracked_face()` ([source](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py), [example](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/head_tracking.py)). `weight=0.0` pauses tracking so the app can take back the head for emotion moves. If we ever need Mac-side control, `look_at_image(u, v, duration)` turns a pixel into a head pose using the calibrated K and D ([reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py)).
2. **Mac-side perception stack for v1:**
   - MediaPipe Tasks (Apache-2.0) for the face (head pose, presence), hands and pose.
   - A small COCO detector for the "cell phone" class (COCO id 67).
   - A temporal state machine on top of these signals. Don't nudge on single frames.
3. **Phone detector licensing:**
   - Ultralytics YOLO26n is the easiest phone detector (2.4M params, 40.9 mAP, Core ML export with an ANE path). It is **AGPL-3.0**, and Ultralytics claims that trained models are covered too ([license](https://www.ultralytics.com/license)).
   - For a permissive stack, use **RF-DETR Nano (Apache-2.0, 48.4 AP)** or **D-FINE-N (Apache-2.0, 4M params, 42.8 AP)**, exported to ONNX or Core ML.
   - For a private personal tool that you never distribute, AGPL is workable. Choose deliberately.
4. **Robust "phone in use" means a phone box plus hand proximity plus head pitched down, held for several seconds.** The COCO phone class alone produces false positives (a phone lying on the desk) and false negatives (a phone held below the desk edge).
5. **Looking away is relative.** The robot camera is not the screen, so "looking at screen" must be a *calibrated head-pose region*, not "facing the camera". Because the robot head moves while tracking, user head pose must be compensated for the robot's head pose, or measured only while the head is still. This is the main open design question.
6. **Posture from a front camera is weak.** Use self-calibrated relative heuristics against a baseline captured when the user says they're sitting well:
   - face size or shoulder width (leaning in)
   - nose–shoulder vertical distance (slumping)
   - shoulder tilt (sideways lean)

   Fire posture nudges rarely, after minutes of sustained deviation.
7. **Small VLMs are a fallback for ambiguous states, not a per-frame classifier.**
   - FastVLM-0.5B has a measured TTFT of 166 ms at 1024² on an M1 Max ([paper](https://arxiv.org/html/2412.13303)), but it is under the Apple research licence.
   - Qwen3-VL-2B (Apache) and SmolVLM2 / LFM2-VL-450M via `mlx-vlm` are realistic choices.
   - Call one at most every 30–60 s, and only when the rules are uncertain.
8. **Raspberry Pi-class board:** face detection (YuNet) already runs on the robot. YOLO26n reaches about 67 ms per frame on a Pi 5 with NCNN ([Ultralytics RPi guide](https://docs.ultralytics.com/guides/raspberry-pi/)), and a CM4 is slower. Keep detectors, pose models and VLMs on the Mac.

---

## Candidate model comparison

"Mac latency" means per-frame inference on an M-series Mac at batch size 1. Unless stated otherwise it is an **[estimate]** extrapolated from the cited hardware numbers.

| Model | Task | License | Size | Published latency (hardware) | Est. Mac latency | Notes |
|---|---|---|---|---|---|---|
| **YOLO26n** (Ultralytics) | Detection incl. COCO `cell phone`, `person` | **AGPL-3.0** or paid Enterprise | 2.4M params, 40.9 mAP50-95 | 38.9 ms CPU ONNX; 3.2 ms INT8 CPU+ANE on iPhone 17 Pro ([docs](https://docs.ultralytics.com/models/yolo26/), [CoreML](https://docs.ultralytics.com/integrations/coreml/)); 67 ms Pi 5 NCNN ([RPi](https://docs.ultralytics.com/guides/raspberry-pi/)) | ~3–10 ms Core ML ANE [estimate] | NMS-free head (`nms=False`). Ultralytics says trained models inherit AGPL. |
| **YOLO26s** | Same | AGPL-3.0 | 9.5M, 48.6 mAP | 87.2 ms CPU ONNX ([docs](https://docs.ultralytics.com/models/yolo26/)) | ~6–15 ms ANE [estimate] | Better recall on small or partly occluded phones. |
| **RF-DETR Nano** (Roboflow) | Detection (COCO) | **Apache-2.0** (N–L sizes) | 30.5M params, 48.4 AP, 384² input | 2.3 ms T4 TensorRT FP16 ([rfdetr docs](https://rfdetr.roboflow.com/1.6.4/learn/run/detection/), [blog](https://blog.roboflow.com/rf-detr-nano-small-medium/)) | ~15–40 ms MPS/ORT [estimate] | Best permissive accuracy. Transformer, so ANE support is uncertain. XL/2XL sizes are not Apache. |
| **D-FINE-N** | Detection (COCO) | **Apache-2.0** | 4M, 7 GFLOPs, 42.8 AP | 2.12 ms T4 TRT ([repo](https://github.com/Peterande/D-FINE)) | ~5–20 ms [estimate] | Objects365-pretrained checkpoints are "not commercially cleared" ([repo](https://github.com/Peterande/D-FINE)). Use COCO-only weights. |
| **RT-DETRv2-S** (lyuwenyu) | Detection (COCO) | Apache-2.0 | 20M, 48.1 AP | 217 FPS T4 ([repo](https://github.com/lyuwenyu/RT-DETR)) | ~15–40 ms [estimate] | The RT-DETR inside Ultralytics is AGPL. The original repo is Apache. |
| **YuNet** (OpenCV Zoo) | Face detection + 5 landmarks | MIT ([HF](https://huggingface.co/opencv/face_detection_yunet/blob/main/README.md)) | ~75k params [unverified, [Luxonis](https://models.luxonis.com/luxonis/yunet/aim_8TPr5pvDyoe5cAvrPbzNxc?backTo=%2F)] | Runs on the Reachy daemon, single thread, 320 px ([source](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_detector.py)) | <2 ms [estimate] | Already used for robot head tracking. |
| **MediaPipe Face Landmarker** | 478 3D landmarks, 52 blendshapes, face transform matrix | Apache-2.0 (code) ([repo](https://github.com/google-ai-edge/mediapipe)) | BlazeFace 192² + FaceMesh V2 256² ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)) | Not published on the page | ~3–8 ms CPU [estimate] | Head pose comes from the transform matrix, or from solvePnP on landmarks. Eye-look blendshapes give coarse gaze. |
| **MediaPipe Hand Landmarker** | 21 landmarks per hand, handedness | Apache-2.0 | palm 192² + landmarks 224² | 17.1 ms CPU / 12.3 ms GPU on Pixel 6 ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker)) | ~5–10 ms [estimate] | Gives the "phone in hand" cue together with the phone box. |
| **MediaPipe Pose Landmarker** (lite/full/heavy) | 33 body landmarks + world landmarks | Apache-2.0 | 224² detector, 256² landmarker ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker)) | Not published on the page | lite ~5–15 ms [estimate] | Lower body is not visible at a desk. Use shoulders, ears and nose only. |
| **L2CS-Net** (ResNet50) | Gaze yaw/pitch | MIT ([repo](https://github.com/Ahmednull/L2CS-Net)) | ResNet50 | n/a | ~10–30 ms MPS [estimate] | Gaze360 weights. Heavier than needed. |
| **yakhyo/gaze-estimation** MobileOne S0 | Gaze yaw/pitch | MIT ([repo](https://github.com/yakhyo/gaze-estimation)) | 4.8 MB, Gaze360 MAE 12.58° | n/a | ~2–5 ms ORT [estimate] | ONNX weights published. A ~12° error is enough for "on screen vs off". |
| **Hand-Object Detector** (100DOH) | Hand boxes + contact state incl. "portable object" | MIT ([repo](https://github.com/ddshan/hand_object_detector)) | Faster R-CNN | n/a | slow (≥100 ms) [estimate] | Research code. Useful as a teacher or labeller, not for runtime. |
| **FastVLM-0.5B** (Apple) | VLM | **apple-amlr** (research licence) ([HF](https://huggingface.co/apple/FastVLM-0.5B)) | 0.8B | TTFT 166 ms @1024², M1 Max, encoder on ANE + LLM on MLX ([paper](https://arxiv.org/html/2412.13303)) | ~0.2–0.5 s per short answer | Fastest measured on Mac. Check the licence before any distribution. MLX build: `mlx-community/FastVLM-0.5B-bf16`. |
| **SmolVLM2-500M / 2.2B** (HF) | VLM | Apache-2.0 ([HF](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct)) | 0.5B (1.8 GB GPU RAM for video) / 2.2B | 2.2B ≈ 75 tok/s generation on M2 via mlx-vlm ([Willison](https://simonwillison.net/b/8331), anecdotal) | 500M: ~0.3–0.8 s; 2.2B: ~1–2 s [estimate] | MLX builds exist (`mlx-community/SmolVLM2-2.2B-Instruct-mlx`). |
| **LFM2-VL-450M / LFM2.5-VL-450M** (Liquid) | VLM | LFM Open License v1.0 ([HF](https://huggingface.co/LiquidAI/LFM2-VL-450M)) (terms not reviewed) | 0.45B, 512² native | n/a | ~0.3–0.8 s [estimate] | MLX builds exist (`mlx-community/LFM2.5-VL-450M-6bit`). |
| **Qwen3-VL-2B-Instruct** | VLM with grounding | Apache-2.0 ([HF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)) | 2B | n/a | ~1–2 s, 4-bit MLX [estimate] | Probably the best judgment quality of the small set. `mlx-community/Qwen3-VL-2B-Instruct-4bit`. |
| **Moondream2** (2025-06-21) | VLM with detect/point | Apache-2.0 ([HF](https://huggingface.co/vikhyatk/moondream2)) | 2B | n/a | ~1–3 s MPS [estimate] | Built-in `detect("phone")` is handy. Moondream 3 uses BSL 1.1 ([HF](https://huggingface.co/moondream/moondream3-preview)). |

---

## 1. Phone-in-hand / phone-use detection

### 1.1 Object detectors (COCO "cell phone")
- COCO's `cell phone` is class **67**. `person` is 0, `laptop` 63, `remote` 65 and `book` 73 ([Ultralytics coco.yaml](https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/coco.yaml)). `remote` is a common confusion for a phone held at an angle, so consider treating it as weak phone evidence. [estimate]
- **YOLO26** is the current Ultralytics generation, released January 2026 ([docs](https://docs.ultralytics.com/models/yolo26/)):
  - n is 2.4M params with 40.9 mAP; s is 9.5M with 48.6 mAP.
  - An NMS-free one-to-one head is available via `nms=False`. It loses about 0.8 mAP.
  - Core ML export accepts `quantize=16|8|"w8a16"`.
  - When running `.mlpackage` on a Mac, Ultralytics loads it with `ComputeUnit.CPU_AND_NE`, which it reports as about 3× faster than CPU. It warns that `ALL` or `CPU_AND_GPU` can crash with coremltools 9.x ([CoreML integration](https://docs.ultralytics.com/integrations/coreml/)).
  - PyTorch on MPS is supported with `device=mps` ([same page](https://docs.ultralytics.com/integrations/coreml/)).
- **Licensing (important):**
  - Ultralytics offers AGPL-3.0 or an Enterprise licence.
  - It states that compliance means "publicly releasing the complete corresponding source code for the entire derivative work". It also says "All Ultralytics YOLO trained models fall under the AGPL-3.0 License by default", and that obligations apply "even if you … use YOLO only internally" ([ultralytics.com/license](https://www.ultralytics.com/license)).
  - That last claim is Ultralytics' interpretation and is broader than the plain AGPL text, where obligations trigger on conveying the work or offering it over a network. [unverified legal reading, not legal advice]
  - Practical implication: if this app might ever be published (for example as a Reachy Mini app on HF Spaces), use YOLO only with the whole app open-sourced under AGPL, or pick a permissive detector.
- **Permissive alternatives:**
  - **RF-DETR** Nano–Large are Apache-2.0. Nano scores 48.4 AP at 384² input and 30.5M params; the latency figure is from a T4 with TensorRT ([rfdetr docs](https://rfdetr.roboflow.com/1.6.4/learn/run/detection/)). XL/2XL use the Platform Model License.
  - **D-FINE**: Apache-2.0. N is 4M params with 42.8 AP. Avoid the Objects365-pretrained checkpoints for anything commercial ([repo](https://github.com/Peterande/D-FINE)).
  - **RT-DETRv2**: Apache-2.0 in the original repo ([repo](https://github.com/lyuwenyu/RT-DETR)).
  - All three export to ONNX, so run them with ONNX Runtime and the CoreML EP (section 6).
- **Known weaknesses for this use case** [estimate, from general experience; validate on our own footage]:
  - COCO phones are mostly seen face-on or from the side. A phone held flat, low, below the desk edge, or mostly hidden by a hand is often missed.
  - A phone lying face-down on the desk is detected but is not use.
  - The robot's wide camera (Pi Camera 3 Wide, IMX708 ([HF blog](https://huggingface.co/blog/pollen-robotics/reachy-mini-media-stack))) makes a phone at arm's length small. Run the detector at 640 px, not lower, or crop around the user's torso and hands.
  - Fine-tuning on 200–500 frames of *our* desk and phone from the robot's viewpoint would likely give the largest accuracy gain.

### 1.2 Hand–object interaction
- **Cheap fusion (recommended for v1):** MediaPipe Hand Landmarker returns 21 landmarks per hand ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker)). Score "phone in hand" when a phone box overlaps or lies within a margin of a hand's landmark hull. Add "phone in use" when the head is also pitched down or turned toward the phone. All of this is [estimate] design.
- **Learned approach:** the 100DOH Hand-Object Detector (MIT) outputs a hand contact state, including "portable object" ([repo](https://github.com/ddshan/hand_object_detector)). It is Faster R-CNN research code and too heavy for continuous use. It could auto-label training data.
- **Head-down without a visible phone** is the most common real failure, because the phone is held below the camera's view. A face pitched down for a sustained time with no laptop-typing posture is a weaker distraction signal. Route it to the VLM or to a gentle "you still with me?" check rather than a phone nudge.

---

## 2. Attention, gaze and head pose

- **MediaPipe Face Landmarker** ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker)):
  - Outputs 478 3D landmarks, 52 blendshapes and an optional facial transformation matrix (`output_facial_transformation_matrixes=True`).
  - Running modes are `IMAGE`, `VIDEO` and `LIVE_STREAM`. Built-in smoothing applies only when `num_faces=1`.
  - Left/right blendshapes are from the subject's point of view, so mirrored input swaps them.
  - The docs describe the matrix as a canonical-to-detected-face transform for effects. Using its rotation as head pose is a common practice that the docs do not state. [unverified as an official use]
  - The alternative is `cv2.solvePnP` with ~6 landmarks (nose tip, chin, eye corners, mouth corners), using the robot camera's real calibration. Reachy Mini exposes `K` and `D` because the camera is calibrated ([reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py), [HF blog](https://huggingface.co/blog/pollen-robotics/reachy-mini-media-stack)).
- **MediaPipe Python API status:**
  - Current release: `mediapipe` 1.1.0 (2026-10-06), with macOS arm64 and Linux aarch64 wheels for Python 3.9–3.14 ([PyPI](https://pypi.org/project/mediapipe/)).
  - The legacy `mp.solutions.*` API was reported missing from 0.10.31 onward ([issue #6204](https://github.com/google-ai-edge/mediapipe/issues/6204)). Use the **Tasks API** (`mediapipe.tasks.python.vision.*`) and ignore older tutorials that use `mp.solutions.face_mesh`.
- **Eye gaze:**
  - **Coarse:** the `eyeLookOut/In/Up/Down` blendshapes, or iris landmarks relative to the eye corners. Free, since the face model already runs.
  - **Learned:** L2CS-Net (MIT, ResNet50) ([repo](https://github.com/Ahmednull/L2CS-Net)), or yakhyo/gaze-estimation (MIT). Its MobileOne S0 is 4.8 MB with 12.58° MAE on Gaze360, and ONNX weights are published ([repo](https://github.com/yakhyo/gaze-estimation)).
  - At desk distance, head pose explains most "looking away" events. Eye-only glances are short and usually harmless. **v1 should use head pose only.** Add gaze in v2 if the data shows head pose is insufficient. [estimate]
- **Apple Vision framework alternative:** `VNFaceObservation` exposes `yaw`, `roll` and, on newer OS versions, `pitch` ([docs](https://developer.apple.com/documentation/vision/vnfaceobservation)). It would need pyobjc bridging. The page did not render, so the details are [unverified].
- **Defining "looking away" when the camera is not the screen:**
  - The robot sits beside or in front of the laptop. When the user looks at the screen, their face is *not* frontal to the robot.
  - Approach: a **calibration step** at session start ("look at your screen for 3 seconds"). Record the mean and spread of yaw and pitch, and treat a region of roughly ±15–20° yaw and ±15° pitch around it as "on task". Add a second "desk/notes" region if the user writes on paper. These thresholds are [estimate] and should be tuned.
  - **Robot head motion confound:** if the robot head tracks the user, camera extrinsics change, so raw camera-frame head pose drifts. Two options:
    1. Rotate the user head pose into the robot body frame using the head pose the SDK reports at frame time. This needs frame-to-pose timestamp alignment.
    2. Make head tracking very gentle or freeze the head during measurement.

    Option 1 is cleaner. Its feasibility depends on SDK timestamping, which belongs to the SDK track.
- **Prior art on durations:** Euro NCAP driver monitoring defines a "long distraction" as a single long glance away, about 3 s per a [Seeing Machines summary](https://seeingmachines.com/wp-content/uploads/2026/01/Lite-Paper_European-NCAP-Driver-State-Monitoring-Protocols-Prevalence-of-Distraction-in-Naturalistic-Driving.pdf). It also defines "visual attention time sharing" as repeated short glances that add up, and calls out phone use as a subset ([Euro NCAP protocol](https://cdn.euroncap.com/media/85854/euro-ncap-protocol-safe-driving-driver-engagement-v10.pdf)). The exact numbers were not confirmed in the primary PDF.
  - Driving tolerances are much stricter than knowledge work needs. Model the *structure* (long single glance plus cumulative short glances) and scale the durations up. See section 7.

---

## 3. Presence and person tracking (robot head follows user)

- **Already in the SDK (verified in source, added in the v1.10.0 line, "smoother face tracking", 2026-08-13 ([release list](https://github.com/pollen-robotics/reachy_mini/releases))):**
  - **Detector:** `pollen-robotics/face_detection_yunet_2026may`, a YuNet ONNX model run with ORT CPU confined to one thread "so it can't … starve the loop". Score threshold 0.6, NMS 0.3 ([face_detector.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_detector.py)).
  - **Tracker:**
    - Locks onto the largest face, which must cover at least 0.3% of the frame area.
    - Then associates the nearest face each frame, rejecting jumps above 0.5 in normalized [-1, 1] coordinates.
    - Drops the target after 20 misses.
    - Aims at the **nose**, because the eye midpoint makes the robot look slightly high.
    ([face_tracking.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py))
  - **Smoother:** an adaptive EMA with α=0.3, switching to α=0.6 when the target moves more than 0.15, plus a 0.02 dead zone ([same file](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py)).
  - **API:** `start_head_tracking(weight)` takes a blend factor: `1.0` gives tracking full control of the head, and `0.0` pauses detection without stopping the tracker. `get_tracked_face()` returns normalized x/y at about 1 Hz status broadcasts ([example](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/head_tracking.py), [doc](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/examples/head_tracking.md)).
  - The conversation app exposes this as a `head_tracking` tool ([repo](https://github.com/pollen-robotics/reachy_mini_conversation_app)).
- **Recommendation:**
  - Use daemon-side tracking for following. It runs next to the camera with no Wi-Fi round trip.
  - Set `weight=0` while playing emotion or nudge animations, then restore it.
  - Use `get_tracked_face().detected` only as a coarse presence hint. The Mac's own face and person detection is the source of truth for presence at a higher rate.
- **If Mac-side control is needed** (for example, to follow the body when the face is hidden):
  - `look_at_image(u, v, duration, perform_movement)` maps a pixel to a head pose using camera `K` and `D` ([reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py)).
  - Feed it a smoothed target: a 1€ filter or the same adaptive EMA plus a dead zone.
  - 1€ filter tuning ([Casiez et al., CHI'12](https://gery.casiez.net/1euro/), BSD Python impl): start with β=0 and min_cutoff≈1 Hz. Lower min_cutoff to remove jitter while still. Raise β (try 0.001, then scale by 10×) to cut lag on fast moves.
  - Rate-limit commands to about 5–10 Hz with `duration` around 0.2–0.3 s. The SDK's own `look_at_image` example uses `duration=0.3` ([example](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/look_at_image.py)).
- **Presence:** "user absent" means no face *and* no `person` box for N seconds, which survives the user turning around. Because of the wide FOV and robot tracking, a short absence usually means the user stood up.

---

## 4. Posture

- **MediaPipe Pose Landmarker:** 33 landmarks with image and world coordinates. Models are lite, full and heavy, with 224² detector and 256² landmarker inputs. Thresholds for detection, presence and tracking confidence default to 0.5. No latency figures are published on the page ([docs](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker)).
- **Front-facing desk heuristics.** All are relative to a per-session or per-user "good posture" baseline. Thresholds are [estimate]:
  - **Leaning in / forward head:** inter-ocular distance or face box width grows more than ~15–20% over baseline (the face got closer). Or the ratio of face width to shoulder width increases.
  - **Slumping / sinking:** the vertical distance between nose (or ears) and the shoulder midline drops more than ~15% of shoulder width below baseline.
  - **Side lean:** shoulder-line tilt or head roll more than ~8–10° from baseline, sustained.
  - **Chin-on-hand:** a hand landmark near the chin for minutes. This is also a fatigue or boredom cue.
- **Prior art:**
  - PostureFix (Mac, Python MediaPipe engine) **requires a side-on camera view** and a timed baseline calibration. It deliberately returns "no reading" rather than a wrong angle ([repo](https://github.com/adnanakil/PostureFix)).
  - SlouchDetector uses face detection only, against a calibrated baseline ([write-up](https://korben.info/en/slouchdetector-webcam-posture-reminder.html)).
  - PoseTrack (arXiv 2508.11683) uses a Raspberry Pi camera with MediaPipe Pose and works "whenever the user's joints are not blocked by the table or their limbs" ([arXiv](https://arxiv.org/html/2508.11683v1)).
- **Limitations:**
  - True neck flexion (craniovertebral angle) is a sagittal-plane measure. A frontal camera sees it only indirectly, through face size and the nose–shoulder gap.
  - The desk occludes the hips, so landmarks below the chest are unreliable.
  - A moving robot head changes the camera's viewpoint, which can make it look like the user's posture changed.
  - No front-camera MediaPipe posture method I found reports clinically validated accuracy.
- **Product guidance:** treat posture as a low-frequency, low-confidence signal. Evaluate it at 1–2 Hz, require minutes of sustained deviation, and phrase nudges softly ("quick stretch?").

---

## 5. Small VLMs as a fuzzy fallback

- **When it's worth it.** Only when rule signals conflict or are ambiguous:
  - head down with no phone seen
  - user present but turned away for more than X s
  - an unknown object in hand
  - checking whether the stated task ("reading paper notes") makes head-down normal

  Ask a constrained question with the task context. Example: *"The user said they are 'writing the report on the laptop'. Answer JSON {distracted: bool, reason: str}."*
- **Runtime:**
  - **`mlx-vlm` (MIT)** does Mac-native inference with a Python `load/generate` API and an OpenAI-compatible server (`mlx_vlm.server`) ([repo](https://github.com/Blaizzy/mlx-vlm)).
  - mlx-community hosts MLX builds of all the shortlisted models (checked via the HF API 2026-10-10): `FastVLM-0.5B-bf16`, `SmolVLM2-2.2B-Instruct-mlx`, `SmolVLM-256M-Instruct-*`, `Qwen3-VL-2B-Instruct-4bit`, `LFM2-VL-450M-*`, `LFM2.5-VL-450M-6bit`.
  - The mlx-vlm README did not explicitly list SmolVLM, FastVLM or LFM2-VL as supported. Confirm per model. [unverified]
- **Measured Mac numbers (the few that exist):**
  - **FastVLM:** on an M1 Max with 32 GB, with the vision encoder on the ANE via Core ML and the LLM on MLX ([paper](https://arxiv.org/html/2412.13303)):
    - 0.5B: TTFT 166 ms at 1024² (encoder 116 ms).
    - 1.5B: 152 ms at 768².
  - **SmolVLM-2.2B:** about 75 tok/s generation on an M2 via mlx-vlm, an anecdotal blog figure ([Willison](https://simonwillison.net/b/8331)).
  - **SmolVLM-256M:** up to 80 decode tok/s in a browser (WebGPU) on an M4 Max ([SmolVLM paper](https://arxiv.org/html/2504.05299)).
  - A short JSON answer is about 20 tokens. So realistic per-call latency is about **0.2–0.6 s for 0.5B-class** and **~1–2 s for 2B-class** models at modest image size (512–768 px). [estimate]
- **Licences:**
  - Apache-2.0: SmolVLM2, Qwen3-VL-2B and Moondream2.
  - FastVLM uses the `apple-amlr` research licence. Check it before distribution.
  - Moondream 3 Preview is BSL 1.1 with a no-competing-hosted-service grant ([HF](https://huggingface.co/moondream/moondream3-preview)).
  - LFM2-VL uses the LFM Open License v1.0 (terms not reviewed).
- **Recommendation:**
  - Default to **Qwen3-VL-2B-Instruct 4-bit** for judgment quality, or **SmolVLM2-500M / LFM2.5-VL-450M** if latency or memory matters more.
  - Keep FastVLM as the speed option for personal use.
  - Budget at most one call per 30–60 s, plus one at "suspect → distracted" transitions. Never call per frame. Never send frames off-device.
- **Caution:** the SmolVLM2 model card lists "unauthorized surveillance" and scoring individuals as out-of-scope uses ([HF](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct)). Self-monitoring with consent is fine, but keep it single-user and local.

---

## 6. Runtime on macOS (Apple Silicon) and on the robot

- **Frame source:**
  - The wireless robot H.264-encodes the camera over WebRTC. The example pipeline is 1280×720 at 60 fps (the sensor can do 1080p60).
  - Glass-to-glass latency measured about 100 ms on same-LAN 5 GHz Wi-Fi.
  - Apps running *on* the robot get raw BGR frames over IPC.

  ([HF media-stack blog](https://huggingface.co/blog/pollen-robotics/reachy-mini-media-stack))

  For distraction detection, analysing **5–10 fps** at 720p is plenty. [estimate]
- **Options on the Mac:**

  | Path | Best for | Notes |
  |---|---|---|
  | **Core ML (`.mlpackage`, ANE)** | YOLO26 and other CNN detectors | Ultralytics uses `CPU_AND_NE` on Mac, about 3× faster than CPU, and warns about a coremltools 9.x crash with `ALL`/`CPU_AND_GPU` ([docs](https://docs.ultralytics.com/integrations/coreml/)). Published Apple number: 3.2 ms YOLO26n INT8 on an A19 Pro. |
  | **ONNX Runtime + CoreML EP** | RF-DETR, D-FINE, gaze ONNX, YuNet | Options are `ModelFormat=MLProgram` (more ops), `MLComputeUnits=ALL/CPUAndNeuralEngine/CPUAndGPU`, `RequireStaticInputShapes=1` for speed, and `ModelCacheDirectory` to skip recompiles. Unsupported ops fall back to CPU ([ORT docs](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)). Transformer detectors may partly fall back. Measure it. |
  | **PyTorch MPS** | Prototyping (Ultralytics `device=mps`, RF-DETR) | Easiest, usually slower than Core ML for small CNNs. [estimate] |
  | **MediaPipe Tasks (CPU/XNNPACK)** | Face, hand, pose | Already fast on CPU. Run in `VIDEO` mode in our own loop to keep control of timing. |
  | **MLX (mlx-vlm)** | VLMs | Unified memory and quantized weights ([repo](https://github.com/Blaizzy/mlx-vlm)). |

- **Overall budget on an M-series Mac at 10 fps** [estimate]:
  - Face: about 5 ms.
  - Hands: about 8 ms.
  - Phone detector: about 5–30 ms.
  - Pose: about 10 ms at a reduced rate.

  That is well under 100 ms per frame, which leaves headroom for STT, TTS and the LLM.
- **Robot board (Raspberry Pi CM4, 4 GB RAM ([Seeed wiki](https://wiki.seeedstudio.com/reachymini_platforms_reachy_mini_hardware/), [CNX](https://www.cnx-software.com/2026/03/24/hugging-face-reachy-mini-open-source-ai-robot-computer-raspberry-pi-cm4/?amp=1))):**
  - YuNet face tracking already runs there.
  - YOLO26n on a *Pi 5* is 67 ms with NCNN and 126 ms with ONNX at 640² ([Ultralytics RPi](https://docs.ultralytics.com/guides/raspberry-pi/)). A CM4 (Cortex-A72) is materially slower, likely 2–3× [estimate], and would compete with the daemon for CPU.
  - Verdict: on the robot, keep only the existing face tracking and perhaps a very low-rate (≤1 fps) phone check as a privacy-max mode. Everything else belongs on the Mac.

---

## 7. Robustness: smoothing, debouncing, state machine

- **Per-frame → per-second evidence:**
  - For each signal (`phone_in_hand`, `head_off_task`, `absent`, `bad_posture`), compute an exponentially weighted ratio or a sliding-window fraction over the last W seconds.
  - Use **hysteresis**: enter at ≥0.7, exit at ≤0.3 ([estimate], the standard Schmitt-trigger pattern).
- **Suggested v1 thresholds.** All are [estimate] starting points. Durations are scaled up from the driver-monitoring structure in section 2 and should be tuned from logs:

  | Signal | Enter state when | Nudge |
  |---|---|---|
  | Phone in use (phone box ∩ hand, or phone + head-down) | ≥60% of frames over 8 s | Voice + "disappointed" emotion. Escalate if it persists 60 s. |
  | Head off the calibrated task region | Single episode ≥ 20–30 s, *or* cumulative ≥ 60 s within 3 min (the time-sharing analogue) | Gentle glance or antenna wiggle first, voice second. |
  | Absent (no face and no person) | ≥ 60 s | None while absent. Greet on return. Pause the session timer after 5 min absent. |
  | Bad posture vs baseline | ≥ 2–3 min sustained | Soft voice, at most once per 15 min. |

- **Global anti-annoyance rules** [estimate]:
  - Cooldown of 3–5 min after any nudge.
  - Never nudge in the first 60 s of a session or within 30 s of a user utterance.
  - Escalation ladder: look → sound → voice → firmer voice.
  - User override ("I'm on a call / reading on my phone") suppresses the relevant detector for the stated duration.
  - Log every nudge with the evidence so thresholds can be tuned offline.
- **States:** `CALIBRATING → FOCUSED ⇄ SUSPECT → DISTRACTED → NUDGED → COOLDOWN → FOCUSED`, plus `AWAY` and `PAUSED`. The VLM is consulted only on `SUSPECT` when the cues are ambiguous.
- **Tracking smoothness:** the SDK's adaptive EMA plus dead zone ([source](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py)), or the 1€ filter ([site](https://gery.casiez.net/1euro/)).

### Existing projects worth borrowing from
- **reachy_phone_home**: a phone focus companion for Reachy Mini.
  - Uses YOLO26 for phone and person detection, `--conf` (example 0.15) and `--process-every`.
  - Movements trigger after N "heartbeats".
  - The app is Apache-2.0, but it notes that the Ultralytics dependency is AGPL ([README](https://mmrech-reachy-phone-home.static.hf.space/README.md)).
  - Closest prior art: read its code for the heartbeat or debounce logic and its Reachy app packaging.
- **pollen-robotics/reachy_mini `vision/`**: face detector, tracker and smoother (Apache-2.0 SDK) ([dir](https://github.com/pollen-robotics/reachy_mini/tree/main/src/reachy_mini/vision)).
- **adityakakarla/focus-tracker**: fine-tuned YOLOv5 phone detector with escalating punishments. AGPL-3.0, stale for 2+ years ([via search](https://science.ecosyste.ms/projects/25854)) [unverified details].
- **Lock In**: browser webcam coach that detects phone pick-ups on-device ([repo](https://github.com/kumarankit512/lock-in)) [not reviewed in depth].
- **infinity811/focus-monitor**: Chrome extension that tracks attention from facial landmarks ([repo](https://github.com/infinity811/focus-monitor)) [not reviewed in depth].
- **PostureFix**: local Mac posture tool with calibration and "no reading" behaviour ([repo](https://github.com/adnanakil/PostureFix)).

---

## Recommended v1 pipeline

```
Robot (CM4)                                Mac (Apple Silicon), all local
-----------                                -------------------------------
camera ─► daemon YuNet face tracking       WebRTC frames 720p ─► sample 8–10 fps
          (start_head_tracking(1.0);        ├─ MediaPipe FaceLandmarker (VIDEO)  → presence, head yaw/pitch (solvePnP w/ K,D)
           weight=0 during emotes)          ├─ MediaPipe HandLandmarker (2 hands) → hand hulls
                                            ├─ Phone detector @640 (5 fps):
                                            │    v1 personal: YOLO26n Core ML (AGPL)
                                            │    permissive: RF-DETR-N / D-FINE-N via ORT+CoreML EP
                                            ├─ MediaPipe Pose lite (1–2 Hz)       → shoulders/nose/ears for posture
                                            ├─ Fusion → per-signal windowed scores (hysteresis)
                                            ├─ Focus state machine (thresholds §7, cooldowns)
                                            └─ On SUSPECT+ambiguous: VLM via mlx-vlm (≤1 call/30–60 s)
                                                 Qwen3-VL-2B-4bit or SmolVLM2-500M / LFM2.5-VL-450M
```

- **Calibration at session start (~5 s):** "look at your screen" gives the on-task head-pose region. "Sit how you'd like to sit" gives the posture baseline.
- **Privacy:** frames stay on the LAN (peer-to-peer WebRTC ([blog](https://huggingface.co/blog/pollen-robotics/reachy-mini-media-stack))). Store no frames by default. Optionally keep an opt-in debug ring buffer of the last 10 s around each nudge for tuning.
- **Build order:**
  1. Presence and head-pose looking-away.
  2. Phone detection plus hand fusion.
  3. The state machine and logging.
  4. Posture.
  5. The VLM fallback.

## Open questions

1. **Licence choice:** is this ever going to be published (HF Space / Reachy app store)? If yes, avoid Ultralytics, or AGPL the whole app.
2. **Robot-head compensation:** can we get the robot head pose timestamped to each WebRTC frame, so user head pose can be expressed in the robot's body frame? This depends on the SDK track. If not, should head tracking be gentle or paused during measurement windows?
3. **Will daemon face tracking and Mac-side analysis conflict** (CPU on the CM4, or the head moving while we measure)? Measure the CM4's CPU headroom with tracking on.
4. **Robot placement relative to screen and user** (beside the laptop vs in front) drives detection geometry. Phones held low may be out of frame. Pick a placement and record test footage.
5. **COCO phone recall** at this viewpoint and distance is unmeasured. We need a small labelled clip set (phone in hand, on desk, face-down, under desk) to choose between YOLO26n, RF-DETR-N and D-FINE-N, and to decide whether to fine-tune.
6. **Real Mac latencies** for each model on the user's specific chip. Every Mac number here is an estimate except FastVLM's.
7. **VLM licence and support:** confirm mlx-vlm support for FastVLM, SmolVLM2 and LFM2.5-VL. Read the LFM Open License and apple-amlr terms.
8. **Is MediaPipe's facial transformation matrix a reliable head-pose source** compared with solvePnP using the real camera intrinsics? Quick A/B.
9. **Task-aware rules:** for tasks like "reading on my phone" or "handwriting notes", the user's stated task should switch detectors off or redefine on-task regions. Who parses that, the LLM layer or a fixed list?

## Sources

- Ultralytics YOLO26 docs: https://docs.ultralytics.com/models/yolo26/
- Ultralytics Core ML integration: https://docs.ultralytics.com/integrations/coreml/
- Ultralytics Raspberry Pi guide: https://docs.ultralytics.com/guides/raspberry-pi/
- Ultralytics licence page: https://www.ultralytics.com/license
- Ultralytics COCO config: https://github.com/ultralytics/ultralytics/blob/main/ultralytics/cfg/datasets/coco.yaml
- RF-DETR docs: https://rfdetr.roboflow.com/1.6.4/learn/run/detection/ ; blog: https://blog.roboflow.com/rf-detr-nano-small-medium/
- D-FINE: https://github.com/Peterande/D-FINE
- RT-DETR (original): https://github.com/lyuwenyu/RT-DETR
- YuNet (OpenCV Zoo on HF): https://huggingface.co/opencv/face_detection_yunet/blob/main/README.md ; Pollen copy: https://huggingface.co/pollen-robotics/face_detection_yunet_2026may
- MediaPipe repo: https://github.com/google-ai-edge/mediapipe ; PyPI: https://pypi.org/project/mediapipe/ ; solutions removal issue: https://github.com/google-ai-edge/mediapipe/issues/6204
- MediaPipe Face Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
- MediaPipe Hand Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker
- MediaPipe Pose Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker
- L2CS-Net: https://github.com/Ahmednull/L2CS-Net
- yakhyo/gaze-estimation: https://github.com/yakhyo/gaze-estimation
- Hand-Object Detector (100DOH): https://github.com/ddshan/hand_object_detector
- Apple Vision VNFaceObservation: https://developer.apple.com/documentation/vision/vnfaceobservation
- Reachy Mini SDK `look_at_image`: https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py
- Reachy Mini face detector / tracker: https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_detector.py , https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py
- Reachy Mini head-tracking example and doc: https://github.com/pollen-robotics/reachy_mini/blob/main/examples/head_tracking.py , https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/examples/head_tracking.md
- Reachy Mini releases: https://github.com/pollen-robotics/reachy_mini/releases
- Reachy Mini conversation app: https://github.com/pollen-robotics/reachy_mini_conversation_app
- Reachy Mini media stack blog: https://huggingface.co/blog/pollen-robotics/reachy-mini-media-stack
- Reachy Mini hardware: https://wiki.seeedstudio.com/reachymini_platforms_reachy_mini_hardware/ ; https://www.cnx-software.com/2026/03/24/hugging-face-reachy-mini-open-source-ai-robot-computer-raspberry-pi-cm4/?amp=1
- reachy_phone_home README: https://mmrech-reachy-phone-home.static.hf.space/README.md
- ONNX Runtime CoreML EP: https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html
- mlx-vlm: https://github.com/Blaizzy/mlx-vlm
- FastVLM paper: https://arxiv.org/html/2412.13303 ; model card: https://huggingface.co/apple/FastVLM-0.5B
- SmolVLM paper: https://arxiv.org/html/2504.05299 ; SmolVLM2-500M card: https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct ; Willison note: https://simonwillison.net/b/8331
- Qwen3-VL-2B: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- LFM2-VL-450M: https://huggingface.co/LiquidAI/LFM2-VL-450M
- Moondream2: https://huggingface.co/vikhyatk/moondream2 ; Moondream 3 preview: https://huggingface.co/moondream/moondream3-preview
- 1€ filter: https://gery.casiez.net/1euro/
- Euro NCAP driver engagement protocol v1.0: https://cdn.euroncap.com/media/85854/euro-ncap-protocol-safe-driving-driver-engagement-v10.pdf ; Seeing Machines summary: https://seeingmachines.com/wp-content/uploads/2026/01/Lite-Paper_European-NCAP-Driver-State-Monitoring-Protocols-Prevalence-of-Distraction-in-Naturalistic-Driving.pdf
- PostureFix: https://github.com/adnanakil/PostureFix ; SlouchDetector: https://korben.info/en/slouchdetector-webcam-posture-reminder.html ; PoseTrack: https://arxiv.org/html/2508.11683v1
- Focus/phone projects: https://github.com/kumarankit512/lock-in , https://github.com/infinity811/focus-monitor , https://science.ecosyste.ms/projects/25854
