# R1: Reachy Mini SDK and platform

Research date: 2026-10-10. Sources: the `pollen-robotics/reachy_mini` repository (`main` at commit `fbdbca3`, 2026-10-05), its GitHub releases and issues, PyPI, the official HF docs, HF Hub datasets and Spaces. Unless noted, repo links point at `main`. The HF docs site (`huggingface.co/docs/reachy_mini`) is built from `docs/source/` in that repo.

Shorthand used below:
- `GH` = `https://github.com/pollen-robotics/reachy_mini/blob/main`
- `DOCS` = `https://huggingface.co/docs/reachy_mini`

---

## TL;DR

- **Latest stable SDK: `reachy-mini` 1.11.0, released 2026-09-11 on GitHub and uploaded to PyPI on 2026-09-21. `1.12.0rc1` came out on 2026-10-02.** Install with `uv pip install reachy-mini`. PyPI metadata says `requires-python >=3.11`, but the install guide still says "3.10 – 3.12" and recommends 3.12, so **use Python 3.12**. macOS is officially supported, and GStreamer ships as a wheel on macOS. ([PyPI](https://pypi.org/project/reachy-mini/), [releases](https://github.com/pollen-robotics/reachy_mini/releases), [DOCS/SDK/installation](https://huggingface.co/docs/reachy_mini/SDK/installation), [GH/pyproject.toml](https://github.com/pollen-robotics/reachy_mini/blob/main/pyproject.toml))
- **Architecture: a daemon on the robot plus an SDK client anywhere on the LAN.** On the Wireless, the daemon runs on the CM4 and exposes FastAPI REST plus WebSocket on `:8000`. The Python SDK drives motion over `ws://<host>:8000/ws/sdk`. Remote clients get camera and audio over **WebRTC (H.264 video, Opus audio)**, with signalling on `:8443`. The client connects to `reachy-mini.local` via mDNS. ([DOCS/SDK/core-concept](https://huggingface.co/docs/reachy_mini/SDK/core-concept), [DOCS/SDK/media-architecture](https://huggingface.co/docs/reachy_mini/SDK/media-architecture), [GH/src/reachy_mini/io/ws_client.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/io/ws_client.py))
- **The Python API covers what we need:**
  - `goto_target` / `set_target` for head (4x4 pose), antennas and body yaw
  - `look_at_image(u, v)` and `look_at_world(x, y, z)`
  - `media.get_frame()` returns BGR numpy frames, 1280x720@30 by default on the Wireless
  - `media.get_audio_sample()` and `push_audio_sample()` work on 16 kHz float32 audio
  - `media.get_DoA()` gives sound direction of arrival
  - IMU readings
  - **built-in daemon-side face tracking** via `start_head_tracking(weight)` and `get_tracked_face()`, available since v1.9.0

  ([DOCS/SDK/python-sdk](https://huggingface.co/docs/reachy_mini/SDK/python-sdk), [GH/src/reachy_mini/reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py))
- **Emotions are a recorded-move library on the HF Hub**: [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library) has about 80 moves with matching `.ogg` sounds, including `attentive1`, `impatient1`, `reprimand1-3`, `success1-2`, `rage1` and `boredom1`. You can play a move from the client with `mini.play_move(RecordedMoves(...).get("happy"))`, or **on the daemon** via REST `POST /api/move/play/recorded-move-dataset/{dataset}/{move}`.
- **Wireless compute is a Raspberry Pi CM4 (CM4104016: 4 GB RAM, 16 GB eMMC, Wi-Fi).** Pollen describes it as "limited compute". **Official Python apps installed from the app store run on the robot** in `/venvs/apps_venv`. Running the SDK on the Mac, with media over WebRTC, is officially supported for scripts, but that is not how store apps are launched. ([DOCS/platforms/reachy_mini/hardware](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/hardware), [DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps))
- **App framework: subclass `ReachyMiniApp` and implement `run(reachy_mini, stop_event)`.** Scaffold with `reachy-mini-app-assistant create`, publish as an HF Space tagged `reachy_mini_python_app`, and install from the Reachy Mini Control desktop app or the REST API. Only one app runs at a time. Pollen now **recommends JS/WebRTC apps** for new, shareable apps. ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps), [GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md))
- **Focus apps already exist (all community-made, not official):**
  - [`riyachhik/dodo_focus`](https://huggingface.co/spaces/riyachhik/dodo_focus): phone detection with MediaPipe on the Wireless, then plays `rage1`
  - [`RyeCatcher/focus_guardian`](https://huggingface.co/spaces/RyeCatcher/focus_guardian)
  - [`idormoy/focus_buddy`](https://huggingface.co/spaces/idormoy/focus_buddy)
  - [`adonaivera/reachy_mini_pomodoro`](https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro)

  These are worth studying for differentiation.
- **Gotchas:**
  - With tracking weight 1.0, the daemon **drops client head targets**.
  - Open bug [#1453](https://github.com/pollen-robotics/reachy_mini/issues/1453): after the face is lost, head targets stay ignored forever.
  - Remote WebRTC video smearing ([#1407](https://github.com/pollen-robotics/reachy_mini/issues/1407)).
  - Idle camera CPU load on the CM4 ([#1277](https://github.com/pollen-robotics/reachy_mini/issues/1277)).
  - The SDK `play_move` streams `set_target` at 100 Hz from the client, which is Wi-Fi sensitive when run from the Mac.

---

## 1. Version, install, Python, macOS

| Item | Finding | Source |
|---|---|---|
| Latest stable | **1.11.0**. GitHub release published 2026-09-11, title "Faster imports, faster boots". PyPI upload 2026-09-21. | [GH release v1.11.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.11.0), [PyPI JSON](https://pypi.org/pypi/reachy-mini/json) |
| Pre-release | **1.12.0rc1** (2026-10-02): daemon credential store, DNS-rebinding/CSRF hardening, WebRTC data-channel chunking over 64 KiB. After upgrading, a linked robot appears signed out once. | [GH release v1.12.0rc1](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.12.0rc1) |
| Previous | 1.10.0 (2026-08-13/25): JSON-RPC app control over WebRTC, cloud consumer, smoother face tracking. 1.9.0 (2026-07-09): face tracking feature, `get_frame_jpeg()`, startup app, **"Deprecate the web dashboard"** (#933). | [v1.10.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.10.0), [v1.9.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.9.0) |
| Install | `uv pip install "reachy-mini"`. Add the extras `[mujoco]` for simulation and `[central-consumer]` for the cloud aiortc consumer. Source install: `git clone … && uv sync`. | [DOCS/SDK/installation](https://huggingface.co/docs/reachy_mini/SDK/installation), [GH/pyproject.toml](https://github.com/pollen-robotics/reachy_mini/blob/main/pyproject.toml) |
| Python | PyPI and pyproject say `requires-python >=3.11`. The install doc table says "3.10 – 3.12" and recommends 3.12. **Discrepancy**: 3.10 is not installable from PyPI. 3.13 is not documented as supported (unverified). The robot's apps venv is Python 3.12 (`/venvs/apps_venv/lib/python3.12/...`). | [PyPI](https://pypi.org/project/reachy-mini/), [DOCS/SDK/installation](https://huggingface.co/docs/reachy_mini/SDK/installation), [DOCS/platforms/reachy_mini/development_workflow](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/development_workflow) |
| macOS | Officially supported. `gstreamer-bundle==1.28.3` is installed automatically on non-Linux, so WebRTC works on macOS "out of the box". MuJoCo sim on macOS needs `mjpython -m reachy_mini.daemon.app.main --sim`. | [DOCS/SDK/installation](https://huggingface.co/docs/reachy_mini/SDK/installation), [DOCS/SDK/python-sdk](https://huggingface.co/docs/reachy_mini/SDK/python-sdk), [DOCS/platforms/simulation/get_started](https://huggingface.co/docs/reachy_mini/platforms/simulation/get_started) |
| Notable runtime deps | `onnxruntime==1.27.0` (face detector) and `huggingface-hub>=1.20.1`. Since 1.11, `scipy` is no longer pulled in, so declare it yourself if you need it. | [GH/pyproject.toml](https://github.com/pollen-robotics/reachy_mini/blob/main/pyproject.toml), [v1.11.0 notes](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.11.0) |

## 2. Architecture: daemon vs SDK, protocols, Wi-Fi, simulation

- **Client-server.** The daemon owns hardware I/O, safety and sensors, and "exposes a REST API and WebSocket at `http://<host>:8000`". The host is `reachy-mini.local` or the robot's IP on Wireless. The SDK "connects to the Daemon over the network… You can run your AI code on a powerful server while the Daemon runs on a Raspberry Pi". ([DOCS/SDK/core-concept](https://huggingface.co/docs/reachy_mini/SDK/core-concept))
- **SDK transport.** The Python client connects to `ws://{host}:{port}/ws/sdk` for commands and cached state ([GH/src/reachy_mini/io/ws_client.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/io/ws_client.py)). Constructor defaults are `host="reachy-mini.local"`, `port=8000` and `connection_mode="auto"`. Auto mode tries localhost first, then falls back to host:port. A non-default `robot_name` is resolved via mDNS (zeroconf). ([GH/src/reachy_mini/reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py))
- **Media transport.** The daemon always owns the camera and audio through `GstMediaServer`. ([DOCS/SDK/media-architecture](https://huggingface.co/docs/reachy_mini/SDK/media-architecture))
  - **LOCAL backend**, for a client on the same machine (the robot): frames come over a Unix-fd IPC socket, **capped at 10 fps**. This is also in code as `IPC_FPS = 10` in [GH/src/reachy_mini/media/media_server.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/media/media_server.py).
  - **WEBRTC backend**, for a remote client such as our Mac: "The daemon streams H.264 video and Opus audio over WebRTC to the client". The Python client signals to the daemon on port **8443** ([GH/src/reachy_mini/media/media_manager.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/media/media_manager.py)). The client jitter buffer is set to 10 ms ([GH/.../webrtc_client_gstreamer.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/media/webrtc_client_gstreamer.py)).
  - On the Wireless, H.264 is hardware-encoded (`v4l2h264enc`). Since 1.11, congestion control manages a bitrate of roughly 1–5 Mbps. ([v1.11.0 notes](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.11.0))
- **Browser/cloud path.** A separate path goes through a central signalling relay on an HF Space (`pollen-robotics-reachy-mini-central.hf.space`) with HF OAuth. It is used by JS apps and by `ReachyCentralConsumer`, an aiortc client for cloud backends. The relay allows only **one consumer per robot**. ([DOCS/SDK/cloud-backend-consumer](https://huggingface.co/docs/reachy_mini/SDK/cloud-backend-consumer))
- **Wi-Fi setup.** The Reachy Mini Control desktop app runs a "First time connecting…" flow: join the robot's AP, then configure home Wi-Fi. The Wireless does **not** expose the robot over USB-C to a laptop. Use Wi-Fi, or a USB-C-to-Ethernet adapter for a wired link. Conference and hotel Wi-Fi with client isolation breaks the connection. ([DOCS/platforms/reachy_mini/get_started](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started), [DOCS/troubleshooting](https://huggingface.co/docs/reachy_mini/troubleshooting))
- **Latency.** **No official latency figure for remote WebRTC frames was found (not verified).** The docs only say LOCAL "provides the lowest latency for on-device applications", and the quickstart lists "lower latency" as a benefit of running on-robot. ([DOCS/troubleshooting](https://huggingface.co/docs/reachy_mini/troubleshooting), [DOCS/SDK/quickstart](https://huggingface.co/docs/reachy_mini/SDK/quickstart))
- **Simulation.** `reachy-mini-daemon --sim` runs MuJoCo; on macOS, use `mjpython` as above. Scenes are `empty` and `minimal`. It behaves "exactly like a real Reachy Mini Lite", and software AEC replaces the XMOS chip. ([DOCS/platforms/simulation/get_started](https://huggingface.co/docs/reachy_mini/platforms/simulation/get_started)) Caveats:
  - The AGENTS.md platform table says simulation "Can't test camera features" ([GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md)).
  - Head tracking does nothing in sim ([#1451](https://github.com/pollen-robotics/reachy_mini/issues/1451)).
  - Sim camera intrinsics are wrong ([#1450](https://github.com/pollen-robotics/reachy_mini/issues/1450)).
- **SSH.** `ssh pollen@reachy-mini.local`, password `root`. ([DOCS/platforms/reachy_mini/get_started](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started))

## 3. Python API surface

All signatures below are from [GH/src/reachy_mini/reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py) unless noted.

**Connect (from the Mac to the Wireless):**
```python
from reachy_mini import ReachyMini
with ReachyMini(host="reachy-mini.local", connection_mode="network", media_backend="default") as mini:
    ...
```

**Head, antennas and body: interpolated vs streamed** ([DOCS/SDK/python-sdk](https://huggingface.co/docs/reachy_mini/SDK/python-sdk)):
```python
import numpy as np
from reachy_mini.utils import create_head_pose
mini.goto_target(head=create_head_pose(z=10, roll=15, mm=True, degrees=True),
                 antennas=np.deg2rad([45, 45]), body_yaw=np.deg2rad(30),
                 duration=2.0, method="minjerk")  # linear | minjerk | ease_in_out | cartoon
mini.set_target(head=pose_4x4, antennas=[r, l], body_yaw=yaw)  # no interpolation, for ~50-100 Hz loops
```
- `goto_target` blocks until the move completes; the client waits up to duration + 1 s.
- `goto_target(body_yaw=0.0)` defaults body yaw to 0. Pass `None` to keep the current yaw.
- `set_target_head_pose`, `set_target_antenna_joint_positions` and `set_target_body_yaw` exist as single-part setters.
- `set_automatic_body_yaw(enabled)` defaults to `True` in the constructor.

**Limits** (the SDK clamps to them): head pitch/roll ±40°, head yaw ±180°, body yaw ±160°, and at most 65° between head yaw and body yaw. ([DOCS/SDK/core-concept](https://huggingface.co/docs/reachy_mini/SDK/core-concept))

**Look-at:**
```python
mini.look_at_image(u, v, duration=0.3)    # pixel coords, top-left origin; uses camera K/D + current head pose
mini.look_at_world(x, y, z, duration=1.0) # meters, robot frame: x forward, y left, z up
```
Both accept `perform_movement=False` to only compute the pose. ([GH/examples/look_at_image.py](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/look_at_image.py))

**Camera:**
```python
frame = mini.media.get_frame()      # np.uint8 (H, W, 3)
jpeg  = mini.media.get_frame_jpeg() # since 1.9.0
```
- The Wireless camera is an RPi Camera v3 Wide (Sony IMX708, 12 MP, 120° FOV, autofocus) ([DOCS/platforms/reachy_mini/hardware](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/hardware)).
- Wireless modes ([GH/src/reachy_mini/media/camera_constants.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/media/camera_constants.py)):
  - **default 1280x720@30**, chosen "for H264 Level 3.1 compatibility (Safari/WebKit)"
  - 1920x1080@30
  - 1280x720@60
  - 3840x2592, 3840x2160, 3264x2448 and 3072x1728 at 10 fps
- On the robot, the LOCAL IPC feed is capped at 10 fps ([DOCS/SDK/media-architecture](https://huggingface.co/docs/reachy_mini/SDK/media-architecture)). Remote frames arrive via WebRTC H.264.
- Frame channel order: the docs say only "(height, width, 3) uint8". The example shows frames directly with `cv2.imshow`, which implies BGR. This is inferred, not stated.
- `media_backend="no_media"` / `release_media()` make the daemon release the camera and mic so you can use OpenCV or sounddevice directly. That only helps on the machine with the hardware attached, i.e. on the robot.

**Audio** ([DOCS/SDK/python-sdk](https://huggingface.co/docs/reachy_mini/SDK/python-sdk)):
```python
mini.media.start_recording(); mini.media.start_playing()
x = mini.media.get_audio_sample()        # float32 (n, 2) @ 16 kHz
mini.media.push_audio_sample(y)          # float32 (n, 1|2) @ 16 kHz, non-blocking
mini.media.play_sound("file.wav")
doa, speech = mini.media.get_DoA()       # 0 = left, pi/2 = front/back, pi = right
mini.enable_wobbling()                   # audio-reactive head wobble while speaking
```
- The mic array has 4 PDM MEMS mics on a ReSpeaker XMOS XVF3800 (hardware AEC), max 16 kHz. The speaker is 5 W. ([DOCS/platforms/reachy_mini/hardware](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/hardware))
- Raw 6-channel mic output needs alternate firmware ([DOCS/troubleshooting](https://huggingface.co/docs/reachy_mini/troubleshooting)).
- Remote audio uses Opus over WebRTC ([DOCS/SDK/media-architecture](https://huggingface.co/docs/reachy_mini/SDK/media-architecture)).
- The TTS example sends text to a Chatterbox HF Space and plays the result with wobbling ([GH/examples/sound_tts.py](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/sound_tts.py)).

**IMU** (Wireless only): `mini.imu` returns a dict with accelerometer, gyroscope, quaternion and temperature. ([DOCS/SDK/python-sdk](https://huggingface.co/docs/reachy_mini/SDK/python-sdk))

**Built-in face tracking (daemon-side):**
```python
mini.start_head_tracking(weight=1.0)   # 1.0 = tracking owns head; 0<w<1 blends; 0 pauses detection
face = mini.get_tracked_face()         # FaceTarget: detected, x, y in [-1,1], roll, ts (~1 Hz when polled with wait)
mini.stop_head_tracking()
```
How it works:
- YuNet runs on ONNX Runtime **inside the daemon**, with the detector input 320 px wide and limited to one CPU thread. The model is downloaded from `pollen-robotics/face_detection_yunet_2026may`. ([GH/src/reachy_mini/vision/face_detector.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_detector.py), [GH/src/reachy_mini/vision/face_tracking.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/vision/face_tracking.py))
- It targets the **closest/largest face** and aims at the nose. Since 1.11 the aim is pitched 15° down.
- An adaptive EMA filter was added in 1.10.
- After about 2 s without a face, the head recentres to neutral.

Sources: [DOCS/examples/head_tracking](https://huggingface.co/docs/reachy_mini/examples/head_tracking), [v1.10.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.10.0) and [v1.11.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.11.0) notes, and [GH/src/reachy_mini/daemon/backend/abstract.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/backend/abstract.py).

Blending detail from that code: the tracking aim is linearly interpolated with the app's target pose using `weight`. **When weight ≥ 1 and the aim is set, the head part of `set_target` is discarded**; antennas and body yaw still apply. Speech-wobble offsets are composed on top before IK.

**Motor modes:** `enable_motors()`, `disable_motors()` and `enable_gravity_compensation()`. Call `set_target` **after** `enable_motors()`. `wake_up()` and `goto_sleep()` exist. ([DOCS/SDK/core-concept](https://huggingface.co/docs/reachy_mini/SDK/core-concept), [GH/src/reachy_mini/reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py))

**State over REST/WS:** `GET /api/state/full` and `ws://<host>:8000/api/state/ws/full`. Interactive docs are at `http://<host>:8000/docs`. ([DOCS/SDK/integration](https://huggingface.co/docs/reachy_mini/SDK/integration))

## 4. Emotions, expressions and dances

- **Datasets on the HF Hub:**
  - [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library): `.json` trajectory plus `.ogg` sound per move, last modified 2026-07-07. Moves include amazed1, anxiety1, attentive1/2, boredom1/2, calming1, cheerful1, come1, confused1, curious1, displeased1/2, downcast1, enthusiastic1/2, exhausted1, frustrated1, furious1, go_away1, grateful1, helpful1/2, impatient1/2, inquiring1-3, irritated1/2, laughing1/2, lonely1, loving1, no1, oops1/2, proud1-3, rage1, relief1/2, reprimand1-3, resigned1, sad1/2, serenity1, shy1, sleep1, success1/2, surprised1/2, thoughtful1/2, tired1, toc-toc-toc, uncertain1, understanding1/2, waiting, welcoming1/2 and yes1. (File listing from the HF API.)
  - [`pollen-robotics/reachy-mini-dances-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library), plus symbolic dance code in [`pollen-robotics/reachy_mini_dances_library`](https://github.com/pollen-robotics/reachy_mini_dances_library).
- **Play from Python** ([GH/examples/recorded_moves.py](https://github.com/pollen-robotics/reachy_mini/blob/main/examples/recorded_moves.py), [GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md)):
  ```python
  from reachy_mini.motion.recorded_move import RecordedMoves
  moves = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
  mini.play_move(moves.get("impatient1"), initial_goto_duration=1.0, sound=True)
  mini.cancel_move()
  ```
  **Note:** `play_move` / `async_play_move` evaluate the trajectory **client-side** and send `set_target_*` at `play_frequency=100` Hz ([GH/src/reachy_mini/reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py)). From a Mac, that is 100 Hz of WebSocket commands over Wi-Fi.
- **Daemon-side playback:** `POST /api/move/play/recorded-move-dataset/{dataset_name}/{move_name}` returns a move UUID, `POST /api/move/stop` stops it, and `GET /api/move/recorded-move-datasets/list/{dataset}` lists moves ([GH/src/reachy_mini/daemon/app/routers/move.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/app/routers/move.py)). A WebRTC data-channel twin, `play_recorded_move`, was added in 1.10 and plays the sound "in lockstep with the motion" on the robot speaker ([v1.10.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.10.0)).
- **Blending with tracking.** The official pattern is "primary moves (emotions, dances, goto, breathing) mutually exclusive… secondary moves (face tracking, speech sync) additive offsets", with **one control loop being the only caller of `set_target` at about 100 Hz** ([GH/skills/control-loops.md](https://github.com/pollen-robotics/reachy_mini/blob/main/skills/control-loops.md)). The reference implementation is `moves.py` in the [conversation app](https://github.com/pollen-robotics/reachy_mini_conversation_app). With daemon-side tracking, an alternative is to drop the weight to 0 (pause) while an emotion plays, then restore it. Mind bug #1453 (section 7).
- The Reachy Mini Control desktop app has an "Expressions" tab ([DOCS/platforms/reachy_mini/usage](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/usage)).

## 5. On-board compute, and what runs where

- **SoC:** Raspberry Pi Compute Module 4 **CM4104016** (Wi-Fi, **4 GB RAM, 16 GB flash**), dual-band 2.4/5 GHz antenna, 2000 mAh LiFePO4 battery. ([DOCS/platforms/reachy_mini/hardware](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/hardware))
- **Official characterisation:**
  - The Wireless has "Onboard CM4… (limited compute)".
  - The platform table lists "Wireless (local): Limited (CM4), Memory/CPU constrained" and "Wireless (streamed): Full (laptop), camera via network, some tracking quality loss".

  ([GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md))
- **Evidence of on-robot load:**
  - `import reachy_mini` alone took about 1.8–2.6 s on the CM4 after 1.11 optimisations ([v1.11.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.11.0)).
  - Camera capture at 720p30 costs about 40% of a core at idle, enough to spin up the fan ([#1277](https://github.com/pollen-robotics/reachy_mini/issues/1277)).
  - The face detector is deliberately limited to 1 thread at 320 px input.
- **Realistic on-robot workloads:**
  - Daemon face tracking (YuNet), which is built in.
  - Light MediaPipe object detection at low fps. Community `dodo_focus` does phone detection with MediaPipe "Tried on Reachy Mini Wireless"; this is a community claim, not verified by Pollen ([Space README](https://huggingface.co/spaces/riyachhik/dodo_focus)).
- **Needs the Mac or the cloud:** pose estimation plus gaze plus object detection at a decent fps, local LLM/STT/TTS, or a VLM. This is inferred from the above; there are no official benchmarks.
- **Where apps run:**
  - Store-installed Python apps are launched by the daemon as a subprocess (`python -u -m your_app.main`) **on the robot**, in `/venvs/apps_venv/` ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps)).
  - The SDK also runs on any LAN host as a remote client ([DOCS/SDK/core-concept](https://huggingface.co/docs/reachy_mini/SDK/core-concept)).
  - JS apps run in the browser or a Space backend, with "the robot stays a pure IO device" ([GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md)).
  - The official conversation app shows a hybrid: the app runs on the robot, while the speech backend runs either on HF ("deployed") or on **your laptop over LAN** (`HF_REALTIME_WS_URL=ws://<laptop-ip>:8765/v1/realtime`) ([conversation app README](https://github.com/pollen-robotics/reachy_mini_conversation_app)).

## 6. App framework, packaging and distribution

- **Scaffold:** `reachy-mini-app-assistant create [--template conversation] my_app <path> [--publish]`. The assistant also provides `check` and `publish`. "Never create app folders manually." ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps))
- **Contract:**
  ```python
  class MyApp(ReachyMiniApp):
      custom_app_url: str | None = "http://0.0.0.0:8042"  # optional FastAPI + static/ web UI
      def run(self, reachy_mini: ReachyMini, stop_event: threading.Event): ...
  if __name__ == "__main__":
      app = MyApp(); app.wrapped_run()
  ```
  - Entry point: `[project.entry-points."reachy_mini_apps"] my_app = "my_app.main:MyApp"`.
  - The README frontmatter must contain the tag `reachy_mini_python_app` for store discoverability.
  - Generated layout: `index.html` and `style.css` for the Space landing page, `pyproject.toml`, and `my_app/main.py` plus `static/`.

  ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps))
- **Lifecycle:**
  - Only **one app** runs at a time.
  - The daemon stops an app with SIGINT, then returns the robot to its default pose.
  - While an app runs, "You cannot run Python scripts while an App is active" ([DOCS/platforms/reachy_mini/usage](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/usage)).
  - `--startup-app` / `PUT /api/apps/startup-app` auto-starts an app on wake or antenna touch.

  ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps))
- **Install and launch:**
  - The **Reachy Mini Control** desktop app has an Applications tab with Discover, Install and Start/Stop ([DOCS/platforms/reachy_mini/usage](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/usage)).
  - REST: `POST /api/apps/install` (`source_kind: "hf_space"`), `POST /api/apps/start-app/<name>`, `POST /api/apps/stop-current-app`, and so on.
  - Offline: `scp` the app and `pip install` it into `/venvs/apps_venv`.

  ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps))
- **App store:** apps are HF Spaces, browsable at [hf.co/reachy-mini/#/apps](https://hf.co/reachy-mini/#/apps). The blog tutorial is [Make and Publish Your Reachy Mini Apps](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps) (2025-12-12).
- **Dashboard status.** v1.9.0 merged "Deprecate the web dashboard" ([v1.9.0](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.9.0)), while `apps.md` still refers to `http://127.0.0.1:8000/` as the dashboard. In practice the **Reachy Mini Control desktop app** is the primary UI. Whether a web dashboard is still served on the Wireless at `:8000` is unverified.
- **JS apps are now the recommended default** for shareable apps. They are static HF Spaces that use the `@pollen-robotics/reachy-mini-sdk` npm package plus a host shell with HF OAuth, connecting through central signalling. Python is recommended "for developers, on-robot control loops, heavy motion sequencing, offline/LAN". There is also a remark that "for the app to be discoverable, it must be a python app for now". ([GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md), [DOCS/SDK/javascript-sdk](https://huggingface.co/docs/reachy_mini/SDK/javascript-sdk))
- **Relevant existing apps:**
  - Official:
    - [Conversation app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) ([GitHub](https://github.com/pollen-robotics/reachy_mini_conversation_app), v1.0.1, 2026-08-19). Tools include `play_emotion`, `head_tracking` (daemon-side), `camera` and `move_head`, and it has a layered motion system.
    - [hand_tracker_v2](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2), a camera-based control loop.
    - [reachy_mini_template_app](https://huggingface.co/spaces/pollen-robotics/reachy_mini_template_app).
    - JS references: [reachy_mini_emotions](https://huggingface.co/spaces/pollen-robotics/reachy_mini_emotions), [reachy_mini_telepresence](https://huggingface.co/spaces/pollen-robotics/reachy_mini_telepresence) and [reachy_mini_minimal_conversation](https://huggingface.co/spaces/pollen-robotics/reachy_mini_minimal_conversation).

    ([GH/README.md](https://github.com/pollen-robotics/reachy_mini/blob/main/README.md), [GH/AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md))
  - Community focus/pomodoro apps (not primary sources; their behaviour is described from their READMEs):
    - [riyachhik/dodo_focus](https://huggingface.co/spaces/riyachhik/dodo_focus): phone-in-frame detection with MediaPipe on-robot, then `rage1`, sulk, and `success2`.
    - [RyeCatcher/focus_guardian](https://huggingface.co/spaces/RyeCatcher/focus_guardian): motion-presence by default, with an optional vision-LLM engagement detector and emotion reactions.
    - [idormoy/focus_buddy](https://huggingface.co/spaces/idormoy/focus_buddy): face-touch, nail-biting and phone detection, plus spoken nudges; runs on the laptop or the robot.
    - [adonaivera/reachy_mini_pomodoro](https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro): voice pomodoro with a wake word.

## 7. Known limitations, gotchas and open issues

- **Tracking overrides app head motion.**
  - At `weight>=1`, the daemon drops the head part of `set_target` ([GH/.../abstract.py](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/backend/abstract.py)).
  - **Open bug [#1453](https://github.com/pollen-robotics/reachy_mini/issues/1453)** (2026-10-05): after the face is lost, the head recentres and client head targets stay discarded indefinitely.
  - [#1331](https://github.com/pollen-robotics/reachy_mini/issues/1331) "Head tracking still not working" is open (Lite/macOS).
  - [#1335](https://github.com/pollen-robotics/reachy_mini/issues/1335): you cannot tell the tracker *which* face to follow, because it auto-acquires the largest face.
- **Tracking is face-only.** It follows faces, not bodies. If the user turns away or looks down at a phone, YuNet may lose the face and the head recentres after about 2 s. This is inferred from the code and **not field-tested**.
- **Remote video quality.** Smearing on the Wireless WebRTC stream is fixed by raising the jitter buffer from 10 to 100 ms, but that setting is not yet configurable ([#1407](https://github.com/pollen-robotics/reachy_mini/issues/1407)).
- **Connection reliability.** Intermittent 30 s WebRTC connect delays ([#888](https://github.com/pollen-robotics/reachy_mini/issues/888)). A `webrtcsink` deadlock on macOS when running the daemon locally ([#1416](https://github.com/pollen-robotics/reachy_mini/issues/1416)) affects the Lite and sim on Mac, not the Mac-as-client case (unverified).
- **Audio.** `play_sound` blocks the caller for about 1.1 s intermittently on the Wireless via WebRTC ([#1436](https://github.com/pollen-robotics/reachy_mini/issues/1436)). The emotion clips are reported as loud by community apps.
- **Camera CPU.** The camera runs at full 720p30 even with no consumer, costing about 40% of a core on the CM4 ([#1277](https://github.com/pollen-robotics/reachy_mini/issues/1277)).
- **Single owner.**
  - Only one app at a time ([DOCS/SDK/apps](https://huggingface.co/docs/reachy_mini/SDK/apps)).
  - The central relay allows one consumer per robot ([DOCS/SDK/cloud-backend-consumer](https://huggingface.co/docs/reachy_mini/SDK/cloud-backend-consumer)).
  - Community apps note "Only one app can own the camera" ([dodo_focus](https://huggingface.co/spaces/riyachhik/dodo_focus)).
- **Simulation gaps.** No head tracking in sim ([#1451](https://github.com/pollen-robotics/reachy_mini/issues/1451)), and wrong sim intrinsics ([#1450](https://github.com/pollen-robotics/reachy_mini/issues/1450)).
- **Security.** The daemon HTTP API is unauthenticated on the LAN ("Direct LAN clients remain trusted") ([v1.12.0rc1](https://github.com/pollen-robotics/reachy_mini/releases/tag/v1.12.0rc1)), and the default SSH password is `root`.
- **API churn.**
  - Fast release cadence: 1.8 → 1.11 between June and September 2026.
  - 1.11 dropped scipy, which broke apps that relied on it transitively.
  - 1.12 forces an HF re-link.

  Pin the SDK version and keep the robot updated in step.

---

## Implications for our app

1. **Head-following can use the built-in tracker**, so the head actually follows the user rather than holding a fixed frame. It costs us nothing to build and runs on the CM4 with no Wi-Fi round-trip. Treat it as a "secondary" layer with these rules:
   - Pause it with `start_head_tracking(weight=0)` before playing an emotion, then restore the weight afterwards.
   - Do not rely on `weight=1` plus our own `set_target` head commands, because of #1453.
   - Alternative if the tracker misbehaves: run our own detector and drive `set_target`/`look_at_image` from a single 50–100 Hz loop, following the official control-loops pattern.
2. **Distraction detection probably belongs on the Mac.** That covers phone in hand, gaze away, posture (pose estimation) and absence. Vision models at useful fps exceed what Pollen calls "limited compute", and the CM4 already spends a large share of a core on the camera. That leaves two topologies:
   - **(A) Mac-side SDK client.** `ReachyMini(host="reachy-mini.local")` with WebRTC frames to the Mac, inference there, and commands back over WS. This is the simplest to develop, but it is not a store-launchable app, and frame latency over WebRTC is unquantified.
   - **(B) Thin on-robot `ReachyMiniApp`.** It owns motion, emotions and tracking, and offloads inference to a Mac service on the LAN, as the conversation app does with `HF_REALTIME_WS_URL`. This is publishable on HF Spaces and installable from the store. How frames get to the Mac is still to be decided: either the app ships JPEGs via `get_frame_jpeg()` at around 10 fps, or the Mac opens its own WebRTC session. That second option needs verification, because only one owner/consumer may be allowed.
3. **Emotions:** use daemon-side playback (the REST `play/recorded-move-dataset` endpoint) when commanding from the Mac. It avoids streaming 100 Hz `set_target` over Wi-Fi and keeps sound in sync. The emotions library already has fitting moves: `attentive1/2`, `impatient1/2`, `reprimand1-3`, `displeased1/2`, `boredom1/2`, `success1/2`, `proud1-3` and `welcoming1/2`. Expose a sound-off option, because the clips are loud.
4. **Voice:** the robot mic (16 kHz, XMOS AEC, DoA) and speaker are reachable remotely over WebRTC. For STT/TTS and an LLM, reuse the conversation app's architecture, or its "conversation" template from `reachy-mini-app-assistant`, rather than building audio plumbing from scratch.
5. **Develop against 1.11.x on Python 3.12, and pin the SDK.** The sim cannot test tracking or the camera properly, so plan for on-hardware testing early.
6. **Differentiation:** at least four community focus apps exist, and `dodo_focus` already does phone detection. Our differentiators would be voice task-setting, active head-following, multi-signal distraction detection (gaze, posture, absence) and graded nudges.

## Open questions

- What is the end-to-end latency and real fps of WebRTC frames from the Wireless to a Mac over home Wi-Fi? Measure it on Sonny.
- Can the on-robot app (LOCAL backend) and a Mac SDK client (WEBRTC) both read the camera at the same time, and can two `ReachyMini` clients command motion at once? The docs say one *app* at a time and one *central* consumer. The LAN multi-client behaviour is unverified.
- Is the deprecated web dashboard still served at `http://reachy-mini.local:8000/` on current Wireless images, or is Reachy Mini Control the only UI?
- Does #1453 (tracking weight latch) get fixed in 1.12.0? Watch the release.
- Does YuNet tracking hold when the user looks down at a phone or turns sideways? This is critical for "follow the user" in exactly the moments we care about.
- The Python version support statement (3.10 in the docs vs `>=3.11` on PyPI) and whether 3.13 works.
- Should our app be Python (store-discoverable, on-robot) or JS (Pollen's recommended default) given the "discoverable must be Python for now" note? This is a decision for the architecture ADR.
- What are the HF Spaces publishing requirements for an app that depends on a Mac-side companion service? Is there any precedent beyond the conversation app's "local backend" mode?

## Sources

- PyPI reachy-mini: https://pypi.org/project/reachy-mini/ (JSON: https://pypi.org/pypi/reachy-mini/json)
- GitHub repo: https://github.com/pollen-robotics/reachy_mini (README, AGENTS.md, pyproject.toml, examples/, skills/, src/)
- Releases: https://github.com/pollen-robotics/reachy_mini/releases (v1.9.0, v1.10.0, v1.11.0, v1.12.0rc1)
- Docs: https://huggingface.co/docs/reachy_mini/SDK/installation, /SDK/core-concept, /SDK/python-sdk, /SDK/media-architecture, /SDK/apps, /SDK/integration, /SDK/cloud-backend-consumer, /SDK/javascript-sdk, /SDK/quickstart, /platforms/reachy_mini/hardware, /platforms/reachy_mini/get_started, /platforms/reachy_mini/usage, /platforms/reachy_mini/development_workflow, /platforms/simulation/get_started, /examples/head_tracking, /troubleshooting
- Source files: src/reachy_mini/reachy_mini.py, io/ws_client.py, media/media_manager.py, media/media_server.py, media/camera_constants.py, media/webrtc_client_gstreamer.py, vision/face_detector.py, vision/face_tracking.py, daemon/backend/abstract.py, daemon/app/routers/move.py
- Issues: #1453, #1451, #1450, #1436, #1416, #1407, #1335, #1331, #1277, #888 on https://github.com/pollen-robotics/reachy_mini/issues
- Emotions dataset: https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library
- Dances: https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library, https://github.com/pollen-robotics/reachy_mini_dances_library
- Conversation app: https://github.com/pollen-robotics/reachy_mini_conversation_app, https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app
- HF blog: https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps
- App store: https://hf.co/reachy-mini/#/apps
- Community apps (non-primary): https://huggingface.co/spaces/riyachhik/dodo_focus, https://huggingface.co/spaces/RyeCatcher/focus_guardian, https://huggingface.co/spaces/idormoy/focus_buddy, https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro
