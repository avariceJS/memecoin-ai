# Shared Contracts and Validity Boundaries

This is the canonical owner for cross-cutting data, replay, training, and artifact invariants.
Domain-specific behavior belongs in the architecture, data, training/evaluation, or operator docs.

## Time and availability

- Normalize timestamps as integer UTC nanoseconds; this is an encoding, not nanosecond accuracy.
- `event_ns` orders a market event, `received_ns` records transport receipt, and `available_ns`
  determines whether a model could use it. Keep `decision_ns`, prediction publication, and label
  maturity separate as well.
- Block time is not receipt time. Backfilled history is not retroactively available to a past
  forecast.
- Preserve observed, assumed (with a named assumption), and unknown as separate states.
- An observation later than the decision cannot enter features, scaler fitting, or label selection.
- Sparse point-in-time origins use arrival/availability as decision time and retain event time
  separately. Historical replay uses event-time decisions and explicitly permits later receipts
  up to its fit cutoff; it must never be reported as online-causal experience.

## Identity, revisions, and storage

- Raw bytes and receipts are immutable and content-addressed. Corrections create new versions;
  they do not rewrite the first-seen value.
- Canonical IDs deduplicate exact repeated observations. Conflicting records reject/ quarantine
  rather than silently overwrite.
- A sealed bundle manifest binds record hashes, source/code/schema identity, and membership.
  A matching hash proves byte identity, not source truth, coverage, or honest provider behavior.
- SQLite is a local transactional metadata/journal store with one canonical writer. It is not a
  full-chain event store. Read live WAL through a consistent read snapshot; do not mark it
  immutable or copy the database without its WAL state.
- Keep secrets, API keys, and credentials out of Git, raw summaries, logs, and task packets.

## Missing data, admission, and splits

- Missing bars, unsupported protocols, stale observations, failed requests, and immature labels
  retain explicit reasons and denominators; none means a zero return.
- Raw capture, decoded event, feature-ready origin, mature label, training admission, and execution
  qualification are different gates.
- A feature/target row may enter a training role only if every input was available by the fit
  cutoff and all target outcomes matured within their assigned interval.
- Purge complete target and feature dependencies across split boundaries. Keep train-only scaler
  fitting and record source membership, pool/mint identity, and exposure.
- Failed/unsupported markets remain in the original enrollment denominator; do not replace them
  after observing their outcomes.
- Sparse endpoint returns require an exact unique observed future endpoint and a positive observed
  origin value, not uninterrupted intervening events. In arrival mode the origin value may be old;
  its age is explicit and it is not a newly observed decision-time mark. No exact endpoint means
  unknown return, never zero. Process counts are exact only under complete coverage of their named
  process; other counts retain a lower bound with censoring and never become zero-valued labels.
- A zero count is exact only when a hash-bound qualification receipt verifies complete support for
  the named process and exact entity/interval member in its pinned population scope. Historical event
  time does not backdate member availability. Unknown, partial, failed, stopped, unsupported or
  absent assertions cannot establish zero; lifecycle changes require their own evidence.
- Hash and accounting checks establish byte identity and internal consistency, not provider truth or
  semantic completeness. Keep machine validation distinct from domain source qualification.
- `market-observations-v1` has no `partial` support value. When projecting a partial assertion, map it
  to `unknown` there while preserving the partial status and qualified lower bound in population
  coverage/evidence. Event-count coverage has no expected event denominator; keep it null.
- Administrative ends, immature targets and missing endpoints remain distinct. The default
  exact-label objective admits observed labels only. The opt-in, versioned
  `partial_count_lower_bound_pinball_v1` also consumes verified positive process-count lower
  bounds with a one-sided per-quantile hinge; zero bounds and unavailable support remain excluded.
  This is not a survival likelihood and does not identify the full count distribution. Full
  dependency purging includes lifecycle facts; long-lived birth dependencies can exclude later
  split rows and must be reported.
- Protected F01/D43 and reserved holdout are out of scope: do not read, mutate, relabel, or train on
  them from this exploratory Brain track.

## Synthetic replay and real market data

`ReferenceCPMM` and `ReferenceReplay` are synthetic engineering references. They are not a live
Raydium adapter or market-edge evidence. The bounded real CPMM decoder has a narrower validity
domain and does not generalize to CLMM/DLMM venues.

Use integer asset atoms and explicit quote denomination at validated boundaries. Do not silently
convert WSOL/USDC to USD. Token-2022 extension type IDs may be inventoried while semantics remain
unsupported; unknown transfer fees, hooks, withheld amounts, configs, and account layouts fail
closed. A pinned public IDL is a schema reference, not proof of deployed binary equivalence.

On-chain provider agreement is not independence if providers share upstream data. A confirmed
transaction or vault balance is not a quote, counterfactual fill, or historical receipt timestamp.
Venue execution, fees, latency, inclusion, and failure behavior require separate evidence.

## Paper ledger and risk

Paper uses Decimal accounting and explicit virtual cash/positions. Fills bind to a fresh observation
available after decision; intents are not fills. Ledger reservations prevent double spending;
settlement is atomic and tied to original intent/assets. Preserve ledger prefix and cash through
recovery/migration; never reset a book to make a result look clean.

The current paper slippage/fee values are assumptions, not calibrated execution. Stale/unsupported
observations cannot close positions at invented prices. A conservative mark is not proof of exit.
Paper PnL cannot authorize real trades. No signing/submission or real-money execution is in scope.

## Features, models, and scoring

`Features` and `Target` are separate immutable objects. `Forecaster.predict` takes no target.
Training examples require labels available by their cutoff. Preserve target definition, horizon,
pool/origin identity, quantile order, censoring reason, and model hash in scoring.

Pinball and coverage are forecast metrics, not execution metrics. Report per horizon and relevant
market strata; pooled means can hide offsetting biases. Overlapping origins are dependent. Any
uncertainty estimate must resample valid time/mint blocks and state the support; iid rows are not
an independence claim.

Candidate selection and promotion are distinct. Keep the official champion as a frozen baseline.
An immutable candidate, a successful fit, a future exam, or a paper score does not automatically
change the champion. No promotion gate may be bypassed or weakened to admit a candidate.

## Run and release provenance

Each training/research output uses a new reserved directory and records code/data/config/split/
seed/recipe identity, environment, actual draws/compute, failures, result hashes, and completion
status. Failed or partial attempts remain visible and cannot be published as completed candidates.
Never overwrite sealed runs or dataset snapshots.

Runtime release pins the exact source/runtime manifest. Validate host ownership, jobs/exams,
champion, source lease, deployment pointer, and health at the owner before a switch. Worker checkout
does not imply release permission or production state.

For implementation contracts, read the owning package and tests. This document records invariants,
not a substitute for source-level interface review.
