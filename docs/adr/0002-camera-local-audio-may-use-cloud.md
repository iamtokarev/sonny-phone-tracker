# Camera frames stay local; audio may use the cloud

Camera frames of the owner never leave the Mac or Sonny, so all vision runs locally. Don't "simplify" by sending frames to a cloud vision model. Audio and transcripts may go to cloud speech and LLM services.

**Revised 2026-10-11:** this ADR originally ruled out a local LLM because a 16 GB M2 Pro also has to hold the vision models. A local LLM (via llama.cpp) is now allowed **if** [Which LLM drives the agent: hosted or local?](../../.scratch/sonny-v1/issues/09-voice-stack-on-m2-pro.md) shows it calls tools reliably and [Voice turn latency and footprint on the M2 Pro](../../.scratch/sonny-v1/issues/16-voice-latency-footprint.md) shows it fits alongside vision. Otherwise the LLM is hosted (OpenRouter). Don't switch to a local LLM without that measurement.

_Revised 2026-10-26: the agent test in [Which LLM drives the agent: hosted or local?](../../.scratch/sonny-v1/issues/09-voice-stack-on-m2-pro.md) chose a hosted LLM (Claude Haiku 5.5 via OpenRouter). Local Qwen3 models failed the agent contract, so v1 has no local LLM._
