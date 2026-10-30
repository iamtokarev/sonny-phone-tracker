# R4: Publishing on HF Spaces and app architecture

Research track R4 for the focus-companion app (wireless Reachy Mini + Apple Silicon Mac).
Researched 2026-10-10. Library versions at that date: `reachy-mini` **1.11.0** on PyPI (2026-09-21, `1.12.0rc1` pre-release) and `@pollen-robotics/reachy-mini-sdk` **1.11.0** on npm (`latest` tag). Repo state checked: `pollen-robotics/reachy_mini@fbdbca3` (2026-10-05).

Labels used below:
- **[verified]**: read directly in a primary doc or in source code.
- **[inferred]**: my conclusion from verified facts. Not stated anywhere.
- **[unverified]**: not confirmed. Needs a test on hardware or a question to Pollen.

---

## TL;DR

1. **The Space is the package and the store listing. It is not a running demo.** A Reachy Mini Python app is a `sdk: static` HF Space. The Space contains a landing `index.html`, a `pyproject.toml` that declares a `reachy_mini_apps` entry point, and a README tagged `reachy_mini` + `reachy_mini_python_app`. The robot's dashboard finds apps by that tag, downloads the Space snapshot and `pip install`s it into the robot's venv. Nothing runs on HF compute. ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps); source: [`hf_space.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/sources/hf_space.py))
2. **Build it with `reachy-mini-app-assistant create <name> <path> --publish`, then run `check` and `publish`.** The docs say "Never create app folders manually." There are two templates: `default` and `conversation`. ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
3. **Where the code runs:**
   - When the dashboard installs and starts the app on a **Wireless** robot, it runs **on the robot's Raspberry Pi CM4** (4 GB RAM) in `/venvs/apps_venv/`, as a daemon subprocess. Only one app runs at a time. ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps), [wireless get started](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started), [hardware.md](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/platforms/reachy_mini/hardware.md))
   - The **same app class can also run on the Mac** with `python -m my_app.main`. `ReachyMiniApp` detects that no daemon is on localhost and switches to `connection_mode="network"`. Commands then go over WebSocket to `reachy-mini.local:8000` and camera/audio arrive over WebRTC. ([`app.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/app.py), [media architecture](https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture))
   - Pollen's own guidance calls this mode "Wireless (streamed): Full (laptop) compute, some tracking quality loss". ([reachy_mini AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md))
