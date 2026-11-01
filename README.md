# Sonny

A focus companion for a Reachy Mini Wireless robot named Sonny. The app runs on a Mac,
watches its owner during a declared Focus Session through Sonny's camera, and nudges by voice
when it sees phone use or the owner away from the desk. Vocabulary is in
[GLOSSARY.md](GLOSSARY.md); decisions are in [docs/adr](docs/adr).

## Run

Sonny's daemon and this app's `reachy-mini` SDK must be the same version (1.11.0).

```bash
uv run sonny
```

The LLM runs on OpenRouter: put `OPENROUTER_API_KEY=...` in a `.env` at the repo root (it is
git-ignored). The first start downloads the voice models from Hugging Face, and exports RF-DETR
Nano to CoreML in `models/` (about 20 s, in the background; it prints `vision: RF-DETR Nano on
CoreML on GPU` once loaded).

Start it from a terminal with macOS Local Network permission: uv's Pythons are blocked when
started from elsewhere (`[Errno 65] No route to host`). It prints `ready` once Sonny's speaker
link is up and the voice loop is listening. Say "Hey Sonny" to chat, or to start a Focus
Session ("Hey Sonny, let's work on my slides for 25 minutes"); in a session you can ask for the
time left, extend, pause, resume, end, take an Excuse or ask for a Check-in. Ctrl-C puts Sonny in
its sleep pose. Each session's log is a JSON Lines file in `logs/`.

Sonny's head: still at neutral in idle; facing you (daemon face tracking) while you talk to it;
level and still in a Focus Session, with tracking stopped before the session starts. In a session
Sonny watches through its camera (not in a Pause): one slow yaw move re-centres you when you've
stayed off-centre for 3 s, and after 10 s with nobody in frame it sweeps once to find you. Phone
Use and Away go to the session log, with a detection summary every second. The agent
colours its lines with `play_emotion` (18 moves, played on the daemon with their sound at volume
62); a move and a line never overlap and play in the order the agent called them.

Every tunable number lives in [src/sonny/sonny.toml](src/sonny/sonny.toml). To override, copy
the whole file and point `SONNY_CONFIG` at the copy.

## Develop

```bash
uv sync
uv run pre-commit install
uv run pytest
```

The agent scenarios (`tests/test_agent_scenarios.py`) replay owner lines and session time through
the real tools and conductor with a scripted agent. To run them against the configured model
instead (costs OpenRouter credit), after any change to the prompt or model:

```bash
SONNY_LIVE=1 uv run pytest -m live -s tests/test_agent_scenarios.py
```

Ruff lints and formats through pre-commit; CI runs the same hooks plus the tests on macOS.
