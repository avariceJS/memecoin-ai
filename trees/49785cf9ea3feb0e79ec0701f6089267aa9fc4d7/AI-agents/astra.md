# Independent forensic verdict: can this become a large Brain?

**Review date:** 2026-10-07. **Code:** `6feba83525c0d21392432c53becd79e8901650ea`.
**Scope:** independent adjudication of [High](claude-opus-5-High.txt),
[5.5](claude-opus-5-5.txt), and their [handoff](../brain-independent-review-handoff-2026-10-07.md).
Both reviews and the handoff were read in full. Findings below come from tracing the current
call paths and inspecting the named machine receipts, rather than accepting their summaries.
The relevant `src/` implementation is unchanged from the handoff's `2b834db` code basis.

This is a source/evidence review, not a reproduction of training or a load test. Existing test
code was inspected; no tests, experiments, inference, acquisition, or runtime mutations were
performed. F01/D43 and reserved holdout were not opened. A read-only owner-status attempt using
the existing pinned SSH configuration timed out. Consequently, the last verified runtime
observation remains the [17:16:36 UTC receipt](../evidence/brain-handoff-status-2026-10-07.json):
paused, empirical champion, ten returned jobs completed, three exams expired, deployment file
matching runtime08. This review does not freshly attest the running executable or current owner
state. Numerical experiment findings below are receipt-backed, not independently rerun.

## 1. Verdict

**The project has a reusable correctness and provenance foundation, but its active learning
architecture is not yet a foundation for a continuously improving, large Brain.** It can retain
substantial history and train reproducible small candidates. It cannot yet turn expanding market
coverage into expanding learned capability through a sustained, qualified, measurable process.

The decisive problem is a conjunction: incomplete semantic data production; bounded consumption
of retained history; changing training objectives disguised as corpus expansion; a representation
that omits most market state; and evaluation too narrow to tell which capabilities improve.
Trading usefulness is a further unvalidated connection. Fixing acquisition alone will not fix this
chain. Neither will a larger network, a calibrator, or a different learning rate alone.

**5.5 is closer on the main architectural diagnosis. High identifies useful implementation
failures but bases its headline explanation on the wrong training path.** High's claim that the
current system is structurally forbidden from learning because it only reads seven days is false.
5.5's assurance that the present GRU is sufficient, and that everything follows from a continuous
data product, is also unsupported. There is no demonstrated scaling law here, positive or negative.

The appropriate direction is a deliberate migration of the data, experience, training, and
evaluation boundaries while preserving their useful contracts. The target should be a system
where more retained evidence can produce more diverse usable experience, larger and longer fits,
and independently demonstrated capability. The current model should survive as a baseline, not
as the permanent definition of Brain.

## 2. Adjudication of the disputed claims

### The seven-day cap: High is wrong about current AUTO

The filter exists in `memecoin_paper.service.source_rows(training=True)` and the historical
`archive.rolling()` comparison arm. But current AUTO calls
[`adapters.snapshot()`](../../src/memecoin_operator/adapters.py), which backs up the source index,
calls `view(output, cutoff)` with **no `since` argument**, merges pinned historical corpora, and
calls `learning.prepare()`. [`store.view()`](../../src/memecoin_continuous/store.py) defaults to
`since=0`. [`partitions()`](../../src/memecoin_operator/learning.py) assigns training from time
zero to `end - 21600`; that is an end boundary, not a six-hour training duration.

This is corroborated by the [expansion receipt](../evidence/brain-training-expansion-2026-10-06.json),
`support.broad`: **48,000 retained decisions covering 104 distinct UTC days**, versus 93 control days.
The existing [historical-training test](../../tests/operator/test_historical_training.py) explicitly
exercises admission of history ten days old into AUTO. This was already true at the reviewers'
code basis; it is not a later repair.

There is a real consumption ceiling, but High misidentifies it: full materialization, an additional
510,000-row snapshot guard, a 500,000-sample assembly guard, 48,000 retained training decisions,
and a fixed update budget. Deleting the old seven-day filter would not repair current AUTO.
Reading all retained history with equal influence would also be a modeling choice, not an
automatic remedy for nonstationarity.

### Evaluation: both identify a real failure; High overstates the mechanism

Current B13 has three-hour validation and three-hour test blocks. It does **not** have a
six-hour train block. Other curriculum/research paths have different windows, so three hours is
not a repository-wide impossibility. It is nevertheless a serious defect in the active selector.

