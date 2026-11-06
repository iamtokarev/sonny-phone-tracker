# R6: mise for project setup

Researched 2026-11-06 against current official mise documentation and the repository's manifests, README and CI. The docs currently identify mise v2026.11.2. This research informed the implemented [mise.toml](../../mise.toml); current usage is in [README](../../README.md).

Labels: **[verified]** means read in the cited primary source or local file. **[recommended]** means a project-specific judgment.

## Recommendation

**[recommended]** Adopt one root `mise.toml` for development tools and a small set of tasks. Keep uv in charge of Python, `.venv` and Python dependencies; keep Bun in charge of frontend dependencies. mise provides one entry point for installing the tools, setting up a checkout, building the Dashboard, running Sonny and checking changes. Its task runner installs missing configured tools before running tasks. [Official getting started](https://mise.jdx.dev/getting-started.html).

## What this project needs

**[verified locally]**:

- [pyproject.toml](../../pyproject.toml) requires Python `>=3.12,<3.13`; [.python-version](../../.python-version) selects `3.12`; `uv.lock` is macOS-only.
- [CI](../../.github/workflows/ci.yml) already uses uv `0.12.5` and Bun `1.4.3`. Preserve these versions during adoption.
- [web/package.json](../../web/package.json) runs the protocol generator with `node`. Node is therefore an existing dependency even though setup currently does not declare it. Either declare Node or separately test switching that script to Bun; mise adoption does not require a runtime migration.
- [README](../../README.md) asks developers to sync Python, install pre-commit hooks, install frontend dependencies and build the Dashboard into `src/sonny/static/`. These steps are currently separate.
- Sonny loads its root `.env` itself in [main.py](../../src/sonny/main.py). First startup downloads models and exports RF-DETR to CoreML. The Reachy daemon must match SDK `1.11.0`; macOS terminal Local Network permission matters.

## Implemented shape

The current tool pins and tasks live in [mise.toml](../../mise.toml). uv and Bun retain the existing CI versions; Node is pinned to the validated local version `26.7.0`. The startup task `app` (alias `start`) is first in the file and in mise's alphabetical task list.

**[verified]** Tasks run from the config root by default; `dir` selects another directory. An array of `run` entries executes sequentially and stops on failure. `{ task = "..." }` calls another task. Independent `depends` prerequisites may run in parallel, so their list order must not express sequencing. [Task configuration](https://mise.jdx.dev/tasks/task-configuration.html).

**[recommended]** Add `check` for the existing Python/pre-commit and frontend checks, retaining the protocol freshness check. Add `dev` for the Dashboard backend and Vite in parallel if one command improves daily use; use the existing replay/placeholder behavior and forward its arguments. Keep live LLM tests as a deliberate opt-in command. Existing `web/package.json` scripts remain the definitions of frontend operations.

**[recommended]** Leave `.python-version` and Python ownership with uv. If later choosing mise-owned Python, remove the competing version source and set `UV_PYTHON = { value = "{{ tools.python.path }}", tools = true }` so uv uses mise's interpreter. Merely putting a Python executable on PATH does not guarantee uv chooses it. Optional `python.uv_venv_auto = "source"` activates an existing uv environment; it is unnecessary when every Python task uses `uv run`. [Python integration](https://mise.jdx.dev/lang/python.html).

## Onboarding and reproducibility

**[recommended]** After installing mise once and reviewing/trusting the repo config, the normal project command becomes `mise run bootstrap`. `mise install` only installs configured tools; our bootstrap task explicitly runs the package managers and builds static assets. Shell activation is optional: `mise run` and `mise exec` work without it, which is useful for agents and CI. [Getting started](https://mise.jdx.dev/getting-started.html).

**[verified]** `mise.lock` records resolved development tools and platform artifacts; it does not replace `uv.lock` or `web/bun.lock`. Generate it with `mise lock` and install it with `mise install --locked`. Include the Mac and Linux runner platforms if adopting it in all current CI jobs. [Lockfile documentation](https://mise.jdx.dev/dev-tools/mise-lock.html).

**[recommended]** A useful next step is using `jdx/mise-action` and the same tasks in CI. Preserve uv's package cache and Bun's download cache: tool caching is a separate concern. Keep Ubuntu lint separate from macOS dependency installation and tests. **[verified]** mise-action installs the repository's tools, caches them and uses locked installation when `mise.lock` exists. [CI documentation](https://mise.jdx.dev/continuous-integration.html).

## Bootstrap features evaluated

**[verified]** Native `mise bootstrap` can provision host packages, services, repositories, dotfiles and shell activation, install tools and run the `bootstrap` task. It supports `--dry-run`. **[recommended]** Sonny currently needs a project task, not workstation provisioning. Use `mise run bootstrap` as the documented onboarding command; leave `[bootstrap]` sections out initially. [Bootstrap overview](https://mise.jdx.dev/bootstrap.html).

**[verified]** `mise bootstrap --from <url>` keeps a separate setup checkout; `--adopt` makes a setup repository part of the machine's own configuration. **[recommended]** Neither is needed to work in a normal Sonny clone. [Repository bootstrap](https://mise.jdx.dev/bootstrap/from-repository.html).

**[recommended]** Install mise once and use `mise` directly for all project commands, as documented in the README.

**[verified]** Host Homebrew formulae can be declared in `[bootstrap.packages]`; formula support includes Apple Silicon Macs. **[recommended]** Add only native packages shown necessary by a clean Mac setup. The manifests do not justify a speculative list of compilers, media libraries or cloud CLIs. [Homebrew package support](https://mise.jdx.dev/bootstrap/packages/brew.html).

## Features to leave out initially

- **[recommended]** Keep the current application `.env` loading. mise can load dotenv files, but that adds another owner and exports the values to task subprocesses. Its file loading also overrides existing environment variables, which can change today's behavior. [Environment directives](https://mise.jdx.dev/environments/#env-file).
- **[verified/recommended]** `[deps.uv]` and `[deps.bun]` can automatically sync packages when inputs change, but this feature is experimental. Explicit locked bootstrap commands already solve the need. [Project dependencies](https://mise.jdx.dev/dev-tools/deps.html).
- **[verified/recommended]** Daemon management is experimental and adds a pitchfork supervisor. Two foreground development servers do not yet justify persistent service supervision. Keep the robot app's current lifecycle. [Development stacks](https://mise.jdx.dev/daemons/development-stack.html).
- **[recommended]** Defer task caching, remote caches, enter-directory install hooks, secrets integrations, monorepo configuration and Docker. If Dashboard builds later become slow, `sources`/`outputs` freshness checks can skip unchanged builds; they require a complete input list to avoid stale assets. [Task caching](https://mise.jdx.dev/tasks/task-caching.html).

## Verification boundary

Research used three Context7 CLI requests (resolve `/jdx/mise`, then tool/Python and task docs) plus the official website.

Implemented and verified on 2026-11-06: exact uv/Bun pins above, Node `26.7.0` (the existing local version), and mise `2026.11.2` installed on PATH. Bootstrap succeeded in this checkout without an existing `.venv` or `web/node_modules`. All 12 tasks validated; `check` passed pre-commit, 520 Python tests (10 skipped, 1 live test deselected), 106 frontend tests, protocol freshness and the production build. Both robot-free dev servers responded over HTTP and stopped on Ctrl-C. CI migration remains a later option. Robot connectivity, matching daemon version, model startup and macOS permissions remain runtime setup concerns.

Use `mise run app` to build the Dashboard and start Sonny.
