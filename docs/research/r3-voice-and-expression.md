# R3: Voice interaction and expressive nudges

Research date: 2026-10-10. Scope: how Sonny (a wireless Reachy Mini) should talk with the user, pull a focus session ("next 25 minutes I'm writing the spec") out of speech, and nudge in character when the user drifts to their phone. The SDK internals and the vision pipeline are covered by other tracks.

Repo snapshots I read locally (cloned 2026-10-10):

- `pollen-robotics/reachy_mini_conversation_app` @ `2e43e80` (v1.0.1 + fixes)
- `pollen-robotics/reachy_mini` (SDK) @ `fbdbca3`
- `huggingface/speech-to-speech` @ `6680878`

Permalinks below point at those commits.

---

## TL;DR

1. **Pollen's official conversation app is the template to copy.** It is an Apache-2.0 Python app ([repo](https://github.com/pollen-robotics/reachy_mini_conversation_app)). It talks the **OpenAI Realtime protocol** (through the `openai` SDK) to a **Hugging Face realtime backend**. That backend is either Pollen's hosted server (no API key) or `huggingface/speech-to-speech` running on your own laptop. In July 2026 the app dropped its OpenAI Realtime and Gemini Live backends ([PR #444, "Consolidate the app around the default backend"](https://github.com/pollen-robotics/reachy_mini_conversation_app/pull/444)). Tools are plain Python classes the model calls by function calling, and `play_emotion` is one of them. The "speaking wobble" now lives in the SDK/daemon (`robot.enable_wobbling()`). The app also has a `say()` / `conversation.say` hook that makes the robot speak without a user turn, which is exactly what proactive nudges need.
2. **Recommended speech stack for v1:** run **HF `speech-to-speech` on the Mac** as a local OpenAI-Realtime-compatible server: Silero VAD + Smart Turn, Parakeet-TDT STT on MLX, a Qwen3 LLM on MLX or a hosted LLM, and Qwen3-TTS or Kokoro on MLX. This is the same backend Pollen runs in production "for thousands of Reachy Mini robots" ([s2s README](https://github.com/huggingface/speech-to-speech)). Our client code is the standard Realtime client. Moving to OpenAI `gpt-realtime-2.1-mini` later is just a base-URL and key change.
3. **Audio I/O: use the robot's own mic array and speaker from the Mac** (SDK WebRTC media backend). The XMOS XVF3800 array does hardware echo cancellation, so the robot does not hear itself and barge-in works ([media_advanced_controls.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/platforms/reachy_mini/media_advanced_controls.md)). The daemon also wobbles the head on incoming WebRTC audio. Using the Mac's mic and speakers breaks both of these.
4. **Session intent comes from function calling.** Use a `start_focus_session(task, duration_minutes | end_time, strictness)` tool, plus in-session tools: status, extend, pause, snooze nudges, "this phone use is OK", and end. The session state machine and its timers live in our app. The LLM only parses requests and phrases replies.
5. **Expressive behaviour:** Pollen publishes **85 recorded moves**, each with an `.ogg` sound, in [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library). They are played with `RecordedMoves(...).get(name)` and `mini.play_move(move, sound=...)`. The conversation app already keeps a curated "excellent/ok" list and an intent-to-move map that we can reuse. Build a **deterministic escalation ladder**: look, then antennas or posture, then a short spoken goal reminder, then a playful "reprimand". Add a cooldown, count distraction *episodes* rather than frames, give positive reinforcement on return, and end with a recap.
6. **HRI evidence is thin but points one way.** In a Wizard-of-Oz study, goal reminders and emotional support re-focused distracted students 75–84% of the time. Users saw the reminder robot as "peer pressure" and the supportive robot as a "social other" ([Cho et al. 2025, arXiv:2510.23860](https://arxiv.org/abs/2510.23860)). More social cues can *raise* reactance ([Ghazali et al. 2018](https://doi.org/10.1016/j.chb.2018.05.016)). Interruptions hurt less at task boundaries ([Adamczyk & Bailey 2004](https://doi.org/10.1145/985692.985727); [Iqbal & Bailey 2008](https://doi.org/10.1145/1357054.1357070)). Design implications: brief, goal-referencing, mostly non-verbal first, warm tone, user-tunable.
7. **Prior art exists on the HF Hub.** Several community Reachy Mini apps already do phone-distraction nudging: Judgy Reachy No Phone, Focus Guardian, Focus Buddy, Dodo Focus, Reachy Mini Pomodoro, and Reachy Phone Home (details in §4.5). None of them combine a full conversational session agent with an escalation ladder, which is our angle.

---

## 1. The official Reachy Mini conversation app

### 1.1 What it is

- Repo: <https://github.com/pollen-robotics/reachy_mini_conversation_app>. Apache 2.0, package `reachy_mini_conversation_app` v1.0.1, Python ≥3.11 ([pyproject.toml](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/pyproject.toml)). It is also published as an HF Space and installable as a Reachy Mini app (entry point `reachy_mini_apps`).
- README summary ([README](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/README.md)):
  - "Low-latency audio conversation through the Hugging Face realtime backend, using the built-in server or a local endpoint."
  - "Layered motion system queues primary moves (dances, emotions, goto poses, breathing) while blending speech-reactive wobble."
- Dependencies are tiny: `openai==2.28.0` ("used by the Hugging Face backend"), `reachy-mini>=1.10.0rc5`, `reachy_mini_dances_library`, `mcp`, `huggingface-hub` ([pyproject.toml](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/pyproject.toml)). `fastrtc` and Gradio were removed in v0.9/v0.10 ([releases](https://github.com/pollen-robotics/reachy_mini_conversation_app/releases)).

### 1.2 Speech/LLM stack

- **Single backend:** `HuggingFaceRealtimeHandler` ([huggingface_realtime.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/huggingface_realtime.py)) opens an `AsyncOpenAI(...).realtime` connection against an "OpenAI-compatible" endpoint. The session config (`_get_session_config`) sets:
  - `server_vad` with `interrupt_response=True`
  - input transcription (`gpt-4o-transcribe`, a model name the HF server accepts)
  - native-rate 16 kHz PCM, a voice, `tools=...`, and `tool_choice="auto"`
- **Two connection modes** ([README "Hugging Face Connection Modes"](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/README.md)):
  - `deployed` (the default): Pollen's hosted server, allocated through the Space proxy `https://pollen-robotics-reachy-mini-realtime-url.hf.space/session` ([config.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/config.py)). It "does not require an API key" and falls back to cached `hf auth login` credentials. **Unverified:** which models it runs, and any quotas or rate limits.
  - `local`: `HF_REALTIME_WS_URL=ws://127.0.0.1:8765/v1/realtime` pointing at your own [speech-to-speech](https://github.com/huggingface/speech-to-speech) server. The README documents a LAN setup where the backend runs on a laptop and the app runs on the Wireless robot, including an SSH reverse-tunnel variant.
- **History:** `openai_realtime.py` and `gemini_live.py` existed until 2026-07-02. Both were deleted in [#444](https://github.com/pollen-robotics/reachy_mini_conversation_app/pull/444), per the GitHub commits API for those paths. Older tags (≤ v0.8.0) are the reference if we ever want a direct OpenAI or Gemini handler.

### 1.3 Coupling speech with motion

- **Wobble (audio-reactive head motion)** moved into the SDK in v0.8.0 ("head wobbler is now handled on the core side", [release notes](https://github.com/pollen-robotics/reachy_mini_conversation_app/releases)). `main.py` simply calls `robot.enable_wobbling()` ([main.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/main.py)). In the SDK ([reachy_mini.py `enable_wobbling`](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/src/reachy_mini/reachy_mini.py)), audio played via `media.play_sound()` or `media.push_audio_sample()` "is analysed and converted into subtle head movements that are composed with the current target pose on the daemon side". With the LOCAL backend the wobble runs SDK-side. In every case the daemon is also told to wobble "so that daemon-side sounds … and incoming WebRTC audio also produce head movement". **Result: if Sonny's voice is pushed to the robot speaker from the Mac, the wobble comes for free.**
- **Listening/speaking states:** realtime events drive the `MovementManager` ([moves.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/moves.py)):
  - `input_audio_buffer.speech_started` → `set_listening(True)` and a flush of queued playback (barge-in)
  - `response.created` → `set_speaking(True)`
  - `response.output_audio.done` → `set_speaking(False)`

  `MovementManager` is a ~60 Hz worker thread and the only caller of `set_target`. It runs primary moves (emotions, dances, goto, breathing) one after another, freezes antennas while listening, and starts an infinite `BreathingMove` when idle.
- **Barge-in:** `LocalStream.clear_audio_queue()` calls the SDK's `media.audio.clear_player()`. That flush works on both the local GStreamer and the WebRTC backends; "the WebRTC one also tells the daemon to drop audio already queued for the speaker" ([console.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/console.py)).
- **Idle behaviour:** after 180 s without activity, the app picks a local idle tool by weight: do nothing 60%, dance 16%, emotion 16%, move head 8% ([idle_policy.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/idle_policy.py), [conversation_handler.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/conversation_handler.py)). **We would need to disable or replace this during focus sessions.** Random dances while the user writes a spec are exactly the wrong behaviour.

### 1.4 Tool calling

- Each tool is a `Tool` subclass ([core_tools.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/tools/core_tools.py)) with `name`, `description`, a JSON-Schema `parameters_schema`, an async `__call__`, and `needs_response` (set it `False` to skip the spoken follow-up). Tools run through a `BackgroundToolManager`, so long tools do not block speech. Tool results go back as `function_call_output` items, followed by `response.create`.
- Built-in tools include `play_emotion`, `dance`, `move_head`, `head_tracking`, `camera`, `sweep_look`, `remember`/`forget` (long-term memory file), `volume_control`, `go_to_sleep`, and MCP "Tool Spaces" (README tool table).
- **`play_emotion`** ([play_emotion.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/tools/play_emotion.py)) exposes coarse intents to the LLM ("happy", "impatient", "attentive", "no_firm", …) and maps them to recorded moves. It also carries Pollen's own quality ranking (`_EXCELLENT_MOVES`, `_OK_CLEAR_MOVES`). This is a useful curated starting set.
- **Profiles** are `profile.md` files: TOML front matter with `voice`, `greeting`, `default_tools`, and a Markdown system prompt ([profiles/default/profile.md](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/profiles/default/profile.md)). The default prompt enforces "1–2 sentences", "under 25 words", and light humour. External profiles and tools can be loaded from directories without forking (`REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY`, `REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY`; README "External profiles and tools").
- **Proactive speech:** `ConversationHandler.say(text)` injects a user-role text item and queues `response.create`. The docstring notes it is "not verbatim TTS (speech-to-speech may rephrase)". The app exposes it over a JSON-RPC surface at `/rpc` as `conversation.say`, alongside `conversation.interrupt` and `conversation.mic` (mute) ([console.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/console.py)).

### 1.5 Fork or extend?

| Path | How | Pros | Cons |
|---|---|---|---|
| **A. Use it unmodified as a voice front-end** | Ship a "sonny" external profile plus external tools (`start_focus_session`, …). A separate watcher process calls `/rpc conversation.say` and `conversation.mic`. | Zero fork. Fastest prototype. Upstream fixes come for free. | Tools only get `ToolDependencies` (robot, movement manager), not the handler, so session state and nudges must cross a process boundary. The idle policy still runs. The app is built around one always-on conversation. |
| **B. Fork** | Add a session/nudge module inside the app. | Everything in one process. Reuse the web UI. | ~10.5k LOC plus a UI we don't need. Upstream moves fast (v0.8 → v1.0.1 in 2 months). Merge burden. |
| **C. Own app, vendored patterns** (recommended) | Our own Python package. Copy, with attribution under Apache-2.0, the realtime handler loop, `LocalStream` record/play/flush, `MovementManager`, and the emotion resolver. | We control the session loop, the idle policy, and the vision integration. Minimal deps (`openai`, `reachy-mini`). | We own maintenance of the copied bits. |

Recommendation: do a **spike with A** (a day's work) to feel latency and voice quality, then **build C**.

---

## 2. Speech pipeline options

### 2.1 Speech-to-speech realtime APIs (cloud)

**OpenAI Realtime** ([pricing](https://developers.openai.com/api/docs/pricing), [realtime conversations guide](https://developers.openai.com/api/docs/guides/realtime-conversations))

- Per 1M audio tokens: `gpt-realtime-2.1` costs $32 in / $64 out, and `gpt-realtime-2.1-mini` costs $10 in / $20 out (cached input $0.30). Text tokens are much cheaper.
- Maximum session length is 60 minutes.
- VAD modes are `server_vad` and `semantic_vad`. Setting `create_response=false` / `interrupt_response=false` keeps VAD but lets the client decide when to respond.
- Out-of-band responses: `response.create` with `conversation: "none"` and custom `input`/`instructions`, which suits proactive nudges.
- Function calling: `function_call` items → `function_call_output` → `response.create`.

**Gemini Live** ([pricing](https://ai.google.dev/gemini-api/docs/pricing), [session management](https://ai.google.dev/gemini-api/docs/live-session), [overview](https://ai.google.dev/gemini-api/docs/live))

- The pricing page lists `gemini-3.8-live` and `gemini-3.1-flash-live-preview` at $3.00/1M audio-in (≈$0.005/min) and $12.00/1M audio-out (≈$0.018/min), with a free tier. Model naming comes from a summarised page fetch, so **verify the exact IDs before use**.
- Audio-only sessions are limited to **15 min** without context-window compression, and a connection lasts about **10 min**. Session resumption tokens are valid for 2 h.
- Features: function calling, barge-in, "proactive audio", and "affective dialog".
- A 25-minute focus session therefore **needs compression plus resumption handling**.

**Trade-offs**

- Best naturalness and lowest engineering effort.
- But: audio streams to the cloud for the whole session (a privacy issue, since the mic is always hot in your room), the cost grows with conversation length, and long silent sessions bump into session caps.
- Cost per 25-minute session with a few minutes of speech is plausibly cents. I did not measure context re-processing costs: **unverified**.

### 2.2 Cascaded pipeline on the Mac

**HF `speech-to-speech`** ([repo](https://github.com/huggingface/speech-to-speech), Apache-2.0, pip `speech-to-speech`)

- "VAD → STT → LLM → TTS, exposed through the core OpenAI Realtime GA event set over WebSocket and WebRTC … runs in production as the conversation backend for thousands of Reachy Mini robots."
- **Apple Silicon fully local:** `speech-to-speech local --mac-optimal-settings --model_name mlx-community/Qwen3-4B-Instruct-2507-4bit`. This runs Parakeet-TDT-0.6b-v3 on MLX, Qwen3-4B 4-bit on MLX-LM, and Qwen3-TTS-1.7B-CustomVoice 6-bit on mlx-audio. That is ~7.5 GB of weights, and the README says to "budget 16 GB or more of unified memory".
- **Local speech + hosted LLM:** ~8 GB. Only text goes to the provider. The default hosted model is `gpt-5.6-terra` through the Responses API, and any OpenAI-compatible provider works: HF Inference Providers, OpenRouter, a llama.cpp server, vLLM.
- **Use `serve` (not `local`)** to expose `ws://127.0.0.1:8765/v1/realtime` to our app.
- Implemented events: `session.update`, `input_audio_buffer.append`, `conversation.item.create`, `conversation.item.truncate`, `response.create`, `response.cancel`, plus tool calls and transcripts. The README calls this "a tested core subset, not … full OpenAI Realtime API equivalence". **Unverified:** whether out-of-band `conversation: "none"` responses work.
- **Turn-taking:** Silero VAD v5 plus [Smart Turn v3.2](https://huggingface.co/pipecat-ai/smart-turn-v3) (BSD-2) endpointing, on by default, with reopenable turns (`--speculative_reopen_ms` 800 ms).
- **Swappable components** (README "Supported Components"):
  - STT: Parakeet TDT, Whisper (Transformers, Faster-Whisper, Lightning-Whisper-MLX, MLX-Audio Whisper), Qwen3-ASR, OpenAI transcription.
  - TTS: Qwen3-TTS, **Kokoro-82M** (built-in on macOS), Pocket TTS, KittenTTS, OmniVoice, OpenAI-compatible `/v1/audio/speech`, MiniMax.
- **Latency:** the docs show an *example* log line `stt=0.18s llm=1.24s tts_ttfa=0.12s e2e=2.01s` ([response-latency.md](https://github.com/huggingface/speech-to-speech/blob/66808782242e66cb4daef812548984bde598cd0d/docs/response-latency.md)). That is an illustration, not a benchmark. **Measure on the user's Mac.**
- **Echo:** the packaged mic client says to "use headphones to avoid speaker feedback" or `--local_audio_block_mic_during_playback`, which disables interruption. This matters if we used the Mac's mic and speaker (§5).

**Pipecat** ([docs](https://docs.pipecat.ai))

- A general voice-agent framework with local services, including `WhisperSTTServiceMLX` (MLX Whisper, including `LARGE_V3_TURBO_Q4`) and `KokoroTTSService` (kokoro-onnx).
- It has user-idle events (`on_user_turn_idle`, `UserIdleTimeoutUpdateFrame`) that map nicely to "check-in" behaviour ([turn events](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/turn-events.md), [detecting user idle](https://docs.pipecat.ai/pipecat/fundamentals/detecting-user-idle.md)).
- **No Reachy Mini transport exists** (Pipecat context-hub search returned nothing), so we would write a custom transport around `media.get_audio_sample()` / `push_audio_sample()`. More moving parts than s2s, which already speaks the protocol the Reachy app uses.

**FastRTC** ([repo](https://github.com/gradio-app/fastrtc)): a Python WebRTC/WebSocket streaming library with `ReplyOnPause` VAD. The conversation app removed it in v0.9, so it is not recommended here.

**Individual components (if we roll our own)**

| Component | Option | Source |
|---|---|---|
| VAD | Silero VAD (MIT) | <https://github.com/snakers4/silero-vad> |
| Wake word | openWakeWord. Code is Apache-2.0, but the **pre-trained models are CC BY-NC-SA 4.0**. Custom wake words can be trained from 100% synthetic TTS clips ("<1 hour" Colab). Typical target: <0.5 false accepts/hour. | <https://github.com/dscripka/openWakeWord> |
| Wake word (commercial) | Picovoice Porcupine (openWakeWord's README suggests it for constrained hardware) | <https://picovoice.ai/platform/porcupine/> |
| STT | Parakeet-TDT-0.6b-v3 (CC-BY-4.0), MLX port `mlx-community/parakeet-tdt-0.6b-v3`, `parakeet-mlx` | [model](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [parakeet-mlx](https://github.com/senstella/parakeet-mlx) |
| STT | MLX Whisper large-v3-turbo (via Pipecat or mlx-audio) | [Pipecat Whisper docs](https://docs.pipecat.ai/api-reference/server/services/stt/whisper.md) |
| LLM local | Qwen3-4B-Instruct-2507 4-bit MLX (Apache-2.0) | [model](https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit) |
| TTS local | Kokoro-82M (Apache-2.0; "under $1 per million characters" when served via API) | [model card](https://huggingface.co/hexgrad/Kokoro-82M) |
| TTS local | Qwen3-TTS-12Hz-1.7B-CustomVoice (Apache-2.0) | [model](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) |
| TTS cloud | ElevenLabs Flash/Turbo, $0.04 per 1K chars, "~75ms" latency. Multilingual/v3 is $0.08 per 1K chars. | [pricing](https://elevenlabs.io/pricing/api) |
| TTS cloud | OpenAI `gpt-4o-mini-tts` ($12/1M audio-out tokens), Gemini `gemini-3.8-flash-tts` | [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) |

Piper was not re-verified this session.

### 2.3 Options comparison (speech stack)

| # | Stack | Where it runs | Latency (turn) | Cost | Quality / character | Privacy | Effort | Fit for Sonny |
|---|---|---|---|---|---|---|---|---|
| 1 | Conversation-app default: HF **deployed** backend | Pollen cloud | Low ("low-latency", not quantified) | No API key; quotas unknown (**unverified**) | Good; fixed voice list | Audio leaves the house | Lowest | Good for the spike |
| 2 | **HF s2s fully local on Mac** (Parakeet + Qwen3-4B MLX + Qwen3-TTS or Kokoro) | Mac | ~1–2 s (doc example; **measure**) | $0 | OK. A 4B LLM is weaker at wit and tool calling; voices are decent | Nothing leaves the Mac | Low (same protocol as the app) | **v1 default** |
| 3 | HF s2s local speech + **hosted LLM** | Mac + LLM API | Adds a network round trip to the LLM | Text tokens only (cheap) | Better personality and tool use | Only transcripts leave | Low | **v1 fallback / upgrade** |
| 4 | OpenAI Realtime `gpt-realtime-2.1-mini` / `-2.1` | Cloud | Lowest, most natural barge-in | $10/$20 (mini) or $32/$64 (full) per 1M audio tokens | Best expressiveness | Mic audio to the cloud while the session is open | Low (same client code) | Good when quality > privacy |
| 5 | Gemini Live | Cloud | Low | $3/$12 per 1M audio tokens; free tier | Good; affective dialog | Audio to the cloud | Medium (15-min session cap → compression + resumption) | Possible; more plumbing |
| 6 | DIY cascade in Pipecat (Silero + Smart Turn + MLX Whisper + any LLM + Kokoro) | Mac | Similar to 2 | $0 local | Same as 2 | Local | High (custom Reachy transport) | Only if s2s blocks us |
| 7 | No conversation: template lines + TTS (Judgy Reachy / Focus Buddy style) | Mac | n/a | ~$0 | Repetitive | Local | Lowest | Too command-driven; useful as offline fallback for nudges |

---

## 3. Intent extraction and in-session conversation

### 3.1 Turning speech into a session

Use function calling. Every option above supports it: the conversation app forwards tool specs in `session.update` ([huggingface_realtime.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/huggingface_realtime.py)), the s2s server passes tool calls through, and OpenAI and Gemini document it. This is a sketch of the schema, not code to ship:

- `start_focus_session`
  - `task: string`: a short noun phrase, e.g. "writing the spec"
  - `duration_minutes: integer` (1–180), **or** `end_time: string` (local `HH:MM`) for "until 3pm"
  - `strictness: enum[gentle, normal, strict]` (optional; default from profile)
  - `allowed_phone_use: string` (optional, e.g. "2FA codes")
- In-session tools:
  - `get_session_status()`: time left, distractions so far
  - `extend_session(minutes)`, `pause_session(minutes)`, `end_session(reason)`
  - `snooze_nudges(minutes, reason)`, e.g. "I'm on a call"
  - `set_strictness(level)`
  - `log_note(text)`, which feeds the recap

Practical guards:

- The app validates and clamps the arguments.
- Relative times ("until 3pm") are computed in code from the current local time. Inject the current time into the instructions, or give the LLM a `get_time` tool. The conversation app ships a time MCP tool.
- Confirm in one sentence ("25 minutes on the spec, starting now"), so a misheard "15" vs "50" gets caught.
- Keep a regex fallback for the canonical phrasing in case the small local LLM fails to call the tool. The 4B-model tool-calling reliability is **unverified; test it**.

### 3.2 Keeping it conversational during a session

- **Session state lives in the app.** The app pushes a fresh `instructions` block through `session.update` when the state changes: task, ends-at, minutes left, distraction count, strictness. The conversation app's `refresh_instructions()` hook exists for exactly this kind of live prompt update ([conversation_handler.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/conversation_handler.py)).
- **Proactive turns** (nudges, half-time check-ins, recap) are app-initiated:
  - Path one: the `say()` pattern, a `conversation.item.create` with an instruction such as "[system event] user picked up phone for 60 s, level 2. Say one short line that references their task", then `response.create`.
  - Path two: an OpenAI out-of-band `response.create` with `conversation:"none"` plus `instructions`, which keeps nudges out of the chat history ([OpenAI guide](https://developers.openai.com/api/docs/guides/realtime-conversations)).
  - For fully predictable wording, render a template and send it through TTS directly.
- **Avoid accidental replies during focus.** With `server_vad` + `create_response=true`, the robot answers anything it hears: the user's phone call, a colleague, the user muttering. Options:
  - Wake word ("Hey Sonny", a custom openWakeWord model) required during an active session.
  - `create_response=false`, with the app deciding (e.g. respond only if the transcript mentions Sonny or a session keyword).
  - A mic mute (the conversation app has `conversation.mic`).

  Recommendation: free conversation outside sessions, wake-word or name-gated inside sessions.
- **Snooze semantics.** When the user says "I need my phone for 2FA" or "I'm on a call", call `snooze_nudges` and acknowledge non-verbally (a `yes1` / `understanding2` move) rather than with speech.

---

## 4. Expressive behaviour

### 4.1 Official motion assets and how to trigger them

- **Emotions dataset:** [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library), with **85 moves** (I listed the dataset via the HF API). Each one is a `.json` keyframe file (`description`, `time`, `set_target_data`) plus a matching `.ogg` sound. Names include:
  - attentive1/2, inquiring1/2/3, curious1, impatient1/2, reprimand1/2/3, displeased1/2, irritated1/2, oops1/2
  - proud1/2/3, success1/2, cheerful1, enthusiastic1/2, welcoming1/2, understanding1/2, yes1, no1, no_excited1
  - thoughtful1/2, boredom1/2, tired1, sleep1, waiting, toc-toc-toc, calming1, relief1/2, serenity1, …

  Sample descriptions and durations:
  - `inquiring1`: "a quick movement", 2.1 s
  - `attentive1`: "show that you're listening", 4.3 s
  - `impatient1`: "when you want things to move faster", 4.0 s
  - `reprimand3`: "a funny way to scold your interlocutor", 4.3 s
  - `curious1`: "you look around", 11.8 s
- **Dances dataset:** [`pollen-robotics/reachy-mini-dances-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library), plus the [`reachy_mini_dances_library`](https://github.com/pollen-robotics/reachy_mini_dances_library) package. Good for celebrations.
- **Python API** ([examples/recorded_moves.py](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/examples/recorded_moves.py), [recorded_move.py](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/src/reachy_mini/motion/recorded_move.py)):
  - `RecordedMoves("pollen-robotics/reachy-mini-emotions-library").get("attentive1")`, then `mini.play_move(move, initial_goto_duration=1.0)`.
  - `async_play_move(..., sound=True)` plays the bundled sound, and `cancel_move()` stops it.
  - `RecordedMoves` reads the local HF cache first and downloads on a miss. Pre-download at startup to avoid stalls.
- **REST alternative:** `POST /api/move/play/recorded-move-dataset/{dataset}/{move}` and `GET /api/move/recorded-move-datasets/list/{dataset}` on the daemon ([routers/move.py](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/src/reachy_mini/daemon/app/routers/move.py)).
- **Procedural micro-gestures** (a glance, an antenna twitch) can use `goto_target(antennas=[...], duration=...)`, `look_at_image` / `look_at_world`, and `start_head_tracking()` from the SDK ([reachy_mini.py](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/src/reachy_mini/reachy_mini.py)). The conversation app composes these in its `MovementManager`, so every move goes through one owner of `set_target`. Do the same, otherwise moves fight each other.
- **Sound caveat:** community apps report the emotion clips are loud. Focus Guardian ships "Sound is off by default (the clips are loud)" ([Space](https://huggingface.co/spaces/RyeCatcher/focus_guardian)), and Dodo Focus says "`rage1` includes official emotion audio and may be loud" ([Space](https://huggingface.co/spaces/riyachhik/dodo_focus)). Default `sound=False` for low ladder levels.

### 4.2 Escalation ladder (proposal)

Principles behind it:

- Start non-verbal.
- Count *episodes*, not frames. Focus Buddy: "a thirty-second phone check is one distraction, not thirty", rate-limited to one nudge per 60 s by default ([Space](https://huggingface.co/spaces/idormoy/focus_buddy)).
- Reference the user's own goal (Cho et al. goal-reminder condition).
- Reward the return.
- Let the user tune or snooze.
- Prefer natural breakpoints for speech (Adamczyk & Bailey 2004; Iqbal & Bailey 2008).

The timings are starting values to tune, not research-derived:

| Level | Trigger (phone in hand / looking at phone) | Robot behaviour | Speech |
|---|---|---|---|
| L0 Notice | ~3–5 s continuous (debounce) | Turn head to the user (look_at / tracking), antennas perk up; `inquiring1` (2.1 s) | None |
| L1 Hint | ~20 s | Antenna wiggle or head tilt, or `impatient1` / `waiting` with sound off | None or a non-verbal sound |
| L2 Reminder | ~45–60 s | `attentive1` + one short line, LLM-phrased from task and time left: "Spec's waiting — 14 minutes to go." | ≤ 12 words |
| L3 Character | ~2–3 min, or 3rd episode in 10 min | `reprimand3` / `displeased1` (funny scold) + a playful line, offering an out: "Want me to pause the timer, or shall we get back to it?" | 1–2 sentences |
| Return | Phone down within an episode | `yes1` / `understanding2` / `cheerful1` (small) + optional "Welcome back." at most once per N min | Optional |
| Recap | Session end | `success1` / `proud1` (or a dance if it was a clean run) + 2–3 sentence summary: duration, # episodes, longest streak, a nod to the task; ask "how did it go?" and offer the next block | Conversational |

Notes:

- **Strictness changes the timings.** "gentle" means a slower ladder that never goes past L2. "strict" means a faster ladder and an earlier L3.
- **Cool down** after L2/L3. Reset to L0 after X minutes of clean focus.
- **Session-level budget:** after N nudges, switch to empathy ("Rough focus day? Want to shorten the session?"). This follows the reactance research and the emotional-support findings.
- **Idle behaviour during focus:** quiet breathing only, with no random dances. Focus Guardian does the same, with "gentle breathing while you work".

### 4.3 HRI / HCI evidence (brief)

- **Robot study companion (most relevant):** Cho, Cha, Liu & Jeong, "Motivating Students' Self-study with Goal Reminder and Emotional Support", arXiv:2510.23860 (2025). NAO, Wizard-of-Oz, N=74, 2-hour sessions. Interventions were triggered by distraction signs, including "using their mobile devices", or else every 30 min, giving 4–6 per session.
  - When delivered to a distracted participant, interventions re-focused them 79% of the time (75% for goal reminders, 84% for emotional support).
  - Both conditions scored higher on ease of use than the presence-only control. The goal-reminder robot drew higher willingness to rely on it in future sessions.
  - Goal-reminder participants described "peer pressure … staring and judging me". Emotional-support participants treated the robot more as a "social other", and seeing the robot as a social other predicted goal achievement.
  - The authors suggest mixing the two types depending on context.
  - <https://arxiv.org/abs/2510.23860>
- **ADHD schoolwork companion:** O'Connell et al., HRI 2024. The authors' summary (as cited by Cho et al.) is that many participants perceived it positively and chose to keep using it after a one-week interaction. <https://doi.org/10.1145/3610977.3634929>. A follow-up "In-Home Body Double Robot" appeared in HRI 2026 Companion, <https://doi.org/10.1145/3776734.3794639> (not read in full).
- **Reactance:**
  - Ghazali, Ham, Barakova & Markopoulos, Computers in Human Behavior 2018: a social robot with *more* social cues produced *higher* psychological reactance, and more so when the user was highly involved. <https://doi.org/10.1016/j.chb.2018.05.016>
  - Their 2019 follow-up found that *interactive* cues (head mimicry plus well-timed praise) lowered reactance and increased liking. <https://doi.org/10.1080/01691864.2019.1589570>
  - Takeaway (my synthesis): avoid heavy-handed "social pressure" displays. Prefer responsive, well-timed, warm cues.
- **Interruption timing:**
  - Adamczyk & Bailey, CHI 2004, "If not now, when?": interruptions at different moments within a task have different costs. <https://doi.org/10.1145/985692.985727>
  - Iqbal & Bailey, CHI 2008, "Effects of intelligent notification management on users and their tasks": deferring notifications to breakpoints. <https://doi.org/10.1145/1357054.1357070>
  - Mark, Gudith & Klocke, CHI 2008, "The cost of interrupted work". <https://doi.org/10.1145/1357054.1357072>

  These findings are summarised from the titles and their well-known results. I verified the DOIs via Crossref but did not re-read the papers this session. Implication: a phone pickup is *already* an interruption, so the robot's L0/L1 cues don't add much cost. Spoken reminders are best delivered right away (at the distraction) or held until the user looks up, never in the middle of typing.
- **Ongoing Reachy-specific study:** Stuttgart Media University (HdM) is running a survey comparing three Reachy Mini "reaction and intervention modes" against smartphone procrastination. It was posted 2026-05-30 and has no results yet ([HF forum](https://discuss.huggingface.co/t/hri-study-how-should-robots-intervene-to-prevent-procrastination-10-min-survey/176400)). Worth watching.

### 4.4 Character and voice

- Keep replies to 1–2 sentences. The official default profile enforces "under 25 words when possible" ([profile.md](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/profiles/default/profile.md)).
- Have the LLM pick an emotion via a `play_emotion`-style tool when *conversing*. For *nudges*, the ladder picks the move deterministically, so the behaviour stays consistent and testable.

### 4.5 Community prior art (HF Spaces tagged `reachy_mini`)

- **Judgy Reachy No Phone** ([Space](https://huggingface.co/spaces/yozkut/judgy_reachy_no_phone)): YOLO26 + ByteTrack phone detection, 8 personalities, reactions that adapt to "offense count and streak", and Edge TTS or ElevenLabs. LLM lines come from Groq Llama-3.1-8B, otherwise pre-written. Uses the emotions library.
- **Focus Guardian** ([Space](https://huggingface.co/spaces/RyeCatcher/focus_guardian)): a "body-double" with timed sessions, silent expressive nudges, and a celebration at the end.
- **Focus Buddy** ([Space](https://huggingface.co/spaces/idormoy/focus_buddy)): episode counting, a 60 s cooldown, hourly summaries, and `macos` `say` as offline TTS. It also reports that small VLMs scored at chance on face-touch detection (relevant to the vision track).
- **Dodo Focus** ([Space](https://huggingface.co/spaces/riyachhik/dodo_focus)): plays `rage1` once when it sees a phone, then sulks with the antennas, and plays `success2` at the end.
- **Reachy Mini Pomodoro** ([Space](https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro)): "Compita" wake word, OpenAI Realtime voice commands for the timer and tasks, and a `--no-robot-audio` browser-mic option.
- **Reachy Phone Home** ([Space](https://huggingface.co/spaces/itsMarco-G/reachy_phone_home)): YOLO phone detection plus "audio quips". Note that its Ultralytics dependency is AGPL-3.0.

---

## 5. Audio I/O on the wireless robot

- **Hardware:** a 4-mic array "based on Seeed's reSpeaker XMOS XVF3800" with a 5 W speaker ([hardware.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/platforms/reachy_mini/hardware.md)). "By default, the audio processor performs acoustic echo cancellation (AEC), so the output from the speaker is canceled out in the microphone input. This way, the robot does not hear itself." AEC and noise suppression can be tuned through XVF3800 parameters. Direction of arrival (DoA) is available. The daemon applies a speaker EQ to correct the "boxy" head shell ([media_advanced_controls.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/platforms/reachy_mini/media_advanced_controls.md)).
- **From the Mac:**
  - The daemon owns the camera and audio. A remote SDK client gets them through the **WEBRTC** media backend (H.264 video, Opus audio), which is auto-selected when remote. GStreamer is needed on the client and ships as wheels on macOS ([media-architecture.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/SDK/media-architecture.md), [python-sdk.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/SDK/python-sdk.md)).
  - The same calls work everywhere: `media.start_recording()`, `get_audio_sample()`, `push_audio_sample()`, `clear_player()`, `get_input/output_audio_samplerate()`.
  - The daemon wobbles the head for incoming WebRTC audio (§1.3).
- **Option comparison:**
  - **Robot mic + robot speaker (recommended):**
    - Hardware AEC, so barge-in works without headphones.
    - The voice comes *from Sonny*, which matters for embodiment, and the wobble comes free.
    - The far-field array picks up the user at desk distance.
    - Costs: dependence on Wi-Fi, plus WebRTC/Opus latency and jitter (not quantified here: **measure**).
  - **Mac mic + Mac speakers:**
    - The voice comes from the laptop, not the robot, and there is no wobble unless we also push the audio to the robot.
    - No AEC unless we use macOS voice-processing I/O or browser `getUserMedia` echo cancellation. Not verified here.
    - s2s explicitly warns about speaker feedback and recommends headphones or mic blocking during playback (which kills barge-in).
    - Reachy Mini Pomodoro offers a browser-mic mode as an option ([Space](https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro)).
  - **Mac mic + robot speaker:** the robot's AEC cannot cancel robot audio in a *Mac* mic, so the robot's own voice will retrigger VAD. Avoid.
- **Resource contention:**
  - Only one consumer should own the camera and audio. Dodo Focus says "Only one app can own the camera" ([Space](https://huggingface.co/spaces/riyachhik/dodo_focus)).
  - Don't run the official conversation app on the robot while our Mac app holds a WebRTC session. Use the daemon's `stop-current-app` if needed ([apps.md](https://github.com/pollen-robotics/reachy_mini/blob/fbdbca3eeb042b8266e0c2d1c855890306c19934/docs/source/SDK/apps.md)).
  - Coordinate with the vision track: the camera frames and the audio come over the same WebRTC connection.

---

## 6. Recommended v1 stack

1. **Topology:** one Python app on the MacBook connects to Sonny with the `reachy-mini` SDK over the WebRTC media backend. It handles camera, mic, speaker, and motion. A **local `speech-to-speech serve`** runs on the Mac at `ws://127.0.0.1:8765/v1/realtime`.
2. **Voice backend:**
   - Start fully local: `--mac-optimal-settings`, with Parakeet-TDT (MLX), Qwen3-4B-Instruct-2507 4-bit (MLX), and Qwen3-TTS 6-bit (or `--tts kokoro` for a lighter, faster voice). Silero VAD + Smart Turn handle turn-taking.
   - If wit or tool-calling is weak, keep local STT/TTS and switch the LLM to a hosted OpenAI-compatible model (`--llm_backend responses-api` / `chat-completions`).
   - If naturalness is what matters most, point the same client at OpenAI `gpt-realtime-2.1-mini`.
3. **Client:** the `openai` Realtime client, following the conversation app's `HuggingFaceRealtimeHandler` structure:
   - `server_vad` + `interrupt_response`
   - `clear_player()` on `speech_started`
   - tool-call plumbing with a background tool manager
   - `say()` for app-initiated turns
4. **Motion:** one `MovementManager`-style owner of `set_target`. Use `enable_wobbling()` for speech. Pre-load `reachy-mini-emotions-library`, start from Pollen's curated "excellent" list, and play moves with `sound=False` by default. Disable random idle dances during sessions.
5. **Session and nudges:** a deterministic state machine in our code (timers, ladder, cooldowns, episode counting, strictness). The LLM's jobs are to parse `start_focus_session` and the follow-up tools, and to phrase L2/L3 lines and the recap. Keep templated fallback lines for when the LLM is slow or offline.
6. **Gating:** free conversation outside sessions. Inside a session, require "Sonny"/"Hey Sonny" (a custom openWakeWord model, or a transcript-name check) before responding. Expose a "mute" voice and UI action.
7. **Process:** do a 1-day spike using the official conversation app unmodified, with a custom profile and a `/rpc conversation.say` driver, before writing our own app.

---

## Open questions

1. **Mac specs:** which chip and how much unified memory? The fully local s2s path needs 16 GB+. It shares the GPU with the vision models from the other track, so check whether MLX contention (s2s logs `mlx_lock_wait`) causes stutter.
2. **Latency** of the full loop: Mac → WebRTC → robot speaker, and robot mic → WebRTC → Mac VAD. Not measured anywhere I found.
3. **Pollen's deployed HF backend:** what models, what quotas or terms, and is it acceptable to send room audio to it?
4. **Does s2s support** out-of-band responses (`conversation: "none"`), and `session.update` instruction refreshes mid-session? The README lists `session.update` but not the out-of-band field.
5. **Language(s):** English only? Parakeet-v3 and Qwen3-TTS are multilingual; the openWakeWord pre-trained models are English only.
6. **Tone preference:** "judgy" (Judgy Reachy) or "supportive peer" (Cho et al.)? Probably supportive by default with a strict option. Ask the user.
7. **Legitimate phone use** (2FA, calls, music): voice snooze only, or a vision-side whitelist?
8. **Wake-word model licensing:** openWakeWord pre-trained models are NC-licensed. That is fine for personal use; train a custom "Hey Sonny" for anything shared.
9. **Gemini Live / OpenAI model IDs and prices** change often. Re-check before choosing a cloud path. Some names above (e.g. `gemini-3.8-live`) came from summarised page fetches.
10. **Does the daemon's head tracking or wobble conflict** with our own `MovementManager` targets on the Wireless? The conversation app hands control back to "daemon tracking" while not speaking (`set_speaking` docstring in [moves.py](https://github.com/pollen-robotics/reachy_mini_conversation_app/blob/2e43e8045188fcb9c8cd33e366b92779965455b3/src/reachy_mini_conversation_app/moves.py)). The SDK track should confirm.

---

## Sources

Official Reachy Mini / Pollen / HF

- Conversation app repo: <https://github.com/pollen-robotics/reachy_mini_conversation_app> (key files at commit `2e43e80`: `huggingface_realtime.py`, `conversation_handler.py`, `console.py`, `moves.py`, `idle_policy.py`, `tools/play_emotion.py`, `tools/core_tools.py`, `profiles/default/profile.md`, `README.md`)
- Conversation app releases: <https://github.com/pollen-robotics/reachy_mini_conversation_app/releases>; PR #444: <https://github.com/pollen-robotics/reachy_mini_conversation_app/pull/444>
- Reachy Mini SDK: <https://github.com/pollen-robotics/reachy_mini> (`src/reachy_mini/reachy_mini.py`, `src/reachy_mini/motion/recorded_move.py`, `src/reachy_mini/daemon/app/routers/move.py`, `examples/recorded_moves.py`, `examples/sound_tts.py`, `docs/source/SDK/media-architecture.md`, `docs/source/SDK/python-sdk.md`, `docs/source/SDK/apps.md`, `docs/source/platforms/reachy_mini/hardware.md`, `docs/source/platforms/reachy_mini/media_advanced_controls.md`)
- Emotions dataset: <https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library>
- Dances dataset: <https://huggingface.co/datasets/pollen-robotics/reachy-mini-dances-library>
- HF speech-to-speech: <https://github.com/huggingface/speech-to-speech> (README, `docs/response-latency.md`)

Speech APIs and models

- OpenAI pricing: <https://developers.openai.com/api/docs/pricing>
- OpenAI Realtime conversations guide: <https://developers.openai.com/api/docs/guides/realtime-conversations>
- Gemini pricing: <https://ai.google.dev/gemini-api/docs/pricing>
- Gemini Live session management: <https://ai.google.dev/gemini-api/docs/live-session>
- Gemini Live overview: <https://ai.google.dev/gemini-api/docs/live>
- ElevenLabs API pricing: <https://elevenlabs.io/pricing/api>
- Kokoro-82M: <https://huggingface.co/hexgrad/Kokoro-82M>
- Parakeet-TDT-0.6b-v3: <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>
- parakeet-mlx: <https://github.com/senstella/parakeet-mlx>
- Qwen3-TTS CustomVoice: <https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice>
- Qwen3-4B MLX: <https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit>
- Smart Turn v3: <https://huggingface.co/pipecat-ai/smart-turn-v3>
- Silero VAD: <https://github.com/snakers4/silero-vad>
- openWakeWord: <https://github.com/dscripka/openWakeWord>
- Porcupine: <https://picovoice.ai/platform/porcupine/>
- FastRTC: <https://github.com/gradio-app/fastrtc>
- Pipecat docs:
  - Whisper STT: <https://docs.pipecat.ai/api-reference/server/services/stt/whisper.md>
  - Kokoro TTS: <https://docs.pipecat.ai/api-reference/server/services/tts/kokoro.md>
  - Turn events: <https://docs.pipecat.ai/api-reference/server/utilities/turn-management/turn-events.md>
  - Detecting user idle: <https://docs.pipecat.ai/pipecat/fundamentals/detecting-user-idle.md>

Community apps

- <https://huggingface.co/spaces/yozkut/judgy_reachy_no_phone>
- <https://huggingface.co/spaces/RyeCatcher/focus_guardian>
- <https://huggingface.co/spaces/idormoy/focus_buddy>
- <https://huggingface.co/spaces/riyachhik/dodo_focus>
- <https://huggingface.co/spaces/adonaivera/reachy_mini_pomodoro>
- <https://huggingface.co/spaces/itsMarco-G/reachy_phone_home>

Research

- Cho, Cha, Liu, Jeong (2025). Motivating Students' Self-study with Goal Reminder and Emotional Support. arXiv:2510.23860. <https://arxiv.org/abs/2510.23860>
- O'Connell et al. (2024). Design and Evaluation of a Socially Assistive Robot Schoolwork Companion for College Students with ADHD. HRI '24. <https://doi.org/10.1145/3610977.3634929>
- O'Connell (2026). Designing an In-Home Body Double Robot to Support College Students with ADHD. HRI '26 Companion. <https://doi.org/10.1145/3776734.3794639>
- Ghazali, Ham, Barakova, Markopoulos (2018). The influence of social cues in persuasive social robots on psychological reactance and compliance. Computers in Human Behavior. <https://doi.org/10.1016/j.chb.2018.05.016>
- Ghazali, Ham, Barakova, Markopoulos (2019). Assessing the effect of persuasive robots interactive social cues on users' psychological reactance, liking, trusting beliefs and compliance. Advanced Robotics. <https://doi.org/10.1080/01691864.2019.1589570>
- Adamczyk, Bailey (2004). If not now, when? CHI '04. <https://doi.org/10.1145/985692.985727>
- Iqbal, Bailey (2008). Effects of intelligent notification management on users and their tasks. CHI '08. <https://doi.org/10.1145/1357054.1357070>
- Mark, Gudith, Klocke (2008). The cost of interrupted work. CHI '08. <https://doi.org/10.1145/1357054.1357072>
- HdM Reachy Mini procrastination survey (HF forum, 2026-05-30): <https://discuss.huggingface.co/t/hri-study-how-should-robots-intervene-to-prevent-procrastination-10-min-survey/176400>
