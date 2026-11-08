# R7: A default LLM path for new owners (Hugging Face Inference Providers)

Research for ticket `.scratch/sharing-sonny/issues/04-default-llm-path.md`.
Researched 2026-11-08 against `pipecat-ai` **1.14.0** and `huggingface-hub` **1.33.0** (both from `uv.lock`), the live router catalogue at `https://router.huggingface.co/v1/models`, and `pollen-robotics/reachy_mini_conversation_app` (Space `main` on that date).

Labels used below:
- **[verified]**: read in a primary doc or source, or measured.
- **[inferred]**: my conclusion from verified facts. Not stated anywhere.
- **[unverified]**: not confirmed. Needs a test or a question to HF or Pollen.

---

## TL;DR

1. **The plumbing works.** In Pipecat 1.14.0, Sonny's `CappedOpenRouterLLM` already takes a `base_url`. Pointing it at `https://router.huggingface.co/v1`, with the cached `hf auth login` token as the API key, ran Sonny's real prompt and all 10 of its tools. **[verified, measured]**
2. **Claude is not on HF.** The router serves only open-weight models, and none of its 127 models is Anthropic's. **[verified]** Switching to HF means switching the model, so the agent-scenario tests (`tests/test_agent_scenarios.py`, live) must be re-run before it can become the default. **[inferred]**
3. **"No extra account" holds, but "no money" does not.** **Free HF accounts get $0 of monthly credit.** They must buy prepaid credits before the first call. PRO ($9/mo) includes **$2.00/month** of compute credit, which is shared with every other HF compute service. **[verified]**
4. **Latency is fine on the fast routes.** First output on Sonny's real prompt came in **0.5–0.75 s** on `openai/gpt-oss-120b:cerebras` and `Qwen/Qwen3.8-27B:cerebras`, and in **0.5–1.4 s** on Baseten's DeepSeek-V4.1-Flash and GLM-5.3-Flash. Haiku via OpenRouter has a logged median of **1.0 s** (p90 2.0 s) to first text. Kimi-K2.6 and MiniMax-M2.7 on DeepInfra took **6–44 s**, so they are unusable. **[verified, measured]**
5. **Cost:** a 25-minute Focus Session costs about **$0.02–0.10** on `gpt-oss-120b:cerebras` and **$0.10–0.30** on `Qwen3.8-27B:cerebras`. That makes PRO's $2 worth roughly **20–60** or **7–20 sessions a month**. Haiku 5.5 on OpenRouter, at $0.10/$0.50 per M tokens, costs about **$0.01–0.03** per session. **[inferred from verified prices and logged call counts]**
6. **Pollen's conversation app does not use Inference Providers.** It resolves a token with `config.HF_TOKEN or huggingface_hub.get_token()` (the env var, then the `hf auth login` cache). It sends that token to *Pollen's own* realtime session allocator, a Space proxy, and the allocator hands back a speech-to-speech websocket. That gives Sonny an auth pattern to copy, not a billing model. **[verified]**

**Recommendation (details at the end):** add HF Inference Providers as a second, **opt-in** LLM backend now, defaulting to `openai/gpt-oss-120b:cerebras` with `Qwen/Qwen3.8-27B:cerebras` as the richer alternative. Keep OpenRouter + Haiku as the default until the agent-scenario suite passes on the HF model. Then make HF the default only for owners who have credits, and show a clear message on 402 or 401.

---

## 1. How the router works

