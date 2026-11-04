# R5: A live web dashboard for Sonny

Research track R5 for the focus-companion app: a browser page that shows, live, what Sonny sees (camera + detection boxes), the Focus Session, the agent, and link health.
Researched 2026-11-04. Versions checked that day:
- `reachy-mini` **1.11.0** pinned in this repo; PyPI latest **1.13.0** (2026-10-26), `1.14.0rc2` (2026-11-04). `@pollen-robotics/reachy-mini-sdk` npm `latest` **1.13.0**. Repo state: `pollen-robotics/reachy_mini@25756e7` (2026-11-04).
- In this repo's venv: FastAPI **0.143.0**, Starlette **0.52.1**, uvicorn **0.54.0**, websockets **15.0.1**, OpenCV **5.0.0**, pipecat-ai **1.14.0**.
- npm `latest`: Vite 8.3.5, Svelte 5.57.3, React 19.3.1, Preact 11.1.1, Solid 1.9.18, Lit 3.3.3, TypeScript 7.0.2 (6.0.3 is the last 6.x), Tailwind 4.3.4, vanilla-extract 1.21.3, Motion 14.9.1, GSAP 3.15.1, uPlot 1.6.32, Observable Plot 0.6.18, visx 4.0.0, Zustand 5.0.16, nanostores 1.5.5, @preact/signals-core 1.14.5, Konva 10.9.1, PixiJS 8.22.0, Bits UI 2.20.5, Ark UI 5.40.0, radix-ui 1.8.1, json-schema-to-typescript 16.0.0, Vitest 5.1.0, MSW 3.0.3.

This builds on [R4](r4-hf-spaces-and-architecture.md) §3.3 and §5 (`custom_app_url`, `settings_app`, `JsonRpcServer`). Only what changed or what R4 left open is re-derived here.

Labels used below:
- **[verified]**: read directly in a primary doc or in source code (or measured here).
- **[inferred]**: my conclusion from verified facts. Not stated anywhere.
- **[unverified]**: not confirmed. Needs a test or a question to Pollen.

---

## TL;DR

1. **Serve it in-process.** Set `custom_app_url = "http://127.0.0.1:8042"` on `Sonny`. The base class then builds a FastAPI `settings_app`, serves `src/sonny/static/` and runs uvicorn in a thread until `stop_event`. This is unchanged from 1.11.0 to `main` today. [verified] Bind to 127.0.0.1, not the docs' `0.0.0.0`: the page shows the camera and has no auth.
2. **Existing Reachy apps are plain.** Community phone apps draw boxes into the frame server-side with `cv2`. They then poll or MJPEG-stream JPEGs and poll REST for state, with vanilla JS. Pollen's conversation app is vanilla ES modules with a JSON-RPC WebSocket at `/rpc` (snapshot, then notifications). Pollen's own GUIs (Reachy Mini Control desktop, mobile, JS-app host) are React 19 + MUI + Emotion + Zustand + react-three-fiber. [verified]
3. **Camera: one binary WebSocket. Each message carries the JPEG *and* the boxes of that exact frame.** The browser decodes it with `createImageBitmap` and draws frame + boxes on one `<canvas>`. Sync is exact by construction. Encoding costs 0.5–2 ms per frame on this M2 Pro (measured). The newest frame wins, and it encodes only while a tab is open. MJPEG can't carry per-frame metadata. Reusing the robot's WebRTC stream in the browser goes through HF OAuth + the central relay (risk of `robot_busy`). Both lose. [verified/inferred, §3]
4. **State: reuse Pollen's `JsonRpcServer` on `/rpc`.** Use a `sonny.snapshot` call plus `sonny.event` notifications carrying a `seq`. On reconnect, take a fresh snapshot and drop deltas with `seq ≤ snapshot.seq`. SSE also works (FastAPI 0.143 has native `EventSourceResponse` with `Last-Event-ID`), but `/rpc` is the ecosystem's pattern and the daemon relay forwards it. [verified]
5. **Most data already exists. It just goes to the session log, not the bus.** `EventBus` carries 16 `EventType`s. The rich records (`attention`, `episode_*`, `nudge`, `line`, `owner.spoke`, `tool_call`, `llm_call`, `wakeup` with the full state snapshot, `detections`, `reconnect`, `move_*`) go only through `LogWriter.write`. One tap on that writer + a bus subscription + a per-frame hook in `VisionWorker.step` + a 1 Hz health poll covers the whole page. [verified]
6. **Frontend: Vite 8 + Svelte 5 + TypeScript (pinned to 6.0.x for now) + plain CSS with custom-property tokens.** Add no component library, plain Canvas2D for boxes, and uPlot or hand-drawn SVG for the session timeline. Build into `src/sonny/static/` with `base: "/static/"`; `uv_build` ships everything under the module dir. React 19 is the alternative if aligning with Pollen's stack matters more than a small, bespoke bundle.
7. **Develop without the robot.** A small replayer serves the same FastAPI app. It feeds `logs/session-*.jsonl` into `/rpc` at N× speed and uses the Mac webcam + the real RF-DETR detector for `/ws/camera`. The Vite dev server proxies `/rpc`, `/ws`, `/api` to it.

