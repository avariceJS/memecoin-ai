# Brain Architecture

**Status:** exploratory supervised forecasting and virtual paper; no demonstrated trading edge.
This document owns the current system map and its intended growth boundary.

## Product goal

Build a persistent Solana research Brain that can acquire large market histories, produce
reproducible model generations, and measure improvement on independent future markets. V1/V2/V3
must represent meaningful increases in useful corpus, training compute, and validated capability,
not a new checkpoint from a tiny fit. The two Macs are the first compute stage. The architecture
must allow later GPU and data-center capacity without losing provenance or safety.

The target is a continuously growing, multi-venue market corpus and learning system that can expand
from thousands of markets to much larger populations, modalities, worker fleets and future model
generations. Current host, provider, fixture, and model limits describe measured capability today;
they do not set Brain's architectural scale boundary. Preserve stable data, provenance, recovery,
and ownership contracts so scale can grow without replacing the sensing foundation.

The user reports two Macs, paid Alchemy, strong research/coding models, and willingness to expand
storage/compute. Alchemy Free published limits and a bounded PAYG read-only series have now been
checked; account choices and receipts are recorded in
[`brain-source-architecture.md`](brain-source-architecture.md). Continue to measure metered work per
account and research task; request count alone is not a CU estimate.

## Current system

### Integrated research migration boundary

The opt-in `memecoin_experience` path now connects a read-only continuous-index export to
qualified portable sources, versioned experience snapshots, research generations, standalone
prediction and registered evaluation. `python -m memecoin_experience pipeline` runs and recovers
that path without acquisition, AUTO, promotion or owner credentials. This is research integration,
not a production Brain replacement or demonstrated market improvement.

The data plane owns immutable captures, qualified source manifests, representation contracts and
content-addressed partitions. Source locations are resolution hints outside dataset identity.
Disjoint increments compose into a new snapshot without rewriting feature payloads; replicas
verify the same object/snapshot pins on a second local store. Domain producers qualify observations
and preserve dependencies; the dense materialized-view contract is independent of source names,
feature count, horizon count and neural architecture. Existing v1 snapshots remain readable.

The compute boundary owns a pinned trainer recipe, exact resume state, model specification and
sealed artifact. Generation declarations bind logical owner/name, snapshot, recipe, code and
parent artifact. Warm starts freeze the parent's normalization and initialize a fresh optimizer;
resume remains the identical-job operation. A standalone predictor and separately registered
candidate/enrollment/window evaluation consume these artifacts. No serving runtime is switched.

Real qualified observations include USD OHLCV, evidenced births, and a tightly scoped finalized-slot
instruction-reference count sample. This on-chain sample verifies two adjacent slots and one next-slot
count; it does not qualify economic swaps, a complete market event process, provider exhaustiveness,
or lifecycle death. The sparse observation layer
separates immutable typed event/lifecycle/support facts from representation snapshots; it no longer
requires long consecutive bar runs to emit experience. Synthetic flow/count objectives exercise
short and zero-event histories under explicit observation completeness. They are not new qualified
order-flow or liquidity datasets. Unknown venue semantics stay outside admission. Real qualification
uses historical replay; sparse arrival-time contracts are exercised synthetically and are not
retroactively assigned to historical candles.

Local filesystem publication/locks and CPU training are implemented. Single CUDA is implemented
but unqualified; DDP, object-store conditional publication and a cluster scheduler are not present.
Those are backend/trainer extensions behind immutable snapshot and generation ownership, not
pretend distributed coordination. Next priority is integrated qualification on explicit research
populations, then source and GPU throughput work based on measured failures.

