# Pipecat runs the voice; our app conducts the agent

Sonny's voice runs as a Pipecat pipeline inside our ReachyMiniApp: a custom transport over Sonny's mic and speaker, a wake-word turn strategy (openWakeWord), on-device STT and TTS on the Mac, and Claude Haiku 5.5 via OpenRouter as the LLM service with our tools registered on it. HF `speech-to-speech` is dropped. No agent framework sits on top: the agent is that LLM service, one context per Focus Session, our tool handlers, and a small conductor in our code that turns events into context messages and decides when the agent runs (one wake-up at a time, queued events, barge-in only by Wake Word). What the agent writes as plain text is what Sonny says; an empty reply is silence, and there is no `speak` tool.

We chose this over keeping `speech-to-speech` (`serve` is an OpenAI-Realtime-style server whose LLM stage always runs; owning the loop meant switching its auto-reply off and driving it over a WebSocket, or calling its handlers through a private API), LiveKit Agents (needs a LiveKit room; Sonny's audio already comes over the Reachy SDK's own WebRTC) and no framework (we would rebuild VAD, sentence streaming and interruption). Pipecat is built for this shape, so each library is used as intended; our code is only the conductor, the tools and the transport.

## Consequences

- Code enforces the Nudge limits from [ADR 0003](0003-code-observes-agent-decides.md) by deciding when the agent wakes, not by refusing a `speak` call: once a line is generated it is spoken.
- The agent contract tested in [Which LLM drives the agent: hosted or local?](../../.scratch/sonny-v1/issues/09-voice-stack-on-m2-pro.md) (with `speak` as a tool) must be re-checked with speech as plain text.
- Pipecat's default VAD interruption stays off while Sonny speaks; the speaker is next to the mic, so only the Wake Word interrupts.
- Don't add an agent framework for the loop; one LLM, ~10 tools and one history don't need one.