---

## 1. How existing Reachy Mini apps do web UIs

### 1.1 What the Spaces actually ship

| App | Stack | Video to browser | Boxes | State to browser |
|---|---|---|---|---|
| [`itsMarco-G/reachy_phone_home`](https://huggingface.co/spaces/itsMarco-G/reachy_phone_home/blob/main/reachy_phone_home/web_ui.py) | One 45 KB `static/index.html`, inline `<script>`, Google Fonts (Space Grotesk, Manrope) | MJPEG `GET /stream.mjpg` (`multipart/x-mixed-replace`, `cv2.imencode`, ~10 fps loop with `sleep(0.1)`) | Drawn into the frame server-side | Polls `GET /logs?since=<id>`; `setInterval` for aspect ratio |
| [`yozkut/judgy_reachy_no_phone`](https://huggingface.co/spaces/yozkut/judgy_reachy_no_phone/blob/main/judgy_reachy_no_phone/main.py) | Vanilla `static/index.html` + `main.js` + `style.css`, system font stack | Every frame JPEG q85 → **base64 in JSON** at `GET /api/video-frame`, polled by `fetch` and set as a `data:` URL | `detector.draw_detections(frame)` server-side | Polls `GET /api/status` |
| [`pollen-robotics/hand_tracker_v2`](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2/blob/main/hand_tracker_v2/main.py) (R4) | Plain JS in `static/` | MJPEG `StreamingResponse` | Server-side | `GET /ready` poll, `POST /set_toggles` |
| [`reachy_mini_conversation_app`](https://github.com/pollen-robotics/reachy_mini_conversation_app/tree/main/src/reachy_mini_conversation_app/static) (`e901925`) | **Vanilla ES modules**, no bundler: `js/api.js`, `views/*.js`, `components/*.js`, an animated "orb" | none | – | **JSON-RPC 2.0 over WebSocket `/rpc`**: `rpcCall()`, `subscribe(method, cb)`, `untilReady()` retry. The conversation panel **buffers notifications while it loads history, then replays them**. This is the snapshot + delta pattern. The orb maps activity reasons (`user_speech_started`, `tool_call_received`…) to a `data-state` attribute driven by CSS. |

All [verified] by reading the files above (HF MCP `hf_fs` + `gh api`, 2026-11-04).

Takeaways [inferred]:
- Nobody streams boxes separately. Drawing them into the JPEG is simple, but it fixes the styling server-side and costs a `cv2` draw + encode per frame even with no viewer.
- The judgy base64-JSON polling is the anti-pattern: about 33% size overhead, one HTTP round trip per frame, and an encode on every frame regardless of viewers.
- The conversation app's `/rpc` client is the best-engineered piece, and it is small. Copy its shape: one shared socket, `subscribe(method)`, retry until ready, and buffer during the snapshot.
- Generic look: the community UIs are the usual centered cards, gradient headers and emoji badges. The conversation app's orb is the one element with character: a single, stateful, animated object that says "listening / thinking / speaking" without text.

### 1.2 Pollen's own frontends

- **The robot's web dashboard is deprecated.** The daemon's `/` now serves a "Web Dashboard Deprecated" page pointing to the **Reachy Mini Control** desktop app. That is already the case in our pinned 1.11.0. [verified: [`daemon/app/main.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/app/main.py), our venv copy] On 2026-10-16 the docs switched "dashboard" to Reachy Mini Control throughout ([#1469](https://github.com/pollen-robotics/reachy_mini/commit/4c2b51a)). [verified]
- **Reachy Mini Control** ([`reachy-mini-desktop-app`](https://github.com/pollen-robotics/reachy-mini-desktop-app), v0.9.32, Tauri) and the **mobile app** ([`reachy_mini_mobile_app`](https://github.com/pollen-robotics/reachy_mini_mobile_app), v0.11.6, Tauri) are built with React 19, MUI 7/9 + Emotion, Zustand 5, react-three-fiber/drei (a live 3D robot), framer-motion/Motion, Vite 7. [verified: their `package.json`]
- **JS SDK + host shell** (`ts/` in `reachy_mini`): the `ts/host` shell is React 19 + MUI + Emotion. The guide says it's React+MUI "for the host shell (and only the host)", and apps inside the iframe can use any framework ([APP_CREATION_GUIDE §13.1](https://github.com/pollen-robotics/reachy_mini/blob/main/ts/APP_CREATION_GUIDE.md)). The SDK peer-depends on React 18/19. [verified]
- New since R4: `ts/host` (embed/host protocol, mobile handoff), `ts/animation` (safe-return, motion presets), and a `connectToHost()` `handle.media.attachVideo(el)` that fixes a late-mount black-video race. These matter only for a JS app, not for a page served by our Python app. [verified]

### 1.3 How the gear/UI link works now, and on a Mac
- Docs on `main`: "Reachy Mini Control opens this UI as soon as the server answers", at `localhost:8042` (Lite) or `reachy-mini.local:8042` (Wireless). Routes added in `run()` exist only once `run()` starts, so the page must retry (`untilReady`). [verified: [apps.md](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/SDK/apps.md#optional-web-ui-for-your-app), commit [6f656a1](https://github.com/pollen-robotics/reachy_mini/commit/6f656a1)]
- Sonny runs on the Mac in network mode, started by `uv run sonny`, not by the daemon. Control only knows apps the daemon launched, so **no gear icon will appear for Sonny. We open `http://127.0.0.1:8042` ourselves**, for example with an optional `webbrowser.open` on "ready". [inferred from `manager.py` and the daemon owning the app subprocess]

---

## 2. Serving model

### 2.1 What the base class does (identical in 1.11.0 and `main`)
Our venv's `apps/app.py` and `jsonrpc_server.py` diff clean against `main@25756e7`. [verified]
- `__init__`: if `custom_app_url` is set and `dont_start_webserver` is False → `self.settings_app = FastAPI()`, plus a **no-cache middleware** on every response. It mounts `<package of the app class>/static` at `/static` and serves `static/index.html` at `/`. For `sonny.main.Sonny` that is **`src/sonny/static/`**. [verified: [`app.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/app.py)]
- `wrapped_run()`: it parses host and port from `custom_app_url`, then starts `uvicorn.Server` in a thread *before* connecting to the robot. It waits on `self.stop_event`, then sets `server.should_exit` and joins. `finally:` sets `stop_event` and joins the server thread. [verified]
- uvicorn skips signal handlers off the main thread (`capture_signals` checks `main_thread()`), so Sonny's Ctrl-C handling is untouched. [verified: uvicorn 0.54 `server.py`]

### 2.2 Choices for Sonny
- **In-process (recommended)**: routes are added on `self.settings_app` in `Sonny.__init__` (static) and in `run()` (live feeds). It shares the `EventBus`, `MediaHub` and `SessionLog` objects directly: no IPC, and one lifecycle. [inferred]
- **A separate server process**: this needs IPC for frames (shared memory or a socket) and for events, and its own lifecycle. It brings nothing here, since Sonny already is the process that holds the data. Reject.
- **Bind 127.0.0.1**: `wrapped_run` passes the URL's hostname straight to uvicorn, so `http://127.0.0.1:8042` works. `0.0.0.0` would expose an unauthenticated camera feed to the LAN [inferred]. Make the port configurable in `config.toml`; 8042 is the ecosystem default.
- **CORS**: none needed. Page, `/rpc` and `/ws/camera` share one origin. In dev, Vite's proxy keeps it same-origin too (§7). [inferred]
- **Shutdown gotcha** [verified in uvicorn source; effect inferred]: on `should_exit` uvicorn closes WebSockets with code **1012**, but it *waits* for running HTTP tasks with `timeout_graceful_shutdown=None` (the base class doesn't set one). An endless MJPEG or SSE generator would therefore **hang Sonny's quit**. WebSockets don't have this problem, which is one more reason to avoid long-lived HTTP streams. If one is ever added, its generator must watch `stop_event`.
- **Thread → loop hand-off**: vision, voice, link and log writes happen on worker threads, and `JsonRpcServer.broadcast_threadsafe` uses `run_coroutine_threadsafe` onto the uvicorn loop. It **drops events while no peer is connected** ("by design"), so the snapshot call is mandatory. [verified: [`jsonrpc_server.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/jsonrpc_server.py)]
- **Daemon relay bonus**: if Sonny ever runs on the robot, the daemon forwards non-`apps.*` JSON-RPC from WebRTC and `/ws/sdk` clients to the app's `/rpc` ([`jsonrpc_relay.py`](https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/jsonrpc_relay.py)). Staying on `/rpc` keeps that door open. [verified; robot-side use inferred]

### 2.3 Packaging the Vite bundle
- Vite: `root: "web/"`, `build.outDir: "../src/sonny/static"`, `emptyOutDir: true`, and **`base: "/static/"`**, because assets are mounted under `/static` while `index.html` is served at `/`. [verified: Vite 8 [`build.outDir`](https://github.com/vitejs/vite/blob/v8.3.5/docs/config/build-options.md), base in shared options; mount path from `app.py`]
- `uv_build` puts the whole module tree under `module-root` in the wheel, minus default and configured excludes. It is the equivalent of `include_package_data` and needs no package-data line (unlike the setuptools template). [verified: [uv build-backend docs](https://github.com/astral-sh/uv/blob/main/docs/concepts/build-backend.md)] The docs list no `.gitignore` handling, so a gitignored `static/` still ships if it was built before `uv build`. [inferred]
- HF Space publishing installs from a snapshot with `pip install`, and nothing runs `npm` (R4 §1.1). So the **built `static/` must be in the Space's git tree**. For this public GitHub repo, either commit the build or build it in the publish step/CI and push to the Space only. Owner decision (Q3).

---

## 3. Transport for the live camera + overlays

### 3.1 What the frame source gives us [verified: `media.py`, `vision.py`, `detector.py`, `logs/`]
- `MediaHub.latest_frame()` → `(BGR ndarray, arrival_t)`. 720p ("~18 ms per 720p frame"), about **30 fps** received (`frames: 30–33` in `detections` records).
- `VisionWorker.step()` detects each new frame once, **only while a session is watched** (not idle, paused, Blind or after time's up). So outside a session there are frames but no boxes.
- `Detections` = `frame_width`, best `phone: Box`, best `person: Box`, `head_moving`. `Box` = conf + x0,y0,x1,y1 in frame pixels. There is **no face class** (RF-DETR on COCO), and all other boxes are dropped in `best_boxes()`. To show "everything RF-DETR sees", `best_boxes` would also have to return the raw `xyxy/conf/class_id` list. That is cheap, since the arrays already exist.
- The per-frame hook goes in `step()` right after `detect()`: `frame`, `t`, `d`, `detect_ms`, plus `rules.state` after `observe`.

### 3.2 Options

| | Latency | Mac CPU | Frame↔box sync | Simplicity | Verdict |
|---|---|---|---|---|---|
| **(a) MJPEG** (`multipart/x-mixed-replace` into `<img>`) | Low | One encode per sent frame | **None.** An `<img>` exposes no per-frame timestamp, so boxes must be burned in server-side | Simplest; what community apps do | Long-lived HTTP: blocks uvicorn shutdown (§2.2) and holds one of the 6 HTTP/1.1 connections per origin ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)) |
| **(b) JPEG + metadata over a binary WebSocket** → `createImageBitmap(blob)` → `<canvas>` | Low (LAN/loopback) | Same encode; decode in browser off-main-thread | **Exact**: header and JPEG travel in one message | One route, about 40 lines a side | **Recommended** |
| **(c) WebRTC** (aiortc/fastrtc re-encode on the Mac, or the robot's own stream via the JS SDK) | Lowest for re-encode; browser-direct adds a separate path | aiortc H.264/VP8 encode in Python is heavier than JPEG [inferred]; fastrtc pulls in Gradio | Hard: the browser only gets RTP-level `captureTime`/`rtpTimestamp` via `requestVideoFrameCallback` ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)), and mapping that to the Mac's `arrival_t` is guesswork | Heavy. The JS-SDK path needs HF OAuth + the central relay, and the central rejects a second consumer with `robot_busy`, or ends sessions with `robot_busy_local_app` when a local app holds the robot ([central `app.py`](https://github.com/pollen-robotics/reachy_mini_central_signalling/blob/main/app.py)) | Reject |
| **(d) Boxes only, drawn over a `<video>`** | – | Lowest on our side | Same problem as (c): the video arrives by a different path with different delay | Needs (c) for the video anyway | Reject; useful only as a "boxes-only" mode (no pixels) |

Encode cost measured on this Mac (M2 Pro, `cv2.imencode` q75, noisy worst-case frame; real frames compress smaller) [verified, measured 2026-11-04]: **1280×720 ≈ 2.0 ms / ~200 KB**, **960×540 ≈ 1.2 ms / ~125 KB**, **640×360 ≈ 0.55 ms / ~73 KB**. At 15 fps and 960×540 that is about 2% of one core, and about 2 MB/s on loopback. Negligible next to the detector. [inferred]

### 3.3 Recommended shape (b)
- `GET /ws/camera`, binary frames: `u32 header_len | JSON header | JPEG`. Header example: `{seq, t: arrival_t, w, h, scale, boxes: [{cls, conf, x0,y0,x1,y1}], state, head_moving, detect_ms, watching}`. The boxes stay in source-frame pixels and the client scales them, so a downscaled JPEG stays aligned. [inferred]
- **Latest-frame-wins backpressure.** The vision hook puts `(frame, d)` into a 1-slot mailbox. An asyncio sender per client awaits `send_bytes` and then takes the newest frame, so a slow tab skips frames instead of queuing them. Encode **only while ≥1 client is connected**, and off the vision thread (`asyncio.to_thread`) so the detector's 18 ms budget is untouched. [inferred]
- Outside a session, send raw frames at a lower rate (e.g. 5 fps) with `watching: false` and no boxes. Whether the page should show the camera at all when no session is running is an owner question (Q2).
- Stale-frame indicator: the client compares `t` to `Date.now()/1000`, which is the same clock on the Mac (both `time.time()`). Freeze plus a "no signal for N s" badge covers Blind and media drops without extra protocol. For non-local viewers, send `server_now` in the snapshot to compute an offset. [inferred]

---

## 4. Transport for state and events

### 4.1 SSE vs WebSocket on this stack
- **WebSocket**: Starlette/FastAPI native. uvicorn uses `websockets` 15 (installed), with ping every 20 s and `ws_max_queue=32` defaults. Pollen's `JsonRpcServer` adds request/response + notifications for free. [verified]
- **SSE**: **FastAPI now ships `fastapi.sse.EventSourceResponse` + `ServerSentEvent(data, event, id, retry)`**, and the browser sends `Last-Event-ID` on auto-reconnect. No `sse-starlette` is needed. [verified: our venv's `fastapi/sse.py`] Downsides here: it's a long-lived HTTP stream (shutdown hang, §2.2), it's subject to the 6-connections-per-origin limit on HTTP/1.1 ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)), and it's one-way, so commands like "end session" or "toggle boxes" still need REST.
- **Pick `/rpc` (JsonRpcServer).** It's one socket for state + commands, it's the conversation app's proven client shape, and it's relay-compatible. The video lives on its own socket so a big frame never delays a Nudge event. [inferred]

### 4.2 Where each piece of data comes from today [verified: source + `logs/session-20261103-221132-002477.jsonl`, 301 records]

| UI panel | Source now | Hook |
|---|---|---|
| Session: task, timer, paused/Blind/time-up, excuse, hooks | `FocusClock.snapshot()` (already sent to the LLM in every `wakeup.payload.state`). It rounds to 0.1 min, so the UI wants raw `started_at`/`duration_s`/`paused_since` to tick locally | `sonny.snapshot` RPC + push on change |
| Attention State, Distraction Episodes, Nudges | Bus: `phone_use.*`, `away.*`, `focused.resumed`, `excuse.expired`. Log only: `attention`, `episode_opened/closed`, `nudge` | bus subscription + log tap |
| Agent transcript, tool calls, LLM latency | Log only: `owner.spoke` (text), `line` (text, tts_start/end, interrupted), `tool_call` (name, args, result), `llm_call` (model, latency, first_text_s), `wakeup` (payload), `wake`, `bare_wake`, `barge_in` | log tap |
| Agent phase: idle / listening / thinking / speaking / tool | Not emitted. `AgentActivity` and `LineTracker` already read the Pipecat frames (`LLMFullResponseStart`, `FunctionCallsStarted`, `BotStarted/StoppedSpeaking`, `Interruption`) | a Pipecat **observer** on the `PipelineWorker` (`observers=[...]`, `observe_every_push=False`) ([Pipecat docs](https://docs.pipecat.ai/api-reference/server/utilities/observers/observer-pattern.md)), or a callback from `AgentActivity` |
| Detector stats | Log: `detections` once a second (fps, detect_ms, confs) | log tap |
| Link / health | `LinkWatch.down` (`media_drop`, `daemon_unreachable`, `daemon_error`), `reconnect` records, `blind.started/ended`, `link.restored`. `MediaHub.frames`, `audio_t`, `dropped` | 1 Hz poll → `sonny.health` |
| Head | `move_start/move_end` records; `HeadOwner.moving/sweeping` | log tap + health poll |

So the cheapest hook is a **`UiFeed`** with:
- an `on_record(type, fields)` callback added to `LogWriter.write`. Everything logged becomes streamable, and the log and the UI can never disagree.
- `bus.subscribe()` for all events, including ones that are never logged.
- the vision per-frame hook.
- a health ticker.

It assigns `seq` and keeps a ring buffer (e.g. the last 500 records) for backfill. [inferred]

### 4.3 Message schema and reconnect
- One envelope: `{seq: int, t: float, type: string, data: {...}}`. `type` reuses the log/`EventType` strings verbatim (`phone_use.started`, `line`, `tool_call`…). The session log is then literally a recording of the UI stream, which makes the mock in §7 trivial. [inferred]
- `sonny.snapshot` → `{seq, server_now, session, attention, episode, agent: {phase}, link, recent: Envelope[]}`.
- Client: connect → subscribe and **buffer** → call snapshot → apply → replay buffered items with `seq > snapshot.seq`. On close, back off (0.5 → 5 s) and repeat. This is exactly the conversation app's history-then-replay pattern. A `seq` gap means "re-snapshot". [inferred]

### 4.4 Typed schema shared by Python and TS
- **Pydantic models → JSON Schema → TS** (recommended). Pydantic is already present via FastAPI. Use a discriminated union on `type`, `TypeAdapter(...).json_schema()` → `json-schema-to-typescript` 16 → `web/src/protocol.gen.ts`. A test fails if the generated file is stale. Python can then also validate what it sends. [inferred; tools' versions verified on PyPI/npm]
- Hand-written TS mirror: fine for about 15 message types, but it drifts silently. Add runtime parsing (Valibot 1.5 / Zod 4) only at the socket boundary if wanted. This is a good TS exercise either way.

---

## 5. Frontend stack

### 5.1 Framework

| | Bundle / runtime | TS story | Fit for a live, bespoke instrument panel | Notes |
|---|---|---|---|---|
| **Svelte 5.57** (runes) | Compiler, tiny runtime [verified concept; size unverified] | `<script lang="ts">`; typecheck via `svelte-check` (peer `typescript ^5 \|\| ^6`, **not 7**) [verified] | Scoped CSS by default pushes you to write your own styles; `$state`/`$derived` map neatly onto a stream store | **Recommended** |
| React 19.3 | Largest of the set | Best-known TSX path; `tsc` checks templates directly | Same stack as Pollen's GUIs (Control, mobile, host) | Pick if you want Pollen alignment or R3F for a 3D Sonny |
| Solid 1.9 | Tiny, fine-grained signals | TSX | Great for 30 fps updates | Smaller ecosystem; 2.0 not yet `latest` |
| Preact 11 (+ signals) | Tiny | TSX | React-like with less weight | Fewer learning resources than React |
| Vanilla TS / Lit 3.3 | Smallest | Plain TS: the most "pure TS" practice | Conversation app proves vanilla works | Manual DOM diffing gets tedious beyond ~5 panels |

- **TypeScript version** [verified]: npm `latest` is **7.0.2** (the native port), but `svelte-check` and `typescript-eslint` (`<6.1.0`) don't support it yet. Pin **`typescript@~6.0.3`** and revisit.
- Vite **8.3** (Rolldown-based) is current. `@vitejs/plugin-react` 6 needs Vite 8, and `@sveltejs/vite-plugin-svelte` 7.3 is current. Pollen's apps are still on Vite 7. [verified]

### 5.2 Styling, components, motion, canvas, charts, state
- **Styling: plain CSS + custom-property tokens** (`--ink`, `--signal-phone`, `--grid`, spacing and type scales) in one `tokens.css`, plus Svelte's scoped styles. Tailwind 4.3 is fine and fast, but utility-first + defaults is how dashboards end up looking alike. vanilla-extract 1.21 gives typed tokens if you want TS there too. [inferred/opinion]
- **Component libraries: skip** (shadcn/ui, Radix, Ark, Bits UI 2.20). This page has almost no form controls, and these kits' default skins are the "generic dashboard" look. If a headless primitive is needed later (a dialog or tooltip), Bits UI (Svelte) or Radix (React) are unstyled. [inferred]
- **Motion**: CSS transitions + `@starting-style` and the View Transitions API cover state changes. Motion 14.9 works for React/vanilla, and Svelte has built-in `transition:`/`animate:`. GSAP 3.15 is overkill here. [inferred]
- **Canvas overlay: plain Canvas2D.** Two to six boxes per frame plus labels; Konva 10 or PixiJS 8 add weight for nothing. Use `devicePixelRatio` scaling and draw image + boxes in one `requestAnimationFrame`. [inferred]
- **Session timeline**: a horizontal band of Focused / Phone Use / Away / Blind / Paused segments plus Nudge ticks is easiest as **hand-drawn SVG** (a good TS exercise). uPlot 1.6.32 suits the 1 Hz detector sparklines: tiny and fast, but no release since 2025-03. Observable Plot 0.6.18 is nicer for a post-session summary, and visx 4 is React-only. [verified versions]
- **State**: Svelte runes need no library. For React: Zustand 5 (what Pollen uses). For framework-agnostic code: nanostores 1.5 or `@preact/signals-core`. Keep one `stream.ts` module that owns the sockets and exposes typed stores. [inferred]

---

## 6. Design direction pointers (brief)

- **Treat it as an instrument, not a dashboard.** One dominant live surface (the camera, with Sonny's "gaze") and secondary readouts on a strict grid. Monospace numerals (tabular figures) for timers, and a restrained palette where colour *means* state (phone = one signal hue, Away = another, Blind = desaturated/hatched). No decorative gradients or cards-in-cards. [opinion]
- **Credible references:**
  - **Foxglove**: image panels + `ImageAnnotations`, whose schema timestamps annotations to match a specific image (the same idea as our per-frame header) ([schema](https://github.com/foxglove/foxglove-sdk/blob/main/schemas/jsonschema/ImageAnnotations.json)) [verified].
  - **Rerun**: a timeline-first viewer, where scrubbing the session timeline is the hero interaction ([rerun.io](https://rerun.io/)).
  - **Pollen's Reachy Mini Control**: a live 3D robot via react-three-fiber. A small Sonny head showing the real head pose would be the distinctive touch; meshes are in `reachy_mini/descriptions/…/mjcf/assets/*.stl` [verified].
  - **Teenage Engineering OP-1 / Linear / Vercel**: dense, typographic, one accent colour, purposeful motion.
- **Give the agent one "orb"-like stateful object**, as the conversation app does, rather than text badges for listening/thinking/speaking.

---

## 7. Dev ergonomics

- **Vite dev server** on :5173. Proxy `'/rpc': {target: 'ws://127.0.0.1:8042', ws: true}`, `'/ws': {…, ws: true}`, `'/api': 'http://127.0.0.1:8042'`. Avoid `rewriteWsOrigin` (Vite warns it can open CSRF). [verified: [Vite 8 `server.proxy`](https://github.com/vitejs/vite/blob/v8.3.5/docs/config/server-options.md)]
- **Robot-free backend** (`uv run sonny-ui-dev`, a sketch): the same FastAPI routes and the same `UiFeed`, fed by:
  - **Events**: a replayer over `logs/session-*.jsonl` / `idle.jsonl` at `--speed N`. Their `type` strings *are* the envelope types (§4.3). The 2026-11-03 session has every panel's data: 21 record types incl. `phone_use.started`, `nudge`, `tool_call`, `line`, `wakeup`.
  - **Camera**: the logs hold no frames (by design, `session_log.py`). Use `cv2.VideoCapture(0)` (Mac webcam) through the **real `load_detector()`**, which takes any BGR frame, so the boxes are genuine. Or loop a clip kept in `.scratch/`.
- **Pure-frontend mocking**: MSW 3 can mock WebSockets in the browser [unverified for v3 specifics]. The Python replayer is preferable because it exercises the real protocol and schema.
- **Tests**: Vitest 5 for the stream store (snapshot + replay + seq-gap logic). The Python side tests `UiFeed` against recorded logs, in this repo's pytest setup.

---

## Recommendation

**Serving**:
- In-process. `Sonny.custom_app_url = "http://127.0.0.1:8042"` (port in `config.toml`), using the base class's `settings_app` + uvicorn thread tied to `stop_event`.
- The Vite build goes to `src/sonny/static/` with `base: "/static/"`. `uv_build` ships it.
- No CORS, no second process. Open the page ourselves, because Control won't show a gear for a Mac-run app.

**Transport**:
- **State + commands**: Pollen's `JsonRpcServer` on `/rpc`, with `sonny.snapshot` and `sonny.event` notifications (`{seq, t, type, data}`, types = existing log/`EventType` strings) fed by one `UiFeed` (log-writer tap + bus subscription + 1 Hz health). Seq-based snapshot/replay on reconnect.
- **Camera**: a dedicated binary WebSocket `/ws/camera`. Each message is header (boxes, state, `t`) + JPEG of the same frame (960×540, q≈75, ≤15 fps in session, ~5 fps idle). Latest-frame-wins, encoded only when someone watches, drawn on a `<canvas>`.

**Frontend**: Vite 8 + **Svelte 5** + TypeScript ~6.0 + plain CSS tokens, no component kit, Canvas2D overlay, SVG timeline (uPlot for sparklines), generated TS types from Pydantic.

**Trade-offs accepted**:
- JPEG over WS uses more bandwidth than WebRTC. That's irrelevant on loopback and it buys exact box sync plus a trivial server.
- Svelte departs from Pollen's React+MUI. It's smaller and nudges toward bespoke styling, but has fewer examples than React and needs TS ≤6 for `svelte-check`.
- Bespoke CSS costs more design time than shadcn. That's the point of the brief.
- Tapping the log writer couples the UI to log record shapes. That's a feature (one source of truth), but log field renames now break the UI, which the generated schema + test catch.

**Rejected**: Gradio/fastrtc (heavy, generic look; R4 already found the official apps don't use them), MJPEG (no metadata, shutdown hang), robot-WebRTC-in-browser (OAuth + relay + `robot_busy` + no sync), a separate server process.

---

## Open questions for the owner

1. **Svelte 5 or React 19?** Do you want transferable React/TSX practice and Pollen alignment (and an easy react-three-fiber Sonny head), or the lighter bespoke route?
2. **Camera outside a Focus Session**: show a live feed when Sonny isn't watching, show nothing, or show it only on demand? Detection runs only in sessions today. Should the dashboard ask the worker to detect while idle (CPU + the CoreML reload behaviour in `detector.py`)?
3. **Built `static/` in git?** Commit the bundle to the GitHub repo, or build it only when publishing to the Space? It must be in the Space either way.
4. **Read-only or control too?** Should the page start/end/pause sessions, grant an Excuse, or toggle box/raw views? Commands would reuse `/rpc` methods and the existing `SessionTools`, but that is a design decision (the agent decides; ADR 0003).
5. **All detections or only the two the rules use?** Showing every RF-DETR class means widening `best_boxes`. A face box would need a new model.
6. **LAN access**: is 127.0.0.1-only OK, or do you want to watch from a phone or iPad, which needs `0.0.0.0` + some auth token?
7. **Auto-open the browser on "ready"**, or print the URL only?
8. **History view**: should the dashboard also browse past `logs/session-*.jsonl` (timeline replay), or only show live data in v1?

---

## Sources

Reachy Mini (Pollen):
- `reachy_mini` `apps/app.py`: https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/app.py
- `apps/jsonrpc_server.py`: https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/apps/jsonrpc_server.py
- `daemon/jsonrpc_relay.py`: https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/jsonrpc_relay.py
- `daemon/app/main.py` (deprecated dashboard, CORS regex): https://github.com/pollen-robotics/reachy_mini/blob/main/src/reachy_mini/daemon/app/main.py
- Apps doc (web UI section): https://github.com/pollen-robotics/reachy_mini/blob/main/docs/source/SDK/apps.md · https://huggingface.co/docs/reachy_mini/main/SDK/apps
- Docs commits #1469 (Control replaces dashboard) https://github.com/pollen-robotics/reachy_mini/commit/4c2b51a, #1445 https://github.com/pollen-robotics/reachy_mini/commit/6f656a1
- JS SDK guide: https://github.com/pollen-robotics/reachy_mini/blob/main/ts/APP_CREATION_GUIDE.md · `ts/host`: https://github.com/pollen-robotics/reachy_mini/tree/main/ts/host
- Central signalling (`robot_busy`): https://github.com/pollen-robotics/reachy_mini_central_signalling/blob/main/app.py
- Reachy Mini Control: https://github.com/pollen-robotics/reachy-mini-desktop-app · Mobile: https://github.com/pollen-robotics/reachy_mini_mobile_app
- Conversation app UI: https://github.com/pollen-robotics/reachy_mini_conversation_app/tree/main/src/reachy_mini_conversation_app/static
- PyPI reachy-mini: https://pypi.org/project/reachy-mini/ · npm SDK: https://www.npmjs.com/package/@pollen-robotics/reachy-mini-sdk

Community Spaces:
- https://huggingface.co/spaces/itsMarco-G/reachy_phone_home (`web_ui.py`, `static/index.html`)
- https://huggingface.co/spaces/yozkut/judgy_reachy_no_phone (`main.py`, `static/main.js`)
- https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2

Web platform / libraries:
- MDN EventSource (connection limit): https://developer.mozilla.org/en-US/docs/Web/API/EventSource
- MDN requestVideoFrameCallback: https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback
- MDN createImageBitmap: https://developer.mozilla.org/en-US/docs/Web/API/Window/createImageBitmap
- FastAPI SSE (`fastapi/sse.py`, 0.143.0): https://fastapi.tiangolo.com/tutorial/server-sent-events/
- uvicorn server shutdown: https://github.com/encode/uvicorn/blob/master/uvicorn/server.py
- Vite 8 server.proxy / build options: https://github.com/vitejs/vite/blob/v8.3.5/docs/config/server-options.md · https://github.com/vitejs/vite/blob/v8.3.5/docs/config/build-options.md
- uv build backend: https://github.com/astral-sh/uv/blob/main/docs/concepts/build-backend.md
- Pipecat observers: https://docs.pipecat.ai/api-reference/server/utilities/observers/observer-pattern.md
- svelte-check peer deps: https://www.npmjs.com/package/svelte-check · typescript-eslint: https://typescript-eslint.io/users/dependency-versions
- json-schema-to-typescript: https://github.com/bcherny/json-schema-to-typescript
- Foxglove ImageAnnotations schema: https://github.com/foxglove/foxglove-sdk/blob/main/schemas/jsonschema/ImageAnnotations.json · Rerun: https://rerun.io/

Local (this repo): `src/sonny/{main,events,session_log,media,vision,detector,attention,link}.py`, `src/sonny/focus/{conductor,clock}.py`, `src/sonny/voice/{pipeline,activity}.py`, `logs/session-20261103-221132-002477.jsonl`, `docs/research/r4-hf-spaces-and-architecture.md`.
