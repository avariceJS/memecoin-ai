# Brain Architecture

**Status:** exploratory supervised forecasting and virtual paper; no demonstrated trading edge.
This document owns the current system map and its intended growth boundary.

## Product goal

Build a persistent Solana research Brain that can acquire large market histories, produce
reproducible model generations, and measure improvement on independent future markets. V1/V2/V3
must represent meaningful increases in useful corpus, training compute, and validated capability,
not a new checkpoint from a tiny fit. The two Macs are the first compute stage. The architecture
must allow later GPU and data-center capacity without losing provenance or safety.

The user reports two Macs, paid Alchemy with high RPC throughput, strong research/coding models,
and willingness to expand storage/compute. The exact provider plan and limits have not been audited.
Do not infer a quota or start paid work from that report alone.

## Current system

```text
Solana RPC / indexed history              GeckoTerminal price bars
          |                                         |
immutable captures, journals             first-seen OHLCV captures
          |                                         |
versioned protocol decoders             continuous canonical bar store
          |                                         |
partial event / birth registry       historical admission and feature targets
          +------------------ sealed datasets -----+
                                      |
                             candidate training
                                      |
                  immutable registry / future forecasts
                                      |
                         separate virtual paper books
                                      |
                     operator and Control Center

Watch Market: user-selected mint/pool -> verified provider -> isolated book
```

The component map is:

- `memecoin_ai`: domain, causal replay, portfolio, execution assumptions, and generic research.
- `memecoin_brain`, `memecoin_brain_sources`: OHLCV forecasts, features/targets, provider and
  raw-chain ingestion paths.
- `memecoin_continuous`, `memecoin_collector`, `memecoin_curriculum`: persistent bars, source
  scheduling, history shards, admission, training, and evaluation.
- `memecoin_registry`: immutable dataset/model manifests and qualification records.
- `memecoin_ml`: empirical/neural forecasters, training and benchmarks.
- `memecoin_paper`: virtual accounting, projections, supervision, and audit.
- `memecoin_operator`: durable jobs/commands, Control Center, deployment checks, and Watch Market.

These package boundaries are real code. They do not imply one integrated high-throughput path:
raw-chain event history is not yet a broad qualified USD feature stream; the current AUTO trainer
still materializes bounded datasets; Watch Market is not installed on the owner runtime.

## Learning path today

Brain currently learns bar-based return quantiles. Exact target, feature, architecture, recipe,
champion comparison, and experiment results have one owner:
[`training-evaluation.md`](training-evaluation.md). Brain is a forecaster, not a policy/execution
model, MPC, RL agent, or LLM.

The latest candidate was not promoted. Its exact comparison and causal diagnostics are in
[`training-evaluation.md`](training-evaluation.md).

## Intended scaling direction

Keep data acquisition, canonicalization, feature generation, training, evaluation, and paper
separate as versioned stages over immutable inputs. A future scale path should support:

1. Continuous raw capture and resumable coverage accounting across venues and time ranges.
2. Versioned decoders and quarantining by unsupported partition, without discarding raw evidence.
3. Partitioned feature/label datasets and streaming batches, not full JSON materialization in RAM.
4. Explicit target population, training exposure, compute budget, and independent evaluation roles.
5. Workers on local hosts first, with immutable shard/model transfer; shared mutable SQLite remains
   single-writer. Add distributed storage/training only after measured bottlenecks require it.

The 48k retained-row / 500k materialization / 1k-update limits describe the current experimental
recipe, not the desired capacity ceiling. No 10x/100x/1000x load test has been run. See the data,
training/evaluation, and operator documents for measured constraints.

## Research tracks

Brain Development is separate from frozen F01/D43; protected holdouts remain unavailable.
Synthetic replay, OHLCV forecasts, virtual paper, and real execution have distinct validity
domains; see [`contracts.md`](contracts.md).

## Owner and worker hosts

Mac 1 is the owner/control host for production/source/operator state. Mac 2 has a worker checkout
and can process sealed research artifacts. No shared mutable database or production credentials
are transferred to the worker. The current release topology and exact dated owner status are in
[`operator-runtime.md`](operator-runtime.md) and [`memory/now.md`](../memory/now.md).
