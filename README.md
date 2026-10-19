# Memecoin AI

An auditable Solana market-data, forecasting, and virtual-paper research system. The long-term
target is a scalable Brain; the current deployment is an exploratory, non-execution system.
See [`docs/INDEX.md`](docs/INDEX.md) for the agent entrypoint and canonical knowledge map.

## Recover code and resume development

Start from GitHub `main`, then read `memory/now.md`, `AGENTS.md`, and `docs/INDEX.md`:

```sh
git clone https://github.com/avariceJS/memecoin-ai.git
cd memecoin-ai
git switch main
git fetch origin --prune --tags
```

The [branch and recovery registry](docs/operator-runtime.md#git-integration-and-recovery-registry)
identifies every retained experimental branch and the immutable pre-reset history archive.
Use a separate topic branch/worktree for each task and push meaningful checkpoints regularly.
A clone restores code/history, not private Brain state. On the restored Mac, historical owner state
is preserved separately and a verified internal read-only BrainData replica supports development
without Kingston. Kingston remains the unchanged recovery source. See the current storage limits in
[data architecture](docs/brain-source-architecture.md#post-reset-development-storage-2026-10-19).
Cloning or updating `main` must not start Brain.

## Development

Active development/research runs on Mac 1 in a checkout separate from owner production state.
Mac 2 is not involved. The recovery entry is `/Volumes/Kingston/BrainRestore-20261018/START-HERE.txt`. See
[operating mode and host boundaries](docs/operator-runtime.md#operating-mode-and-host-boundaries)
for canonical paths and runtime separation.

Python 3.11+; runtime dependencies use the standard library.

In the research checkout, source `tools/brain_dev_env.sh` in a Cursor terminal to activate the
existing `.venv` and select Python 3.13.6, OpenSSL 3.6.4, Node v24.5.0 and zstd 1.5.7.
The helper makes no installs; `PATH` and the OpenSSL config selection affect only that shell.
Its `BRAIN_DEV_*` overrides support verified alternate tool paths.

```sh
cd /Users/root1/Developer/memecoin-research
source tools/brain_dev_env.sh
make smoke
.venv/bin/python tools/brain_dev_operator_preview.py serve --latest --port 8793
```

Open `http://127.0.0.1:8793`; stop with Ctrl-C. This existing Operator UI displays a copied historical
snapshot with writes and background tickers disabled. Refresh, research commands and the separate
Observatory are documented in [operator/runtime](docs/operator-runtime.md#post-reset-local-development-2026-10-19).

For a new environment only, use `make setup` / `make ml-setup`. Existing restored environments need
no reinstall. `make check` currently stops at 107 legacy Ruff findings; `make test` / `make test-ml`
include known historical failures (see `memory/now.md`). `make ml-check` verifies the focused ML
suite. Synthetic smoke/replay validates software behavior, not market quality or trading edge.

Do not overwrite existing `work/`, datasets, runs, model artifacts, or owner runtime. Read
[`AGENTS.md`](AGENTS.md) before project work.

## Read-only Observatory UI

The integrated [Control Center app](apps/control-center/README.md) displays pinned historical
engineering evidence, not current production state. `make check` includes its isolated Python
boundary tests. After `npm --prefix apps/control-center/web ci`, run `make cc-web-check` for
TypeScript, web tests and build. UI dependencies remain separate from the stdlib runtime.
