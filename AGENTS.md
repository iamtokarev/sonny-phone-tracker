# AGENTS.md

## Development commands

Use `mise run bootstrap` for a new checkout (tools, dependencies, build and Git hooks).
Run `mise run check` before finishing changes; `mise run test <args>` runs selected Python tests.
Use `mise run app` (alias: `mise run start`) for Sonny, or `mise run dev` for the robot-free Dashboard.
See `mise.toml` or `mise tasks ls` for tasks; use `mise exec -- <command>` for other tool commands.
uv owns Python and `.venv`; live LLM tests require explicit opt-in (see README).

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