4. **Space SDKs:**
   - `static` Spaces are free.
   - **Gradio and Docker Spaces now need a paid plan to create (PRO).** Free accounts get only 2 ZeroGPU Gradio Spaces. ([Spaces overview](https://huggingface.co/docs/hub/spaces-overview))
   - Python Reachy apps use `sdk: static` anyway. JS apps (`reachy_mini_js_app` tag) are static Spaces built with Vite that talk to the robot over WebRTC through Pollen's HF-hosted signaling relay.
   - The **robot dashboard lists only Python apps today.** JS apps appear in the mobile app catalog. ([`hf_space.py` TODO](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/sources/hf_space.py), [APP_CREATION_GUIDE §13.7](https://github.com/pollen-robotics/reachy_mini/blob/main/ts/APP_CREATION_GUIDE.md))
5. **A JS/TS front end inside a Python app is supported.**
   - Set `custom_app_url = "http://0.0.0.0:8042"`. The base class then starts a FastAPI server (`self.settings_app`) that serves the package's `static/` folder.
   - The dashboard shows a gear icon that opens this UI.
   - The UI talks to the backend through:
     - plain REST routes on `settings_app`;
     - MJPEG `StreamingResponse` for video (the pattern in `hand_tracker_v2`);
     - the official `JsonRpcServer`, a JSON-RPC 2.0 WebSocket mounted at `/rpc` that pushes notifications. It ships in 1.11.0.

   ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps), [`jsonrpc_server.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/jsonrpc_server.py), [hand_tracker_v2](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2/blob/main/hand_tracker_v2/main.py))
6. **Recommended v1:**
   - One Python `ReachyMiniApp` package, published as a static Space with the `reachy_mini_python_app` tag.
   - Develop and run it **on the Mac** in streamed mode, so vision runs on Apple Silicon. The front end is Vite + TS, prebuilt into `static/` and served by the app's FastAPI on `:8042`. It uses REST for setup, the `/rpc` WebSocket for live status, and throttled MJPEG for the debug video.
   - Leave robot-side (CM4) execution as a later "lite" mode once we know how heavy the vision really is.
   - Gradio and fastrtc are **not** needed. The current official apps use FastAPI + static files.

---

## 1. How Reachy Mini apps are distributed via HF Spaces

### 1.1 The app store
- "Reachy Mini has an app ecosystem powered by Hugging Face Spaces. You can build Python apps, publish them, and any Reachy Mini owner can install them in one click from the dashboard." [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
- The daemon reads two lists [verified, source]. Both are in [`apps/sources/hf_space.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/sources/hf_space.py):
  - **Official/curated**: `list_available_apps()` reads a JSON list of Space IDs from the dataset `pollen-robotics/reachy-mini-official-app-store` (`app-list.json`), then fetches each Space's metadata.
  - **Community**: `list_all_apps()` calls `HfApi().list_spaces(filter="reachy_mini_python_app", sort="likes", limit=500)`. When the user is signed in, this also includes their own private Spaces.
  - The same file has a comment: `# TODO look for js apps too (reachy_mini_js_app)`. **The dashboard does not list JS apps yet.**
- `reachy-mini-app-assistant publish --official` opens a PR on that dataset to ask for official listing. [verified, source: [`assistant.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/assistant.py)]
- **Install mechanism** [verified, source: [`local_common_venv.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/sources/local_common_venv.py)]:
  - The daemon calls `snapshot_download` on the Space repo, then `uv pip install` or `pip install` into the apps venv.
  - **The Space is just a pip-installable source tree.** No `npm` build happens at install time. [inferred]
- **Updates** [verified, source: [`app_update_checker.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/sources/app_update_checker.py)]:
  - The daemon compares the installed snapshot's **commit SHA** with the Space's latest SHA. The `pyproject` version is not used.
  - So every push to `main` counts as an update.
  - REST endpoint: `POST /api/apps/update/<app_name>`. ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
- **REST API for app lifecycle** (on `<HOST>:8000`):
  - `POST /api/apps/install` with `{"name", "source_kind": "hf_space", "url"}`
  - `/api/apps/start-app/<name>`
  - `/api/apps/stop-current-app`
  - `/api/apps/current-app-status`
  - `/api/apps/remove/<name>`
  - `/api/apps/startup-app` (auto-start on wake-up or antenna touch)

  [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))

### 1.2 Required layout (Python app)
Generated by `reachy-mini-app-assistant create my_app <path> [--publish] [--template conversation]` [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps)):

```
my_app/
├── index.html              # HF Space landing page (what visitors see on huggingface.co)
├── style.css               # landing page styles
├── pyproject.toml          # package + entry point
├── README.md               # Space card: sdk: static, tags reachy_mini + reachy_mini_python_app
└── my_app/
    ├── __init__.py
    ├── main.py             # class MyApp(ReachyMiniApp) + __main__ block
    └── static/             # optional web UI served by the app's FastAPI (index.html, main.js, style.css)
```

Template files [verified, source: [`apps/templates/`](https://github.com/pollen-robotics/reachy_mini/tree/main/src/reachy_mini/apps/templates)]:
- `README.md` front matter:
  ```yaml
  sdk: static
  short_description: ...
  tags: [reachy_mini, reachy_mini_python_app]
  ```
- `pyproject.toml`:
  ```toml
  [project.entry-points."reachy_mini_apps"]
  my_app = "my_app.main:MyApp"
  ```
  plus `keywords = ["reachy-mini-app"]` and `[tool.setuptools.package-data] my_app = ["**/*"]`. The package-data line is what ships `static/` inside the wheel.
- `main.py`:
  - subclass `ReachyMiniApp`
  - implement `run(self, reachy_mini, stop_event)`
  - optionally set `custom_app_url` and `request_media_backend`. The template comment says: "On the wireless, use gstreamer_no_video to optimise CPU usage if the app does not use video streaming".
  - a `__main__` block that calls `app.wrapped_run()`.

`reachy-mini-app-assistant check` validates the following [verified, source: [`assistant.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/assistant.py)]:
- `pyproject.toml`, `index.html`, `style.css` and `README.md` exist;
- the README has a `title` and both tags (`reachy_mini`, plus `reachy_mini_python_app` **or** `reachy_mini_js_app`);
- the entry point exists and the class subclasses `ReachyMiniApp`;
- install and uninstall work in a temporary venv.

How the daemon runs an app [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps)):
- It launches `python -u -m your_app.main` as a subprocess and passes a connected `ReachyMini` plus a `stop_event`.
- It stops the app with SIGINT, then moves the robot back to its default pose.
- The app inherits the daemon's environment variables. "There is no special injection mechanism". The recommended way to configure secrets is a settings page in the app's own web UI.

### 1.3 Official template and real published apps
| Space | What it shows |
|---|---|
| [`pollen-robotics/reachy_mini_template_app`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_template_app) | Linked from the docs as the template with a toggle and sound playback. **The Hub API returned "Repository not found" for it on 2026-10-10** (moved or private?) [unverified]. The canonical template is now the Jinja set in the repo (`apps/templates/`). |
| [`pollen-robotics/hand_tracker_v2`](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2) | **Closest to our use case.** Camera control loop at 50 Hz with `set_target`, plus emotion moves from `pollen-robotics/reachy-mini-emotions-library`. The `custom_app_url` UI has toggles (`POST /set_toggles`), a readiness poll (`GET /ready`) and an **MJPEG `/video_feed`** (`StreamingResponse`, `multipart/x-mixed-replace`). The UI is plain JS in `hand_tracker_v2/static/`. |
| [`pollen-robotics/reachy_mini_greetings`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_greetings) | Minimal README (`sdk: static`, both tags). `pyproject` depends on `reachy-mini-toolbox[vision]` and `ultralytics` and bundles a MediaPipe `.task` model inside the package. |
| [`pollen-robotics/reachy_mini_conversation_app`](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app) | Large app with a `src/` layout. Its web UI is served by FastAPI on `custom_app_url = "http://0.0.0.0:7860/"`. Apache-2.0, versioned `1.0.1`. Shows **robot-side app + laptop-side heavy backend**: `HF_REALTIME_WS_URL=ws://<laptop-lan-ip>:8765/v1/realtime`, or an SSH reverse tunnel. |
| [`pollen-robotics/telepresence`](https://huggingface.co/spaces/pollen-robotics/telepresence) | JS app example (`reachy_mini_js_app`). React 19 + Vite, `sdk: docker`, `hf_oauth: true`. |

Community examples tagged `reachy_mini_python_app`: [`HTurlet15/reachy_alive`](https://huggingface.co/spaces/HTurlet15/reachy_alive), [`fcollonval/reachy_mini_wake_word`](https://huggingface.co/spaces/fcollonval/reachy_mini_wake_word), [`signalnine/reachy_mini_dance_party_app`](https://huggingface.co/spaces/signalnine/reachy_mini_dance_party_app). Found through an HF MCP Space search, 2026-10-10.

### 1.4 JS ("Live/Web") apps, the other flavour
- Pollen's agent guide says: "**Default to a JS (Live/Web) app** unless the user explicitly wants on-robot Python, or has a need that only Python can cover (heavy on-robot compute, rich hardware access, deterministic real-time control loops, or bundled offline LAN tooling)". It also notes: "for the app to be discoverable, it must be a python app for now". [verified] ([reachy_mini AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md))
- How a JS app works [verified] ([JS SDK reference](https://huggingface.co/docs/reachy_mini/main/SDK/javascript-sdk), [APP_CREATION_GUIDE](https://github.com/pollen-robotics/reachy_mini/blob/main/ts/APP_CREATION_GUIDE.md)):
  - It is a static Space with `hf_oauth: true`.
  - The browser signs in with HF OAuth, then signals through `https://pollen-robotics-reachy-mini-central.hf.space`.
  - Video, audio and commands then flow **peer-to-peer over WebRTC** between the browser and the robot.
  - It is "Currently supported on **wireless versions** only".
- Tags and listing [verified]: the `reachy_mini_js_app` tag is mandatory. JS apps are listed in the **mobile app** catalog, not the robot dashboard.
- **Conflict on Space build** [unverified]:
  - The APP_CREATION_GUIDE says to build with `app_build_command: npm ci && npm run build`.
  - The telepresence README says static `app_build_command` is "now restrict[ed] to paid Team/Enterprise plans" and uses Docker for that reason.
  - The HF static-Spaces doc still describes `app_build_command` with no plan restriction. ([static Spaces](https://huggingface.co/docs/hub/spaces-sdks-static))
- For our case this matters only if we ship a separate JS app.

---

## 2. Where the code actually runs

| Mode | Where Python runs | How it reaches robot | Compute | Source |
|---|---|---|---|---|
| Dashboard install + start (Wireless) | On robot, Raspberry Pi **CM4, 4 GB RAM**, shared `/venvs/apps_venv/` | `localhost:8000`; camera through local IPC | "Limited (CM4)", "Memory/CPU constrained" | [apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps), [hardware.md](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/platforms/reachy_mini/hardware.md), [AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md) |
| `python -m my_app.main` on the Mac (streamed) | Mac | WebSocket `ws://reachy-mini.local:8000/ws/sdk` for commands and state; WebRTC (H.264/Opus) for media | Full (Apple Silicon), "some tracking quality loss" | [`app.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/app.py), [`ws_client.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/io/ws_client.py), [media architecture](https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture), [AGENTS.md](https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md) |
| JS app in a browser | The browser (any device) | WebRTC P2P after HF-hosted signaling | Browser's own hardware | [JS SDK](https://huggingface.co/docs/reachy_mini/main/SDK/javascript-sdk) |
| Cloud backend (HF Space / Cloud Run) | HF compute | `ReachyCentralConsumer` (aiortc), relayed through the central signaling Space, one consumer per robot | Paid Space hardware | [cloud-backend consumer](https://huggingface.co/docs/reachy_mini/main/SDK/cloud-backend-consumer) |

Key points:
- **A hosted Python Space cannot reach the LAN robot. Official Python apps do not try.** The Space is only a pip source plus a landing page. [verified for the mechanism, see §1.1]
- The only official cloud-to-robot path is the central relay (the JS SDK and `ReachyCentralConsumer`). It needs the visitor's HF token and allows "one consumer per robot at a time". [verified] ([cloud-backend consumer](https://huggingface.co/docs/reachy_mini/main/SDK/cloud-backend-consumer))
- Streamed-mode details [verified unless noted]:
  - `ReachyMini()` defaults to `host="reachy-mini.local"`, `port=8000`, `connection_mode="auto"`. ([`reachy_mini.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/reachy_mini.py))
  - The remote WebRTC client signals to the robot on port **8443** (LAN, not the central relay). (`media_manager.py`)
  - It needs GStreamer on the client, which "ships as Python wheels on macOS". ([Python SDK](https://huggingface.co/docs/reachy_mini/main/SDK/python-sdk))
- **On-robot vision is capped.** The daemon serves local IPC camera frames at **10 fps** to keep CM4 load low. [verified] ([media architecture](https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture)) This is a strong reason to run our vision on the Mac.
- **Cheap on-robot option.** The daemon has built-in face head-tracking: `start_head_tracking(weight)` / `get_tracked_face()`, with detection running inside the daemon. It could handle "follow the user with the head" without our own model. [verified] ([Python SDK](https://huggingface.co/docs/reachy_mini/main/SDK/python-sdk)) R2/R1 should check its cost and quality.
- **Offload pattern used by Pollen.** The conversation app keeps the robot-side app thin and points it at a laptop service over a LAN WebSocket, or over `ssh -R`. [verified] ([conversation app README](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app/blob/main/README.md))

---

## 3. Space SDK options and limits

| SDK | Cost to create | Runs code? | Fit for us |
|---|---|---|---|
| `static` | **Free for everyone**, "served directly without running on compute" | No (optional build step) | **Yes.** This is what every Python Reachy app uses. |
| `gradio` | Paid plan required (PRO). Free accounts: up to 2 ZeroGPU Gradio Spaces | Yes | Not needed |
| `docker` | Paid plan required | Yes, `app_port` default 7860, uid 1000, ephemeral disk | Only for a cloud backend or a Docker-built JS app |

Sources: [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [static Spaces](https://huggingface.co/docs/hub/spaces-sdks-static), [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker). [verified]

Other limits [verified] ([Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [config reference](https://huggingface.co/docs/hub/spaces-config-reference)):
- CPU Basic hardware: 2 vCPU, 16 GB RAM, 50 GB non-persistent disk.
- Free hardware sleeps when unused.
- Outbound traffic is allowed only on ports 80, 443 and 8080.
- Static Spaces expose secrets and variables to client JS through `window.huggingface.variables`, so **never put secrets in a static Space**.
- `short_description` is capped at 60 characters. ([APP_CREATION_GUIDE §11.1](https://github.com/pollen-robotics/reachy_mini/blob/main/ts/APP_CREATION_GUIDE.md))

**A JS/TS front end served by the Python backend inside the Reachy app is supported:**
- When `custom_app_url` is set:
  - `ReachyMiniApp.__init__` creates `self.settings_app = FastAPI()`;
  - it mounts `<package>/static` at `/static` and serves `static/index.html` at `/`, with a no-cache middleware;
  - `wrapped_run()` runs it with uvicorn on the host and port taken from `custom_app_url`.

  [verified, source: [`app.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/app.py)]
- On a Wireless robot the UI is at `http://reachy-mini.local:8042`. [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
- **Vite build output must be committed into `my_app/static/`.** Install is a plain `pip install` of the Space snapshot, so nothing runs `npm` (§1.1). [inferred] Vite also needs `base: "/static/"`, or `index.html` asset paths must be adjusted, because assets are mounted under `/static`. [inferred from the mount code]
- **Dashboard embedding.** The dashboard "shows a settings icon to open this UI". The blog says "Click on the ⚙️ icon of a running app to view its GUI". **Whether it opens as an iframe or a new tab is not documented** [unverified]. ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps), [blog, 2025-12-03](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps))
- Remote clients can reach the running app's UI channel without LAN access to port 8042. The daemon's JSON-RPC relay forwards any non-`apps.*` JSON-RPC method that arrives over WebRTC or `/ws/sdk` to the running app's `/rpc` WebSocket, which it derives from `custom_app_url`. It also re-broadcasts the app's notifications. [verified, source: [`jsonrpc_relay.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/jsonrpc_relay.py)] This presumably applies only when the daemon launched the app on the robot [inferred].

---

## 4. Communication layers

| Link | Protocol | Notes / source |
|---|---|---|
| SDK ↔ daemon (commands, state, IMU) | **WebSocket** `ws://<host>:8000/ws/sdk` (FastAPI). The daemon also has a REST API and `/docs` on :8000 | [`ws_client.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/io/ws_client.py), [`ws_server.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/io/ws_server.py), [core concepts](https://huggingface.co/docs/reachy_mini/main/SDK/core-concept) [verified] |
| Daemon → SDK media, same machine | GStreamer IPC (`unixfdsink`), 10 fps cap; audio device opened directly | [media architecture](https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture) [verified] |
| Daemon → SDK media, remote (Mac) | **WebRTC** (`webrtcsink`), H.264 + Opus, LAN signaling on :8443 | [media architecture](https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture), `media_manager.py` [verified] |
| Browser JS app ↔ robot | WebRTC P2P (video, audio, JSON data channel), signaling via the HF central Space (SSE/HTTP + HF OAuth) | [JS SDK](https://huggingface.co/docs/reachy_mini/main/SDK/javascript-sdk) [verified] |
| App backend ↔ its own UI | FastAPI on `custom_app_url`: REST routes, optional `JsonRpcServer` WebSocket `/rpc` (JSON-RPC 2.0, `broadcast_threadsafe()` for pushes from worker threads) | [`jsonrpc_server.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/jsonrpc_server.py), in `reachy-mini==1.11.0` wheel [verified] |
| Video to the app UI | Official example uses **MJPEG** `StreamingResponse` at about 20 fps (`time.sleep(0.05)`) | [hand_tracker_v2](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2/blob/main/hand_tracker_v2/main.py) [verified] |

**Gradio and fastrtc:**
- The current conversation app has no Gradio or fastrtc. It uses FastAPI + uvicorn on :7860. [verified by grep of `main.py`]
- In Reachy docs, fastrtc appears only as the HF TURN-credentials service (`fastrtc-turn-service.hf.space`), used by cloud consumers. [verified] ([cloud-backend consumer](https://huggingface.co/docs/reachy_mini/main/SDK/cloud-backend-consumer))
- Earlier versions of the conversation app may have used Gradio + fastrtc [unverified, from memory]. That is irrelevant now.

**SSE vs WebSocket for our UI.** Use the provided `/rpc` WebSocket. It is official, bidirectional, and already the channel the daemon relays. SSE would also work but adds nothing. [inferred]

---

## 5. Dev workflow

- **Simulation**: run `reachy-mini-daemon --sim`, then open `http://127.0.0.1:8000/`. With `uv pip install -e .` the app shows up in the dashboard's installed list. [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
  - Limitation: simulation "can't test camera-based tracking, audio input/output, physical antenna presses, real motor behavior". [verified] ([testing-apps skill](https://github.com/pollen-robotics/reachy_mini/blob/main/skills/testing-apps.md))
  - So test vision with recorded video or the Mac webcam (`media_backend="no_media"` + OpenCV is documented). [inferred]
- **Wireless robot, streamed from the Mac**: run `python -m focus_companion.main` on the Mac.
  - It auto-connects to `reachy-mini.local`.
  - The UI runs at `localhost:8042`.
  - For logs, use the terminal locally. On the robot, use `ssh pollen@reachy-mini.local` and `sudo journalctl -u reachy-mini-daemon -f`. [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
- **Wireless robot, on-robot**: install from the dashboard or with `curl POST /api/apps/install`.
  - Offline install: `scp` to the robot, then `/venvs/apps_venv/bin/pip install`.
  - After a manual deploy, restart the app and clear `__pycache__`.

  [verified] ([apps doc](https://huggingface.co/docs/reachy_mini/main/SDK/apps))
- **Publishing and versioning**:
  - `hf auth login` (write token), then `reachy-mini-app-assistant publish <path>`. After that, a `git push` to the Space remote updates it.
  - Robots see an update whenever the Space commit SHA changes (§1.1), so treat Space `main` as a release channel. Develop elsewhere (a GitHub repo or a dev branch) and push to the Space only on release. [inferred]
  - Bump the `pyproject` `version` anyway, for humans. [inferred]
- **README / card**:
  - Required: `title`, `sdk: static`, `tags: [reachy_mini, reachy_mini_python_app]`, and `short_description` (60 characters max, shown as the store description). [verified]
  - Optional: `license: apache-2.0` (or `mit`). For `license: other`, add a LICENSE file and `license_name`. [verified] ([licenses](https://huggingface.co/docs/hub/repositories-licenses))
  - Not required: the existing apps carry no `license` key in front matter. The conversation app has a LICENSE file and says "Apache 2.0" in its README. [verified]
  - The SDK is Apache-2.0 ([LICENSE](https://github.com/pollen-robotics/reachy_mini/blob/main/LICENSE)), so any OSI license works for us. [verified, inferred]
  - Spaces need no model card. List the models we use under `models:` so the Space links them. [verified] ([Spaces overview](https://huggingface.co/docs/hub/spaces-overview))
- **Private testing**: `publish --private` / `create --publish --private`. The dashboard's `list_all_apps()` includes private Spaces when the robot is signed in to HF. [verified, source]

---

## 6. Recommended v1 architecture

All app code runs on the Mac. The robot runs only Pollen's daemon. Same package, publishable as a Space.

```
┌───────────────────────── MacBook (Apple Silicon) ──────────────────────────┐
│                                                                             │
│  Browser tab  http://localhost:8042                                         │
│  ┌──────────────────────────────┐                                           │
│  │ Vite + TS UI (prebuilt into  │  REST  (session setup: task, 25 min, ...) │
│  │ focus_companion/static/)     │──────────────┐                            │
│  │  - session setup             │  WS /rpc     │  JSON-RPC 2.0:             │
│  │  - live status, timer        │◄────────────►│  status/distraction events │
│  │  - stats                     │  MJPEG       │  (broadcast_threadsafe)    │
│  │  - debug video <img>         │◄─────────────┤  /video_feed ~5-10 fps     │
│  └──────────────────────────────┘              │  (annotated frames, opt.)  │
│                                                ▼                            │
│  Python process: `python -m focus_companion.main`  (ReachyMiniApp subclass) │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ FastAPI settings_app (uvicorn, :8042)  + JsonRpcServer at /rpc        │   │
│  │ session/state machine ── vision workers (Apple Silicon: CoreML/MPS)  │   │
│  │ motion/nudge policy ──── voice (TTS/STT, see R3)                     │   │
│  │ local storage for stats (SQLite/JSON, Mac disk)                      │   │
│  └───────────────┬───────────────────────────────▲───────────────────────┘  │
└──────────────────┼───────────────────────────────┼──────────────────────────┘
                   │ WebSocket ws://reachy-mini.local:8000/ws/sdk
                   │ (set_target / goto_target / play_move, state)
                   │                               │ WebRTC (LAN, signaling :8443)
                   │                               │ H.264 camera + Opus mic → Mac
                   │                               │ (+ audio push → robot speaker)
┌──────────────────▼───────────────────────────────┴──────────────────────────┐
│  Reachy Mini Wireless (Raspberry Pi CM4)                                     │
│  reachy-mini-daemon (systemd): FastAPI :8000 (REST, /ws/sdk, dashboard),     │
│  GstMediaServer (camera, mic array, speaker), motors, IMU                    │
└──────────────────────────────────────────────────────────────────────────────┘

  Hugging Face Hub (no runtime role):
  Space  <user>/focus_companion  (sdk: static, tags reachy_mini + reachy_mini_python_app)
   = landing index.html + pip-installable package (+ prebuilt static/ UI)
   → listed in the dashboard "community apps"; installable on the robot (CM4) later.
```

Why this layout:
- It uses the Mac's compute, which is the point. The CM4 gets 10 fps IPC frames and has 4 GB RAM.
- The code is still a standard `ReachyMiniApp`, so it can be published and later installed on the robot unchanged.
- Each connection uses the protocol Pollen already provides. We write no custom transport.
- The UI is our own TS. The backend uses the official FastAPI/`JsonRpcServer` hooks.

Later options (not v1):
- **(b) Robot-resident orchestrator + Mac "vision worker"** over a LAN WebSocket, the conversation-app pattern. The app then works from the dashboard and is reachable remotely through the daemon's JSON-RPC relay. Cost: frames must reach the Mac, and multi-consumer WebRTC is unknown (see open questions).
- **(c) JS app with in-browser vision** (MediaPipe/ONNX-web on the Mac's browser). Zero-install, free static Space, mobile-catalog listing. But it moves the backend to TS, against the user's preference, and has no robot dashboard listing yet.

---

## Open questions

1. **App slot vs. streamed run.** Can a Mac-run streamed app coexist with the dashboard "one app at a time" lock? `robot_app_lock.py` mentions remote sessions taking the slot, and `--startup-app` "does not replace ... an active remote session". [unverified] Test: start our Mac app, then try to start a dashboard app.
2. **Multiple WebRTC consumers.** Can more than one WebRTC media consumer exist at once on the LAN path? For example our Python client plus the JS SDK or the mobile app. The central relay allows one per robot. [unverified]
3. **Streamed-mode quality.** Actual frame rate, resolution and latency of the camera stream from the Wireless robot to the Mac, and the "tracking quality loss" Pollen mentions. Needs measurement (with R2).
4. **UI host when installed on the robot.** Is the dashboard UI an iframe or a new tab? Does the robot's daemon serve the app UI on the LAN at `:8042`? Docs say yes, `reachy-mini.local:8042`. Not tested.
5. **`app_build_command` on free accounts.** Is it now paid-only for static Spaces? The telepresence README and the HF docs disagree. This matters only for a JS app.
6. **`pollen-robotics/reachy_mini_template_app` returned 404** through the Hub API. Ask whether it moved, or use the repo templates.
7. **Official listing criteria** for `reachy-mini-official-app-store` (PR review policy) are undocumented.
8. **Privacy.** Should we document in the README that camera frames stay on the LAN? (WebRTC on the LAN, no cloud relay in streamed mode.) [inferred, worth confirming that no TURN or central relay is used in LAN mode]

---

## Sources

Reachy Mini docs and code:
- Apps guide: https://huggingface.co/docs/reachy_mini/main/SDK/apps
- Media architecture: https://huggingface.co/docs/reachy_mini/main/SDK/media-architecture
- Python SDK: https://huggingface.co/docs/reachy_mini/main/SDK/python-sdk
- Core concepts: https://huggingface.co/docs/reachy_mini/main/SDK/core-concept
- JavaScript SDK: https://huggingface.co/docs/reachy_mini/main/SDK/javascript-sdk
- Cloud-backend consumer: https://huggingface.co/docs/reachy_mini/main/SDK/cloud-backend-consumer
- Wireless get started (CM4): https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started
- Blog, "Make and publish your Reachy Mini apps" (2025-12-03): https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps
- Repo (commit fbdbca3, 2026-10-05): https://github.com/pollen-robotics/reachy_mini
  - `AGENTS.md`
  - `ts/APP_CREATION_GUIDE.md`
  - `skills/testing-apps.md`
  - `src/reachy_mini/apps/{app.py, assistant.py, jsonrpc_server.py, templates/, sources/hf_space.py, sources/app_update_checker.py, sources/local_common_venv.py}`
  - `src/reachy_mini/io/{ws_client.py, ws_server.py}`
  - `src/reachy_mini/daemon/jsonrpc_relay.py`
  - `src/reachy_mini/media/media_manager.py`
  - `docs/source/platforms/reachy_mini/hardware.md`
- PyPI `reachy-mini` (1.11.0): https://pypi.org/project/reachy-mini/
- npm `@pollen-robotics/reachy-mini-sdk`: https://www.npmjs.com/package/@pollen-robotics/reachy-mini-sdk

Example Spaces:
- https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2
- https://huggingface.co/spaces/pollen-robotics/reachy_mini_greetings
- https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app
- https://huggingface.co/spaces/pollen-robotics/telepresence

HF Hub docs:
- Spaces overview: https://huggingface.co/docs/hub/spaces-overview
- Config reference: https://huggingface.co/docs/hub/spaces-config-reference
- Static Spaces: https://huggingface.co/docs/hub/spaces-sdks-static
- Docker Spaces: https://huggingface.co/docs/hub/spaces-sdks-docker
- Licenses: https://huggingface.co/docs/hub/repositories-licenses
