# Research summary: Sonny focus companion

Summary of four research tracks (2026-10-10). Details and sources are in the four track files:

- [r1-reachy-mini-sdk.md](r1-reachy-mini-sdk.md): SDK and platform
- [r2-vision-distraction-detection.md](r2-vision-distraction-detection.md): vision
- [r3-voice-and-expression.md](r3-voice-and-expression.md): voice and expression
- [r4-hf-spaces-and-architecture.md](r4-hf-spaces-and-architecture.md): Spaces and architecture

Checked directly against the source:

- PyPI's latest `reachy-mini` is 1.11.0, requiring Python >=3.11.
- `start_head_tracking`, `get_tracked_face`, `look_at_image`, `enable_wobbling` and the daemon's `recorded-move-dataset` playback route all exist in `pollen-robotics/reachy_mini` main.
- The face tracker uses YuNet.

Nothing has been measured on the robot yet.

## What we're building

Sonny, a wireless Reachy Mini, sits on the desk. You tell it "next 25 minutes I'm doing X". It follows you with its head, watches for distraction (phone, looking away, leaving the desk, slouching) and nudges you with a growing series of reactions: emotion moves and speech. At the end it gives a recap.

## Recommended v1 architecture

```
 MacBook (Apple Silicon)                                 Reachy Mini Wireless (CM4)
┌───────────────────────────────────────────┐          ┌──────────────────────────────┐
│ sonny app (Python, ReachyMiniApp subclass)│  WS :8000│ reachy-mini daemon           │
│  ├─ session state machine + timers        │◄────────►│  ├─ motors, head tracking    │
│  ├─ vision worker (8-10 fps)              │  WebRTC  │  │  (YuNet face detector)    │
│  │   phone det. + MediaPipe face/pose     │◄─────────│  ├─ camera 720p, mic array   │
│  ├─ distraction detector (hysteresis)     │  audio ► │  ├─ speaker, wobble          │
│  ├─ voice agent (tools: start_session…)   │          │  └─ recorded-move playback   │
│  └─ FastAPI :8042 → Vite+TS UI, /rpc      │  REST    │     (emotions dataset)       │
│                                           │─────────►│                              │
│ HF speech-to-speech serve (local)         │          └──────────────────────────────┘
│  Parakeet STT · Qwen3-4B · Kokoro/Qwen TTS│
└───────────────────────────────────────────┘
 Published as: static HF Space, tags reachy_mini + reachy_mini_python_app
```

**Where things run:** R1 suggests the app runs on the robot with the heavy work on the Mac. R4 suggests the whole app runs on the Mac. **For v1, run the whole app on the Mac.** The vision models and local speech already need the Mac, the robot's board is weak (the camera alone uses about 40% of one core), and you develop and debug in one process. The app is still written as a proper `ReachyMiniApp`, so it can be published and later split so the thin parts run on the robot.

**What the robot does itself (built in, no code from us):**

- follows your face (`start_head_tracking`);
- plays emotion moves via the daemon's REST route, so we never send 100 Hz motion over Wi-Fi;
- wobbles its head while speaking;
- uses its mic array, which cancels the robot's own voice so you can interrupt it.

## Key decisions for you

1. **Phone detector licence:**
   - RF-DETR Nano: Apache-2.0, accurate, slightly more setup. **(recommended)**
   - Ultralytics YOLO26n: easiest, but AGPL-3.0, which reaches the published app and any model you train.
2. **Speech stack:**
   - Local HF `speech-to-speech` on the Mac: private and free, needs about 16 GB+ RAM. It's the backend Pollen uses for Reachy Mini. **(recommended)**
   - Pollen's hosted backend or OpenAI realtime: less load on the Mac, but costs money or comes with quotas, and audio leaves your machine.
   - Switching between them is a base-URL change.
3. **Starting point:**
   - Run a one-day spike first, using the official conversation app unmodified with a "Sonny" profile, to test voice, wobble and emotions on the real robot. Then build our own app that borrows its patterns and the debounce logic from `reachy_phone_home`. **(recommended)**
   - Fork the conversation app directly.
4. **Front end:** a Vite + TS UI served by the app's FastAPI on `:8042` for session setup, live status and a debug camera view. Could also be skipped for v1, with voice only.

## Biggest technical risks (to test first on the robot)

1. **Head tracking vs gaze measurement.** The head moves while it follows you, so "is the user looking away?" must be corrected using the head's position at the moment each frame was taken. If the SDK can't record that per frame, pause tracking while measuring.
2. **Head tracking while you look down at your phone.** That's exactly the moment we need to detect. Open bug #1453: after the face is lost, our own head commands stay ignored.
3. **Video from the robot to the Mac:** real latency and frame rate, known smearing (#1407) and slow WebRTC connects (#888). Also, can the voice agent and the vision worker share one stream?
4. **Running the app on the Mac** vs the dashboard's one-app-at-a-time rule.
5. **Posture from a front camera** is weak. Treat it as a slow, low-priority signal compared with your posture at the start of the session.

## Distraction rules (starting values, tune from logs)

| Signal | Trigger |
|---|---|
| Phone in use | phone box near a hand or head pitched down, in at least 60% of frames over 8 s |
| Looking away | 20–30 s at once, or 60 s total within 3 min, compared with the start of the session |
| Absent | no person for 60 s |
| Posture | slouched for 2–3 min compared with the start of the session |
| Cooldown | 3–5 min after any nudge |

Each nudge moves up one step: silent glance → antenna wiggle or "impatient" emotion → spoken reminder of the task → playful scold. Returning to work gets a small reward emotion. Turn off idle dances during sessions, and only listen when you say "Sonny" mid-session.

## Prior art

Several community Reachy Mini focus apps already exist: Dodo Focus, Focus Guardian, Focus Buddy, Reachy Mini Pomodoro, Reachy Phone Home and Judgy Reachy No Phone. None combines a conversational session agent, active head-following, several distraction signals and an escalation ladder. That combination is what makes ours different.