- **Endpoint and auth.** The router is an OpenAI-compatible endpoint at `https://router.huggingface.co/v1`. You pass `api_key=<HF user token>`. A fine-grained token needs the "Make calls to Inference Providers" permission (`inference.serverless.write`). **[verified]** ([function-calling guide](https://huggingface.co/docs/inference-providers/guides/function-calling), [index § Authentication](https://huggingface.co/docs/inference-providers/index#authentication))
  - The token on this Mac is a classic `write` token. It worked unchanged. **[verified, measured]**
- **Choosing a provider.** A suffix on the model id picks the provider: `model:provider` pins one, and `:fastest`, `:cheapest` or `:preferred` choose a selection policy. A bare id uses `:fastest`, which means the highest throughput. **[verified]** ([index § Python](https://huggingface.co/docs/inference-providers/index#python), [Claude Code integration](https://huggingface.co/docs/inference-providers/integrations/claude-code))
  - Different providers run different configurations of the same model. HF advises pinning a provider for function calling "by reducing variance". **[verified]** ([function-calling guide § Provider Selection](https://huggingface.co/docs/inference-providers/guides/function-calling#provider-selection))
- **Feature caveats.** `tools`, `tool_choice` (`auto`, `required`, or a named function) and streaming are supported. HF warns that strict mode and streaming are "not supported by all providers". **[verified]** (same guide)
- **The live catalogue.** `GET /v1/models` returns, for each model and provider: `supports_tools`, `supports_structured_output`, `pricing` (USD per M tokens), `first_token_latency_ms`, `throughput` and `status`. **[verified]** ([live catalogue](https://router.huggingface.co/v1/models), fetched 2026-11-08)
  - On 2026-11-08 it listed 127 models and 176 tool-capable model/provider pairs across 11 providers (baseten, cerebras, cohere, deepinfra, featherless-ai, groq, novita, nscale, ovhcloud, publicai, zai-org).
  - No pair was `is_free`, and no Anthropic, OpenAI-proprietary or Google Gemini model was listed. `openai/gpt-oss-*` is OpenAI's open-weight family. **[verified]**

## 2. Tool calling: which models hold up

Sonny depends heavily on tool calls: `start_session`, `extend`, `pause`/`resume`, `end_session`, `set_hook`, `play_emotion` and others (`src/sonny/focus/tools.py`). "Supports tools" in the catalogue only means the provider accepts the `tools` field, so I measured behaviour directly.

**Method.** I wrote a scratch script (`uv run python` in the repo env) that sends Sonny's real system prompt (`agent_prompt(load_config())`, 6,912 chars) and the real 10 tool schemas (`_schemas(cfg)`) to the router, streaming at temperature 0.6. It ran two wake-ups modelled on logged ones, 2–3 runs each:
- **start**: an `owner.spoke` with "Hey Sonny, let's do 25 minutes on writing the report." and no session running. The expected result is `start_session(task, 25)`.
- **nudge**: a `phone_use.started` at minute 6 of a 25-minute session. The expected result is a light Nudge (a move and/or a line), possibly with a follow-up hook.

It recorded the time to the first visible delta (text or tool-call), token usage and the parsed calls. **[verified, measured]** All calls below parsed as valid JSON with the right arguments:

| Model:provider | First output (s) | start → | nudge → | Spoke a line? | $/M in / out |
|---|---|---|---|---|---|
| `openai/gpt-oss-120b:cerebras` | 0.48–0.75 | `start_session` ✓ (3/3) | `play_emotion` (3/3) | never | 0.35 / 0.75 |
| `openai/gpt-oss-120b:groq` | 0.97–1.76 | `start_session` ✓ | `play_emotion` | never | 0.15 / 0.75 |
| `openai/gpt-oss-120b` (auto = fastest) | 0.89–1.43 | `start_session` ✓ | `play_emotion` | never | n/a |
| `Qwen/Qwen3.8-27B:cerebras` | 0.52–0.59 | `start_session` ✓ (+ move once) | `play_emotion` + `set_hook` | **yes**, in character | 0.99 / 1.49 |
| `deepseek-ai/DeepSeek-V4.1-Flash:baseten` | 0.66–1.42 | `start_session` ✓ (+ move once) | `play_emotion` + `set_hook` | never | 0.30 / 1.20 |
| `zai-org/GLM-5.3-Flash:baseten` | 0.47–1.33 | `start_session` ✓ | `play_emotion` + `set_hook` | never | 0.15 / 0.50 |
| `Qwen/Qwen3-235B-A22B-Instruct-2507:deepinfra` | 0.37–1.75 (total up to 6.5) | `start_session` ✓ | `play_emotion` (+ hook) | yes | 0.09 / 0.55 |
| `MiniMaxAI/MiniMax-M2.7:deepinfra` | 1.8–6.8 | `start_session` ✓ | `play_emotion` + `set_hook` | yes | 0.25 / 1.00 |
| `moonshotai/Kimi-K2.6:deepinfra` | **6–42** | `start_session` ✓ | `play_emotion` + `set_hook` | yes | 0.75 / 3.50 |

What the runs show:
- **Every tested model called the right tool with the right arguments in every run.** The samples are tiny (n = 2–3 per scenario), so this is "no red flags", not "reliable". **[verified, measured]**
- **The models differ on behaviour that matters more than the tool call itself:**
  - **Speaking alongside a tool call.** gpt-oss-120b, DeepSeek-V4.1-Flash and GLM-5.3-Flash *never* spoke a line with a tool call; they only acted. Qwen3.8-27B spoke short, in-character lines, such as "Psst — the emails are getting lonely. Phone can wait five minutes.", and set a follow-up hook. That is the closest match to how the prompt describes Sonny. **[verified, measured]**
  - **Whether the tool-only behaviour is acceptable.** Whether a silent `start_session`, or a move-only first Nudge, is acceptable under Sonny's own rules needs the live scenario suite (`tests/test_agent_scenarios.py`). **[unverified]**
- **Hidden reasoning output.** gpt-oss and Qwen3.8 are reasoning models. Their `completion_tokens` (100–490) include hidden reasoning, so they bill more output than they speak. That was still well inside latency. **[verified, measured]**
- **What the catalogue flags do not tell you.** Throughput-heavy routes (Cerebras, Baseten) gave the low first-output times. The same models on DeepInfra or Novita can be several times slower, and `supports_tools` says nothing about latency. **[verified, measured + catalogue]**

## 3. First-token latency against Sonny's needs

- **Current baseline.** Sonny's logs (10 session files, 89 `llm_call` records, Haiku 5.5 via OpenRouter) show **first text: median 1.02 s, p90 2.00 s**, and **whole call: median 1.24 s, p90 2.28 s, max 3.28 s**. **[verified, logs]**
- **The limits in config.** Sonny caps a whole wake-up at `wakeup_cap_s = 15`, which covers the tool call plus the continuation after its results. It re-issues a call that produces nothing within `retry_timeout_s = 3`. **[verified, `sonny.toml`, `voice/llm.py`]**
- **Routes that fit.** gpt-oss-120b, Qwen3.8-27B (both on Cerebras), GLM-5.3-Flash and DeepSeek-V4.1-Flash (both on Baseten) were at or below Haiku's median first output, and well inside both limits. **[verified, measured]**
- **Routes to avoid.**
  - Kimi-K2.6 and MiniMax-M2.7 on DeepInfra would trip the 3 s re-issue constantly, and Kimi would hit the 15 s cap. **[verified, measured]**
  - Qwen3-235B on DeepInfra reached first output quickly but took up to 6.5 s to finish, which would push the continuation call close to the cap. **[verified, measured]**
- **Pinning matters for latency too.** A bare model id (`:fastest`) chooses by *throughput*, not first-token latency. Pin a provider with a `:provider` suffix so the route doesn't drift to a slow one. **[verified docs; inferred consequence]**

## 4. Cost per Focus Session, and what the credit covers

**What HF provides** **[verified]** ([pricing](https://huggingface.co/docs/inference-providers/pricing)):

| Account | Monthly credit | Without credit |
|---|---|---|
| Free | **none** | must buy prepaid credits |
| PRO | **$2.00**, shared with Spaces hardware, ZeroGPU overage, Endpoints and Jobs | pay-as-you-go |
| Team/Enterprise | $2.00 per seat, pooled | pay-as-you-go |

HF passes provider prices through "with no additional fees". **[verified]**

**How much a session uses.**
- **Calls per session.** Logged sessions made 2–20 LLM calls each. A 61-minute session made 13. Short test sessions were denser, at 10–20 in 3–5 minutes. **[verified, logs]**
- **Context growth.** Sonny never trims its context; I found no trimming code in `voice/pipeline.py` or elsewhere. So the input grows by about 300–500 tokens with each wake-up, on top of a base of about 2.8–3.4k tokens (the prompt plus tools, measured as `prompt_tokens` on the router). **[verified for the base; inferred for growth]**
- **Two estimates for a 25-minute session:** **light** is 15 calls, about 90k input and 4k output tokens; **heavy** is 30 calls, about 280k input and 8k output tokens. **[inferred]**

| Route | Light session | Heavy session | Sessions per PRO $2 |
|---|---|---|---|
| `gpt-oss-120b:cerebras` | ~$0.035 | ~$0.10 | ~20–57 |
| `Qwen3.8-27B:cerebras` | ~$0.095 | ~$0.29 | ~7–21 |
| `GLM-5.3-Flash:baseten` | ~$0.016 | ~$0.046 | ~43–125 |
| `DeepSeek-V4.1-Flash:baseten` | ~$0.032 | ~$0.094 | ~21–62 |
| `gpt-oss-120b:deepinfra` (slower, 0.39 s catalogue first token) | ~$0.004 | ~$0.012 | 160+ |
| *Reference:* Haiku 5.5 on OpenRouter ($0.10/$0.50) | ~$0.011 | ~$0.032 | n/a |

These are **[inferred]**: each model's tokenizer counts differently, and the call count depends heavily on how many distractions there are.

**Prompt caching.** I found no cached-input pricing for HF routes; the catalogue gives no cache price. Haiku on OpenRouter lists a $0.01/M cache-read price. If Sonny used caching, Haiku's advantage would grow. **[unverified for HF]**

**My probe spend.** About 50 calls of roughly 3k tokens each, which is well under $0.10, billed to the PRO account (`isPro: true`). **[verified]**

## 5. Plugging an OpenAI-compatible base URL into Pipecat 1.14.0

- **No dedicated service.** Pipecat 1.14.0 has no Hugging Face LLM service; nothing under `pipecat/services/` mentions `huggingface`. Its generic route is `OpenAILLMService` / `BaseOpenAILLMService`, which accepts `base_url`, `api_key` and `default_headers` (`services/openai/base_llm.py`). The docs describe it as supporting "OpenAI-compatible base URLs". **[verified]** ([Pipecat OpenAI LLM docs](https://docs.pipecat.ai/api-reference/server/services/llm/openai.md))
- **What Sonny's class already passes through.** Sonny's `CappedOpenRouterLLM` subclasses `OpenRouterLLMService`. That class's `__init__` has `base_url: str = "https://openrouter.ai/api/v1"` and passes it to `OpenAILLMService`, and Sonny's `create_client` override already forwards `base_url` (`src/sonny/voice/llm.py`). **[verified]**
- **Smallest change.** Pass `base_url="https://router.huggingface.co/v1"` and the HF token. **[verified source; inferred fit]**
- **Why staying on the OpenRouter class is safe.** The only OpenRouter-specific behaviour is:
  - `_apply_provider_preferences`, which only adds `extra_body.provider` when `Settings.provider` is set (Sonny doesn't set it);
  - a Gemini-only rewrite of system messages;
  - `supports_developer_role = False`, which keeps `developer` messages as `user`. That is the safer choice for HF providers too.

  So no OpenRouter-only field reaches HF. **[verified source]**
- **A cleaner long-term shape.** Rebase the class on `OpenAILLMService`, keep `supports_developer_role = False`, and let `[llm]` carry `base_url` (renaming the class to something like `CappedLLM`). **[inferred]**
- **Optional org billing.** `default_headers={"X-HF-Bill-To": "<org>"}` would bill an organisation. **[verified docs]**
- **Reasoning output.** gpt-oss and Qwen3.8 can stream reasoning in a separate delta field, and Pipecat's OpenAI adapter would ignore it. My probe saw no reasoning leak into `content`. **[unverified for Pipecat; verified for the probe]**
- **Errors.** Sonny's three-layer error handling (SDK retries, re-issue on timeout, the wake-up cap) is client-side and does not depend on the provider. A 402 (no credits) or 401 would surface as a failed `LlmCall`, and Sonny would just stay silent. **[inferred from `voice/llm.py`]** That is poor UX for a new owner.

## 6. How Pollen's conversation app uses the cached `hf auth login` token

Sources: `src/reachy_mini_conversation_app/huggingface_realtime.py` (`_build_realtime_client`), `config.py`, and the README, all in [the Space](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app). **[verified]**

- **What it imports and how it picks the token.** It imports `from huggingface_hub import get_token` and resolves the token as `bearer_token = configured_bearer_token or (get_token() or "").strip()`, where `configured_bearer_token = config.HF_TOKEN`.
- **What it does with the token.** In the default `deployed` mode it POSTs to `HF_REALTIME_SESSION_PROXY_URL = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"` with:
  - the header `X-Reachy-Mini-Authorization: Bearer <token>`;
  - the robot's `hardware_id` from the daemon, when available.

  The allocator replies with a `connect_url`. The app then opens an OpenAI-compatible **Realtime** websocket (speech-to-speech: Qwen3-ASR/TTS and an LLM on Pollen's backend) with the same token as `api_key`.
- **Local mode.** Local endpoints receive only an explicitly configured `HF_TOKEN`. The README notes: "Cached credentials and the hardware ID are not sent to local endpoints."
- **Where `get_token()` looks.** In `huggingface_hub` 1.33.0 it checks, in order:
  1. an OIDC exchange, if `HF_OIDC_RESOURCE` is set;
  2. the `HF_TOKEN` env var;
  3. the token file at `$HF_HOME/token`, by default `~/.cache/huggingface/token`, which `hf auth login` writes. Browser-OAuth tokens are refreshed transparently;
  4. Colab secrets.

  **[verified, `huggingface_hub/utils/_auth.py`]**
- **Who pays for Pollen's backend.** The README says the default "does not require an API key". Whether the backend bills the owner's HF account, is rate-limited by `hardware_id`, or is subsidised by Pollen is not stated. **[unverified]** In any case it is **not** `router.huggingface.co`, so it offers no billing precedent for Sonny.
- **Scope of browser-login tokens.** Whether a token from the browser-OAuth flow of `hf auth login` carries the Inference Providers permission is **[unverified]**. Classic `write` tokens work, as measured.

**Pattern to copy:** `api_key = os.environ.get(cfg.llm.api_key_env) or get_token()`. When neither exists, fail at startup with "run `hf auth login`", not with silence.

---

## Recommendation

**Yes, add HF Inference Providers as a backend. Do not make it the default yet.**

1. **Code (small).** Make `[llm]` carry `base_url`, with `api_key_env` falling back to `huggingface_hub.get_token()` when the base URL is HF's. Add a startup check: call `GET /v1/models` or a 1-token completion, and turn 401/402 into a spoken or logged message such as "Sonny needs Hugging Face credits or an OpenRouter key". Do not stay silent.
2. **Model.** Default the HF path to **`openai/gpt-oss-120b:cerebras`**: the fastest first output measured (about 0.5 s), clean tool calls and about $0.04–0.10 per session. Offer **`Qwen/Qwen3.8-27B:cerebras`** for owners who want Sonny to talk more; it was the only fast model that spoke in character alongside its tool calls, at about 3× the cost. Always pin the provider suffix.
3. **Gate.** Run the live agent-scenario suite on the chosen model before switching. Haiku 5.5's behaviour (a spoken line with a move, the Nudge rules) is what the prompt was tuned on, and gpt-oss never spoke alongside a tool call in the probe.
4. **Billing message for new owners.** "Uses your HF account; free accounts must add prepaid credits; PRO's $2/month covers roughly 20–50 Focus Sessions." That still beats asking for a second account (OpenRouter), but it is not free.
5. **Keep OpenRouter + Haiku** as the documented default until steps 1–3 land. Haiku is still among the cheapest per session (about $0.01–0.03; only the slower DeepInfra gpt-oss route is clearly cheaper) and the tested behaviour.

**Open questions:**
- Does a browser-OAuth `hf auth login` token carry the inference permission?
- Do HF providers discount cached input?
- How does gpt-oss do on the full scenario suite?
