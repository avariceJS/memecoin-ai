# Current Project State

**As of:** 2026-10-12 02:41Z. Next horizon: choose an independent Senses horizon while Agave source
closure remains terminal after the one approved retry failed.
Research checkout: `/Users/root1/Developer/memecoin-research`, branch `research/active`.
Last published active/main: `148ca165e1487a99c3ce6532dbc276c4922c31b6`. Current work is uncommitted.
Production runtime, champion, protected evaluation and real trading remain untouched. No signing or
submission. Do not start Cortex.

## Live research state

- The reviewed Helius external-v2 02:30–03:33Z lease was initialized offline under host/storage/
  predecessor locks. Its bootstrap ended terminal `missed_bootstrap`, with verified predecessor
  lineage, zero provider requests/reservations/intents, and no sealed bucket. The exact LaunchAgent
  is unloaded and disabled. No retry, catch-up, backfill, schedule shift, or later-frame activation.
  Receipt: `docs/evidence/brain-senses-external-v2-missed-bootstrap-terminal-2026-10-12.json`.
- Kingston `/Volumes/Kingston/BrainData` remains bound to UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID `9428695af6d4f46c08e1a444cdc66de8`; latest
  health is ready with ~206 GiB free, above the 50-GiB floor. No formatting or internal fallback.
- Helius read-only account tools still cannot show plan/usage/charge (no JWT); Alchemy aggregate
  remains 18,680 CU / $0.01 without account limit or workload attribution. Helius lease charge is
  unknown; the lease itself made zero RPC requests.

## Build/source qualification

- Descriptor-bound storage supervisor is APFS-qualified on disposable images through v4, including
  forced-detach no-underlay writes. Its opt-in Cargo Git symlink exception accepts only relative
  links within one ordinary repository/revision tree; cross-revision, cross-repository, absolute,
  and structural symlinks fail closed. Receipt: `docs/evidence/brain-senses-build-storage-guard-apfs-qualification-2026-10-12-v4.json`.
- One Sol-approved Agave `cargo fetch --locked` attempt stopped on four upstream Crossbeam
  `../no_atomic.rs` links. The partial cache at
  `.../tmp/fetch-cargo-home-v1` is preserved and not reused; source diff and lock pins are unchanged,
  target is empty, no process remains, and no build/offline metadata ran. Failure/correction receipts
  are in `docs/evidence/agave-dependency-fetch-attempt-2026-10-12.json` and its source-correction
  receipt. Corrected wrapper passed 16 focused tests, Ruff/mypy, APFS v4 and separate-root
  `--check-only` with zero network. Preflight receipt:
  `docs/evidence/agave-dependency-fetch-preflight-2026-10-12-v2.json`.
- Sol approved one fresh bounded fetch in the separate v2 paths. It exited 101 after about 16s;
  Cargo stdout/stderr were discarded, so the cause is unknown. The partial v2 cache (26,104 KiB,
  499 files) is preserved and must not be reused or cleaned; target/tmp are empty, source diff and
  lock hashes match, and no process remains. No retry, metadata, or build. Receipt:
  `docs/evidence/agave-dependency-fetch-attempt-v2-2026-10-12.json`.
- Source closure is terminal unless a separately reviewed disposition is made. Candidate-position
  ProgramTest witness remains an offline goal, but no build or economic claim is approved. The next
  horizon should advance independent Senses coverage/qualification without c4 replay or protected data.

## Continue Senses

After source closure, prioritize candidate-position runtime witness, independent native settlement/
buyExact25 qualification, then broad causal population/lifecycle/modalities and unknown/anomaly
qualification. Preserve event, receipt, availability and decision times, immutable provenance, and
unknown/censored states. Frozen c4 remains terminal; protected samples stay unopened. Continue
focused tests and update canonical owners; commit/push complete scopes to `origin/research/active`,
then fast-forward `main` only at a verified capability milestone.
