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
git-ignored). The first start downloads the voice models from Hugging Face.

Start it from a terminal with macOS Local Network permission: uv's Pythons are blocked when
started from elsewhere (`[Errno 65] No route to host`). It prints `ready` once Sonny's speaker
link is up and the voice loop is listening. Say "Hey Sonny" to chat; Ctrl-C puts Sonny in its
sleep pose.

Every tunable number lives in [src/sonny/sonny.toml](src/sonny/sonny.toml). To override, copy
the whole file and point `SONNY_CONFIG` at the copy.

## Develop

```bash
uv sync
uv run pre-commit install
uv run pytest
```

Ruff lints and formats through pre-commit; CI runs the same hooks plus the tests on macOS.