The [latest audit](../evidence/brain-post-training-audit-2026-10-07.json) confirms 264 validation
and 249 test decisions, eight old pools, and no useful population uncertainty estimate. A
three-hour test can gain more rows from more pools, but cannot acquire multi-day regime coverage.
Adjacent temporal folds are not automatically leakage or the same experiment; they are an
inadequate basis for the claimed breadth of generalization here.

Both reviewers also over-credit “group-disjoint splits.” B13 purges whole origins when their
longest target crosses a boundary. It does not enforce mint-disjoint folds or disjoint raw input
dependencies. Recent historical inputs may overlap across folds. That can be valid for forecasting
known markets, but cannot establish cold-market transfer or independent episodes.

### Quantiles and trading: High diagnoses misalignment, then misreads the evidence

[`scoring()`](../../src/memecoin_brain/model.py) defines coverage as `y <= predicted_quantile`.
Therefore 1.606% at q10 and 98.996% at q90 indicate excessive empirical interval coverage, not
tails collapsed inward. The fraction satisfying `q10 < y <= q90` is approximately **97.39%**,
against nominal 80%. The 77.912% median coverage indicates upward bias on this sample. These
aggregate statistics do not identify every conditional interval's width or cause.

[`paper()`](../../src/memecoin_paper/service.py) selects `horizon == 3`: the +1.6% median threshold
acts on **15-minute forecasts**, not five-minute forecasts as High says. Entry then waits for a
fresh later close, and [`engine.advance()`](../../src/memecoin_paper/engine.py) schedules exit
900 seconds after that entry bar. The forecast and executed intervals therefore differ.

Pinball loss is not intrinsically the wrong objective for useful conditional quantiles. For a
continuous conditional distribution its expected derivative with respect to a predicted quantile
is `F(q | x) - tau`; near-zero observations are not inherently wasted gradients. Nor is the code
learning the “whole distribution”: it supervises three quantiles at two horizons. Better pinball
can help a decision system, but does not establish useful ranking near costs, expected net return,
fillability, or risk. A median threshold is not an expected-profit calculation.

High's “coin flip,” “exactly explains zero fills,” and “trading quality is not reachable by
improving pinball” exceed the evidence. Entry cancellation, freshness, source health and exposure
limits have their own code paths. A calibrator cannot repair the different execution interval or
supply missing liquidity/cost information. **Keep forecast scoring; add a separately specified
decision problem and policy-aligned validation.**

### Optimization and capacity: neither review establishes the binding limit

Controlled receipts support an effect of sampling, additional updates, and learning rate on the
specific tested paths. They do not prove sufficient capacity or future-market gains:

- [Sampling](../evidence/brain-sampling-ablation-2026-10-07.json): changing new-cohort mass alone
  improved exposed-test ensemble loss by 11.91%; young-market performance was not evaluated.
- [Budget](../evidence/brain-budget-ablation-2026-10-07.json), `effects.selected.test`: additional
  updates improved loss **8.95% current / 10.05% capped**. The **20.76%** quoted by 5.5 as a budget
  effect combines sampling and budget. The canonical training document also misplaces 10.24%
  into the current-sampler test result; that number belongs to validation. The handoff has the
  correct 8.95%. Primary receipts take precedence over all three narratives.
- [LR](../evidence/brain-lr-diagnostic-2026-10-07.json): lower LR stabilized train coverage on
  matched continuations. It did not score validation/test and slightly worsened current-sampler
  train loss. This is evidence of conditional optimizer sensitivity, not a recommended default.
