# Memecoin AI

An auditable Solana market-data, forecasting, and virtual-paper research system. The long-term
target is a scalable Brain; the current deployment is an exploratory, non-execution system.
See [`docs/INDEX.md`](docs/INDEX.md) for the agent entrypoint and canonical knowledge map.

## Development

Active development/research runs on Mac 1 in a checkout separate from owner production state.
Mac 2 is standby. See [operating mode and host boundaries](docs/operator-runtime.md#operating-mode-and-host-boundaries)
for canonical paths, artifact handoff and future worker reactivation.

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
