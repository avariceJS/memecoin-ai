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
A clone restores code/history; required Brain state comes from the separately pinned Migration v1
package and Kingston. Cloning or updating `main` must not start Brain. The current plan is Mac1 →
Kingston → reset Mac1 → inert restore on Mac1; the owner accepts Kingston as the sole physical
data copy. Pre-reset backup acceptance
and post-reset functional acceptance are separate gates in [the migration guide](docs/portability-disaster-recovery.md).

## Development

Active development/research runs on Mac 1 in a checkout separate from owner production state.
Mac 2 is not involved. The recovery entry is `/Volumes/Kingston/BrainRestore-20261018/START-HERE.txt`. See
[operating mode and host boundaries](docs/operator-runtime.md#operating-mode-and-host-boundaries)
for canonical paths and runtime separation.

Python 3.11+; runtime dependencies use the standard library.

```sh
make setup
make check
make smoke
```

ML development uses the separately pinned environment and commands documented in
[`docs/training-evaluation.md`](docs/training-evaluation.md). Synthetic smoke/replay validates
software behavior only; it does not establish market quality or trading edge.

Do not overwrite existing `work/`, datasets, runs, model artifacts, or owner runtime. Read
[`AGENTS.md`](AGENTS.md) before project work.

## Read-only Observatory UI

The integrated [Control Center app](apps/control-center/README.md) displays pinned historical
engineering evidence, not current production state. `make check` includes its isolated Python
boundary tests. After `npm --prefix apps/control-center/web ci`, run `make cc-web-check` for
TypeScript, web tests and build. UI dependencies remain separate from the stdlib runtime.