- [Clipping](../evidence/brain-clipping-audit-2026-10-07.json): 100% activation is established;
  harmful causation is not. High's claim that clipping replaces the effective learning rate is
  incorrect for AdamW. Global clipping rescales a batch gradient; AdamW still applies LR to its
  moment-normalized update. Constant scale can substantially cancel between moments; varying
  scale changes their history. It does not rotate the instantaneous gradient. See the primary
  [AdamW algorithm](https://docs.pytorch.org/docs/2.14/generated/torch.optim.AdamW.html) and
  [global norm clipping definition](https://docs.pytorch.org/docs/2.14/generated/torch.nn.utils.clip_grad_norm_.html).

5.5 mistakes draw-probability ESS near 1,172 for effective independent training N. It measures
concentration of sampling mass, not the amount of independent information or required model size.
Its assertion that the bottleneck is optimization rather than capacity is untested. High is right
that representation deserves much more attention, but twelve features do not mathematically
establish “almost nothing for a larger model to learn.”

The [shared head](../../src/memecoin_ml/models.py) really forces a history-independent median
horizon difference. The [head intervention](../evidence/brain-horizon-ablation-2026-10-07.json)
did not establish it as the main cause of failure. Improve head expressiveness as part of a
coherent task design; do not promise a calibration cure.

### Acquisition and scaling: High's concrete findings survive, with narrower scope

[`source.reserve()`](../../src/memecoin_continuous/source.py) sets `history_done=True` before IO.
A valid historical outcome may clear it and advance the cursor; transport failure does not.
The normal scheduler then stops that pool's history work. Crash recovery can ingest a sealed
capture without restoring pagination eligibility. **The normal recovery path strands unfinished
coverage.** Reservation-before-IO is correct accounting; conflating reserved/failed/completed work
is the defect. Preserve the charged attempt and represent retryable or unresolved gap debt
separately. This is not proof of how much history the provider actually had.

[`Ledger.audit()`](../../src/memecoin_runner/ledger.py) loads and hashes the entire event chain on
construction; source reservations/outcomes contain full pool state. For E events and P pools,
state storage is roughly O(E·P); repeated reconstruction over an accumulating journal can produce
O(E²·P) cumulative work. Calling the ledger itself simply “quadratic” loses this distinction, but
the scale warning is correct. Full snapshot copies, `fetchall()`, full raw audits and recursive
artifact verification add further global work.

The serial GeckoTerminal fetcher and default twelve-second spacing are real. Roughly 7,200
attempts/day is only the ideal pacing ceiling for that configuration, before latency, cooldown,
expiry and other budgets; it is not a provider/account or whole-project limit. Paid Alchemy
capacity does not automatically accelerate this qualified USD path. There is already some
live/background prioritization and raw-vacuum cursor/gap reporting, so “no scheduling or gap
tracking exists” would also be false. These pieces are not an integrated coverage service.

The [streaming utilities](../../src/memecoin_curriculum/streaming.py) are useful, with existing
equality tests for lazy/mmap batches. High overstates “the work is already done.” Whole-corpus
source reads, scaler fitting, membership, sampler probabilities, audit costs, shard locality and
device transfer still need integration. `TensorCache` explicitly leaves training membership to
its caller; its constructors still verify artifact bytes. A GPU path also requires more than
adding `.to(device)` to this pipeline. Neither laptop nor cluster throughput is qualified.

### 5.5's roadmap contains unsupported shortcuts

“PumpSwap 90% done,” USD conversion “not hard,” 90% admission yield, seven-day/1,000-window
statistical adequacy, fixed training-hour limits and the proposed implementation weeks have no
supporting qualification here. Its 965 → 100 → 62 → 7,987 diagram combines stages from different
campaigns; it is not a measured end-to-end conversion rate. The raw registry and subsequent bar
admission must retain their own denominators.

The [decoder](../../src/memecoin_pumpswap/decoder.py) explicitly leaves `training_eligible`,
`execution_supported` and `protocol_qualified` false even for reconciled candidates. Remaining
accounts, Token-2022 semantics and unknown event suffixes have blockers. The
[raw corpus schema](../../src/memecoin_pumpswap/corpus.py) enforces `training_eligible=0`.
Completing a wire layout does not establish economic semantics, complete history, reliable USD
conversion, usable features or execution. High's “ordinary adapter work” also understates this.

Preserve exact raw bytes; columnar decoding should be a derived layer, not a replacement for raw
RPC evidence. Traffic weighting is not interchangeable with volume weighting. “Calibration
within ±10pp” is particularly weak for a nominal 10% tail and needs a decision-specific precision
requirement. Retire obsolete orchestration through a migration, but **do not delete or release
protected F01/D43/holdout artifacts**, as 5.5's removal list suggests.

## 3. Where scale and information actually disappear

**Market data → qualified experience.** Raw collection has real value, but the
[vacuum receipt](../evidence/brain-vacuum-2026-10-06.json) reports 965 registry markets and zero
qualified USD decisions from that raw lane. This is an absent semantic bridge, not evidence of
965 useless markets. Current training admission obtains USD bars from a separate provider.
[`admission.qualify()`](../../src/memecoin_curriculum/admission.py) is specifically bound to
GeckoTerminal base-token USD five-minute bars in a birth-to-three-day window. It is a bounded
cohort adapter, not a general lifecycle experience contract. Unknown layouts, incomplete
pagination, quote conversion and sparse bars all interrupt the bridge at different points.

**Qualified experience → training corpus.** The expansion added 7,987 eligible decisions but
displaced exactly 7,987 old decisions under the unchanged 48k cap. Training pool count rose
24 → 86 while row-share effective pools rose only 6.19 → 9.55. Retained breadth and retained
depth trade against each other. Immutable storage accumulates evidence; the fitted snapshot
does not grow proportionally with it. These figures describe the expansion, not the later AUTO
snapshot or raw vacuum.

**Training corpus → optimizer exposure.** [`weights()`](../../src/memecoin_curriculum/design.py)
assigns equal pool mass, then equal pool-day mass. The new cohort owns 16.64% of rows but 72.09%
of draws; twenty pools with fewer than sixteen decisions consume 23.26% of sampling mass.
[`fit()`](../../src/memecoin_curriculum/training.py) makes 64,000 replacement decision draws per
fit, including both horizons per draw. Thus expansion changes the objective and repeatedly
shows sparse young pools, while the evaluator remains old-only. This is neither a clean
data-scaling experiment nor evidence that young markets damage a general Brain.

**Exposure → model.** Every normal B13 fit creates a fresh width-16, context-8 model and
optimizer. There is no automatic inheritance of competence from the previous generation. Full
retraining can be a valid accumulation strategy, but only if corpus selection and compute let it
relearn the growing experience. Here both remain bounded. The 2,174-parameter GRU compresses
eight frames of twelve OHLCV transforms, masks and relative frame age. It receives no pool age,
market-wide state, depth, order flow, holders, deployer history or venue state. Relative frame age
is not token lifecycle age. More RPC history cannot enrich these inputs without an explicit
feature/representation path.

**Model → evaluation.** Checkpoints optimize validation pool-macro pinball; the run selects among
its freshly fitted empirical/linear/GRU candidates. The frozen official champion is not a member
of that selection set. A selected GRU can therefore be the best candidate in a badly matched
refit and still lose to the official empirical model. The later matched audit finds +41.95%
test loss on 249 decisions; it establishes that descriptive comparison, not general inferiority.
The newer [research envelope](../evidence/brain-research-envelope-2026-10-07.json) has 453 origins,
six supported mints, 29 globally dependency-disjoint windows and one mint contributing 71.08%.
It does not resolve the coverage problem. Benchmark metadata validation is not benchmark data.

**Evaluation → trading usefulness.** Three return quantiles omit a usable model of costs,
capacity, market disappearance, fill failure and the path between origin and execution. The
paper engine provides accounting under assumptions, not those missing observations. Neither
zero fills nor a few costed trades demonstrates economic quality. The eventual Brain must prove
useful conditional information and then prove that a declared policy can exploit it under
measured constraints. Those are separate accomplishments.

## 4. Problems the reviews missed or did not develop enough

**Admission can create outcome-dependent selection.** B13 requires both horizon labels to exist
before admitting any origin to a fold. A valid 5m target is discarded when 15m support is missing.
Contiguous-history admission also removes sparse lifecycle segments. The actual minimum is eight
consecutive context bars; fifteen is the maximum raw dependency span of fully populated context
features, not a universal fifteen-bar admission gate. If disappearance or missing
future bars correlates with adverse market states, complete-case training learns a selected
surviving/observable population. The code confirms the selection mechanism; its empirical bias
has not been measured. Neither zero-filling nor calling every disappearance a rug is justified.
Preserve enrolled origins, observation processes and horizon-specific validity. Distinct
supervision tasks can use different qualified subsets without weakening trading admission.

**Transitive feature availability is incompletely represented.** In
[`data.build()`](../../src/memecoin_brain/data.py), a frame's `values` are computed from up to
eight raw bars, but its `available_ns` and `capture_sha` name only the final bar. A sample takes
the maximum receipt of its eight direct context rows, while those frames can depend on up to
fifteen raw bars. An older dependency received later can therefore be omitted from the advertised
availability maximum and per-frame lineage. The global snapshot cutoff still checks every raw
row, and prospective prediction is published after that cutoff: **this is not evidence that the
reported run used future data**. It is a concrete weakness in the reusable dependency contract,
especially for incremental/as-of feature services. Availability must propagate over the complete
dependency graph; retaining raw provenance makes repair possible.

**The growth trigger measures the old source, not growth of useful competence.**
[`adapters.snapshot()`](../../src/memecoin_operator/adapters.py) deliberately keeps the AUTO
growth denominator in the continuous-source namespace even when historical admission expands
training. [`control.evaluate()`](../../src/memecoin_operator/control.py) compares that count
with the previous snapshot and a time/quota threshold. This prevents a mixed-namespace subtraction
bug, but historical additions, improved decoding, new tasks and benchmark gaps cannot naturally
drive the next learning job. A future generation scheduler needs versioned corpus/task deltas
and exposure plans, not merely more mature bars from the legacy live lane.

**Small-model failure cannot be interpreted without a learnability diagnosis.** There is no
registered broad experiment separating insufficient conditional information, optimizer failure,
insufficient capacity, population shift and inadequate label semantics. The model may mostly be
estimating changing volatility and a median near zero; that can produce forecast gains without
tradable directional information. Conversely, fixing the optimizer on the current representation
does not test the richer Brain hypothesis. Both explanations must remain available.

**More rows and continuous weight updates are not the same as accumulating knowledge.** Reusing
adjacent origins increases exposure without creating equally many independent market episodes.
Two pools can describe the same mint; shared market shocks couple different mints. Corpus growth
needs lifecycle, time, venue and regime diversity accounting, and duplicate/related-market
identities. Persistent raw memory plus reproducible periodic retraining may be preferable to
online weight updates initially. Any later continual-learning recipe must measure forgetting,
recent adaptation and old-skill retention explicitly.

**Provenance has become coupled to global reprocessing.** Full verification at every consumer,
absolute local capture paths, repeated SQLite copies and JSON reconstruction prevent the present
artifact interfaces from transferring unchanged to a cluster. Preserve evidence guarantees through
immutable partition identities, verified publication, dependency manifests, bounded local checks
and scheduled audits. A hash and a successful rebuild prove internal consistency; they do not
prove semantic truth, source completeness or statistical independence.

## 5. What is worth carrying forward

The strongest reusable assets are concrete: raw capture preservation; first-observed/revision
separation; event/receipt/decision clocks; deterministic membership and train-only transforms;
explicit missingness; delayed forecast outcomes; sealed candidate identities; candidate/promotion
separation; charged reservations; and Decimal paper accounting. The decoder reconciliation code,
raw cursor journals and lazy/mmap utilities are valuable starting components.

These contracts substantially reduce the cost of diagnosing failures. They should survive the
migration, with the dependency-availability weakness corrected. Their implementation is not beyond
criticism, and “world-class” is not a verified property. In particular, a passed temporal audit
checks the invariants it implements, not every source of hindsight or selection bias.

Both reviewers reach the correct preservation conclusion by different routes: High inspects
specific local mechanisms; 5.5 emphasizes service boundaries and data production. Both correctly
reject equating raw counts with examples, weights hashes with generations, and paper results with
execution edge. Their agreement is corroboration only where code/receipts support it; it is not
two independent measurements of the project's success.

## 6. Direction for a genuinely large Brain

### First boundary: a persistent market and experience product

Define a versioned market universe and its selection mechanism, then maintain live and historical
coverage as durable work. Every range needs separate states for requested, attempted, captured,
decoded, semantically qualified, feature-ready, label-ready, unsupported and unresolved. Failed
reservations retain their charge; retry/reconciliation policies retain the missing work. Partial
failure should quarantine the affected partition rather than strand a market indefinitely.

Use exact raw objects plus versioned canonical events and derived columnar partitions. A snapshot
should reference immutable partitions and transformations rather than copy the entire data store.
Keep local transactional metadata where it works; design worker ownership, leases, fencing,
idempotent commits and backpressure before distributing writes. Provider/account budgets must be
shared and actual limits verified. No acquisition quota is inferred from the reported paid plan.

The next source milestone should qualify one complete raw-to-experience path with honest quote
units, conversion dependencies, transaction ordering, token/fee semantics and lifecycle coverage.
It need not wait for every venue. Keep the existing bar feed as a baseline and cross-check where
appropriate. Do not turn “one successful PumpSwap decoder” into a universal Solana adapter.

### Second boundary: an experience definition rich enough to support a larger model

A training example should have an origin, market/entity identities, complete input dependencies,
knowledge time, observation validity, task/horizon labels and explicit censoring. Retain events
and multiple time resolutions so new representations can be derived without reacquisition.
Separate data safe for auxiliary learning from data qualified for execution claims.

Develop market context, liquidity/flow and lifecycle representations alongside a simple OHLCV
baseline. Use longer context and horizon-dependent responses where the task requires them.
Multi-task forecasting of returns, volatility, future liquidity/availability or other observed
market outcomes can use more retained evidence than the current two-label gate. Representation
pretraining is a plausible research direction, not established alpha. Holder/deployer features
likewise require causal historical state and independent usefulness evidence, not assumptions
about where “the real alpha lives.”

This design work should start with the data service. Waiting until millions of identical
OHLCV examples exist before asking what information Brain needs would preserve the current trap.

### Third boundary: scalable consumption and a scientific learning contract

Replace the eager B13 path with partitioned membership, streaming transforms/batches and explicit
sampling objectives. Distinguish intended population weights, draw probabilities and any loss
correction. Importance weighting is an option with support/variance requirements, not an automatic
fix. Declare replay of older regimes versus recent adaptation instead of silently changing it as
pools arrive. Fit budgets must scale with measured unique exposure and convergence needs.

Make model width/context/head configuration real recipe parameters: the current curriculum fit
constructs width 16 and global `CONTEXT` regardless of the width/context entries passed in its
config. Support accelerator jobs, resumable data position/RNG/optimizer state and numerical
qualification. Keep tiny deterministic fits as plumbing controls while permitting substantially
larger representation and capacity trials. There is no scientific reason to decree 10k–100k
parameters for V2 or one million for V3 in advance.

The most informative next scientific program, after adequate development support exists, is a
**data × compute × representation/capacity study on common declared populations**. First establish
a stable optimizer control, then compare nested data support, adequate updates and a small number
of justified model/representation alternatives. Include the frozen champion, refitted empirical
and linear baselines. Measure unique exposure, train/development loss, conditional calibration,
transfer, latency and cost. An LR × clipping diagnostic is useful within this work; it should not
be the strategic milestone, and does not require waiting for every data-system migration.

### Fourth boundary: independent evaluation and economic relevance

Populate broad development, separate calibration and prospectively enrolled future evaluation
while building the data service. Report known-market temporal performance and cold-mint transfer
separately, with related pools grouped by mint and market-time dependence acknowledged. Define
AUTO traffic and Watch sessions explicitly; publish unsupported, censored and abstained fractions
against the original enrollment, not just successful predictions. Future windows must be fixed
before outcomes. Repeated generations consume evaluation information even when individual fits
use validation-only selection.

Use precision and effect-size requirements to choose support, rather than declaring seven days
or 1,000 windows universally sufficient. Fit and test calibration of the final deployed ensemble;
averaging individually reasonable quantiles does not establish calibration of their average.
Require useful conditional discrimination as well as calibration. A constant distribution can be
well calibrated and useless for selecting trades.

Specify a policy-aligned target/decision interface with latency, horizon, costs, liquidity,
failure and abstention semantics. Shadow decisions and later outcomes can validate it without
real-money execution. The existing paper accounting remains useful but cannot substitute for
market/execution qualification. Profitability is a later empirical question, never a consequence
of larger training runs.

### Generations and infrastructure

**V1 should establish the complete accumulation loop:** sustained qualified production, a growing
partitioned corpus, demonstrated consumption beyond the current retained cap, reliable bounded
recovery, broad independent evaluation and a learned candidate with a defensible measured
capability gain. Million-example capacity is a sensible engineering target; the generation claim
must also report unique support, exposure, quality and uncertainty. If the candidate cannot beat
appropriate simple baselines, call the data platform successful and learning unproven.

**V2 should establish useful scaling:** millions to tens of millions of qualified examples across
more lifecycles and regimes; larger/longer accelerator fits; evidence that additional data and
compute improve transfer or another declared capability; and measured retention of earlier
capabilities. Parameters, hardware and run duration are measured axes, not arbitrary release gates.

**V3 should establish distributed scale and robust usefulness:** partition workers and storage that
survive retries/failures without losing coverage or provenance, accelerator training justified by
measured throughput and learning curves, and repeated independent improvements on relevant
decisions. Use multiple GPUs for parallel studies or training according to the measured workload;
tens of millions of short examples do not by themselves require model parallelism or a data center.
Real execution would require a separate authorized qualification.

The two Macs can validate these interfaces with a local catalog and immutable partitions. A
cluster then changes storage, task ownership and compute capacity behind explicit contracts.
Replacing four local mechanisms alone, as High suggests, does not make a hundred-node service:
recovery, fencing, publication, schema evolution, resource isolation and observability must also
work under distributed failure.

**The strategic acceptance criterion is an unbroken, measured chain from additional market
evidence to additional useful learned capability.** Today several links are implemented only as
bounded experiments, and some are missing. Preserve the verifiable evidence; replace the bounded
learning envelope and complete the semantic, representation and evaluation connections. That is
the change required for the stated Brain ambition.