The first integrated CPU qualification on real captures is complete: fixed 24-market development
enrollment, cumulative/replica snapshots, parent and fresh generations, exact hard-kill recovery,
and separate retrospective evaluation. Four markets still supplied 95.62% of cumulative training
exposure; generation gains were mixed by horizon. The next constraint is broader useful data and
actual GPU qualification, not another increase to a retained-row cap. Exact scope and receipt:
[`training-evaluation.md`](training-evaluation.md#real-data-system-qualification-2026-10-08).

The `memecoin_population` layer now makes enrollment, coverage, usable corpus and optimizer
exposure separate pinned contracts. Append-only temporal metadata and coverage facts feed sparse
source work plans and generic diversity audits; audits bind owned generations and their committed
exposure journals. Evaluation registers its own population and axes before scoring. Neither
sampling nor loss is silently balanced. The integrated route is now:

`declared populations/streams → reconciled acquisition → immutable captures → qualified experience
→ representation snapshots + diversity audit → owned training + exposure → registered cohort evaluation`.

Planning/reconciliation interfaces are implemented; provider dispatch remains in the existing
acquisition plane. Real order-flow/on-chain semantic producers, independent broad enrollment and
GPU/distributed backend qualification remain substantive work. They can now add facts, streams,
representations and trainers without changing population identity or snapshot/evaluation semantics.
Capacity and real-data diversity findings are owned by
[`training-evaluation.md`](training-evaluation.md#diversity-qualification-2026-10-08).

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
- `memecoin_experience`: scalable research sources, snapshots, model generations and independent
  evaluation; its opt-in integrated pipeline is described above.
- `memecoin_population`: temporal enrollment/coverage facts, sparse work plans, diversity audits,
  committed exposure and registered evaluation cohorts.
- `memecoin_paper`: virtual accounting, projections, supervision, and audit.
- `memecoin_operator`: durable jobs/commands, Control Center, deployment checks, and Watch Market.

The new research pipeline connects the qualified bar lane end to end and v3 can preserve native slot
coordinates alongside UTC availability without implicit clock conversion. Raw-chain event history is
still not a broad qualified feature stream; a durable prospective collector and broad evolving pool
population are the nearest sensing priorities. The current AUTO trainer remains separate and bounded;
Watch Market is not installed on the owner runtime.

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

The 48k retained-row / 500k materialization / 1k-update limits describe the legacy experimental
path. The new experience pipeline has completed synthetic million-bar qualification including
two full CPU training epochs and independent evaluation. This does not qualify 100M-scale,
cluster execution or market usefulness. See the data, training/evaluation and operator documents
for measured limits and the exact code-bound receipts.

The independent 307-market birth campaign has now exercised population enrollment through real
CPU learning and independent-mint evaluation. Its conversion loss confirms the next architectural
priority: the active contiguous-OHLCV producer still excludes most short/sparse histories from
learning, even though storage preserves their enrollment and raw observations. Broader data improved
neural controls but did not beat empirical train quantiles. Preserve the existing data-plane,
snapshot, generation and evaluation boundaries; qualify sparse/event-time and lifecycle producers
and appropriate objectives before interpreting more downloads or larger neural fits as capability
growth. Exact results and limitations belong to `training-evaluation.md`.

## Research tracks

Brain Development is separate from frozen F01/D43; protected holdouts remain unavailable.
Synthetic replay, OHLCV forecasts, virtual paper, and real execution have distinct validity
domains; see [`contracts.md`](contracts.md).

## Owner and worker hosts

Mac 1 is the active development/research host as well as the owner/control host, using separate
research and production checkouts, environments and state directories. Mac 2 is an inactive standby
worker; the two-host path remains available for explicit future scale-out. No shared mutable database
or production credentials are transferred to the worker. Current paths and exact dated status are in
[`operator-runtime.md`](operator-runtime.md) and [`memory/now.md`](../memory/now.md).

## Portable deployment boundary

Brain's Linux deployment path is a platform adapter over portable release artifacts and explicit
state contracts. Linux host-specific responsibilities include ext4/device discovery, ownership and
mode checks, systemd unit policy, and service activation. Handoff-v1 copies a verified idle SQLite
state into a versioned generation, binds it to an exact release and effective service policy, then
requires a separate explicit activation. Recovery adopts only an exact generation tree, checks for
nested mounts and conflicting units before permission changes, and retains ambiguous artifacts for
operator review. This is a synthetic idle-state path; it does not make Migration v1 interchangeable
with Linux handoff or establish production Brain restore.

On Ubuntu 24.04/x86_64, clean-clone setup, deterministic source builds, real ext4/systemd staging,
process-crash recovery, inert activation/finalization, and host reboot checks have passed on the
isolated Linux portability branch. At the same source commit and build epoch, the wheel and source
archive are byte-identical between macOS arm64 and Ubuntu x86_64; source archive modes are
canonicalized to remove umask differences. A minimal Ubuntu rootfs also completed a clean venv setup,
first-boot stage/verify, explicit activation in a private network namespace, inert restart, and
idempotent restaging. The read-only Linux capability report can run directly from source before a
venv exists and now checks the exact `PYTHON` executable used by `make setup`, importing its `venv`
and `ensurepip` modules without creating an environment or using the network. It correctly detected
the absent `python3` alias and `udevadm` package on a minimal rootfs. The resulting source-build and
service-host roles passed after installing the corresponding Ubuntu packages. A follow-up check
also requires a present non-root `brain` account before declaring the service-host role ready,
matching the account required by Linux release staging. The current proof covers one CPU-only
x86_64 toolchain and one systemd host.
ARM64 runtime, Windows/WSL, CUDA/NVIDIA, power-loss behavior, production datasets, and unattended
host package provisioning remain unqualified. Keep account creation explicit and fail closed when
the required service identity is missing; package provisioning must not silently create users or
grant sudo. Preserve the adapter boundary: discover optional
compute and host capabilities explicitly, while release identity, state formats, manifests, and
restore semantics remain platform-independent. Recovery evidence and exact limits are indexed in
[`docs/evidence/README.md`](evidence/README.md).

The opt-in `tools/brain_linux_packages.py` bootstrap utility is restricted to Ubuntu 24.04 x86_64
inside systemd-nspawn and requires explicit `--lab-only` invocation. It uses a dedicated APT source,
index, and archive area with the official Ubuntu signing key and a caller-pinned archive snapshot.
The provisioner entry substrate must already provide `/usr/bin/python3.12`, APT/dpkg, the Ubuntu
archive keyring, and a live systemd PID 1. Check this read-only with
`tools/brain_linux_packages_preflight.sh` before invoking Python; raw minbase without these
prerequisites is outside the tool contract and must fail before any package-state writes. Construct
that installer substrate separately from the same pinned signed Noble snapshot and record its
builder inputs and base package inventory.
Index refresh is a separate explicit operation; read-only planning binds fixed package roles,
dependency closure, versions, solver output, installed state, source hashes, and host capability
report to a digest. Apply rechecks that digest and all observed inputs, refuses removals, downgrades,
unrelated package changes, or active Brain units, and blocks service starts during package scripts.
The inert-service role explicitly includes `bsdutils` as an Ubuntu Essential transaction companion
for the pinned util-linux/systemd package set; it is not a capability requirement. Any other solver
change must be reviewed and added explicitly before it can pass the package transaction allowlist.
The source-build role may start from a verified externally acquired source artifact when Git is not
yet installed; it records that preflight gap and requires the exact clean source commit to become
verifiable after installation. Receipts are create-only and root-private. The utility does not
create users, grant sudo, auto-run from setup or restore, or promise rollback after an interrupted
dpkg transaction. This lab-only
contract does not qualify ordinary host provisioning or package update policy.

The fixed inert-service package role has now passed plan/apply on a disposable Noble systemd-nspawn
rootfs at snapshot `20261018T084900Z`. The exact transaction included the explicit `bsdutils`
companion and its solver closure, with no removals; a second plan and apply produced an empty
transaction. This demonstrates the package role in a containerized rootfs only. It does not qualify
bare-metal package provisioning, activation, rollback after interrupted dpkg, or power-loss recovery.
See the [package-role receipt](evidence/brain-linux-inert-service-package-qualification-2026-10-18.json).
