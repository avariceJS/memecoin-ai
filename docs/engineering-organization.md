# Brain Engineering Organization

Canonical owner of the engineering control system, separate from the Brain research and production
runtimes. Technical documentation is English; the [Founder Desk](../engineering/OWNER-DESK.md) is
Russian. Brain architecture and scientific validity remain owned by `brain-development.md`,
`brain-source-architecture.md`, `training-evaluation.md`, and `contracts.md`.

## Independent director audit — 2026-10-20

**Verdict: retain the core architecture; reject operational release and unattended operation.**
The organization has substantial tested coordination and isolation primitives. It has not completed
its first real, independently accepted Brain task through the deployed pipeline. Component source
approval, integrated system acceptance, and operational authority remain separate.

Audited source: `b2dcd6dc58a8e1ab09b15da8fa0d1bba5f3ad0d0`, `org/development`.
The audit changed documentation and evidence only. At that time it did not repair controller source,
resume a Goal, dispatch a worker, install/start a service, clear pilot STOP, run training, access
protected datasets, request market data, or authorize publication. The founder later completed the
temporary audit hold and explicitly resumed engineering source development; see the current authority
section below. The audit findings and release gates remain relevant, but the hold is not a restart
authorization gate.

The [audit receipt](evidence/brain-org-independent-audit-2026-10-20.json) binds source identities,
local reproductions, test results and limits. Private scripts and raw results are under
`/Users/root1/Developer/brain-org-state/audit-20261020-independent/`. Original consolidation custody
and pilot state remain intact. The [handoff](../engineering/SOL-MANDATE.md) owns the resumption entry.

### Founder resumption and non-blocking technical program — 2026-10-20, reaffirmed 2026-10-21

The founder subsequently authorized autonomous **engineering development**. This does not authorize
operational release, original pilot dispatch, removal of STOP, market collection, training, AUTO,
trading, protected-data access, production or `main` changes, paid resources, or automatic Astra.
The sole implementation owner is successor Goal/thread
`01a15b1c-c263-71b0-bfca-c5b46d17a89b` (`gpt-6-luna/high`). On 2026-10-21 the founder explicitly
confirmed that the temporary audit hold is complete and autonomous source work continues under this
same Goal. Current native readback confirms this same Goal is `active`; preserve its identity,
history, counters and durable state, and do not create a replacement or competing owner. The earlier
`paused` readback was a prior state, not a current control-plane limitation. This source authority
does not grant operational release or runtime authority.
Predecessor Goal/thread
`01a15a6e-0e03-77d2-b3ce-d81b95b5a89f` remains blocked and preserved after repeated A03 research;
the native Goal API cannot resume a blocked Goal. The founder authorized exactly one successor because
of that API limit. Preserve the predecessor's counters, checkpoints, prior decisions and all six pilot
failures. The Astra audit thread has no Goal. Continue bounded A05/A10/A04 source development and
verification under the resumption limits; do not create another Goal, coordinator or worker team. The
successor Goal is recorded in the external resumption handoff.

Independent Sol technical lead `01a15a5e-7fc3-7bf0-9a10-73199636bfcd`
(`gpt-6.1-sol/high`) verified the current checkout, selected private evidence hashes, pilot STOP,
configuration, manifest and SQLite against the audit. It made no source or pilot changes. Its
follow-up established that A03 is an external Codex-interface dependency at pinned v0.163.0: stable
public progress snapshots and successful turn completion cannot prove complete per-dispatch
settlement; internal raw events and token records also fail completeness or public binding. Do not
repeat source probes without a new interface or source change. Keep reservations for unknown, late or
ambiguously attributed usage. Any future bounded operation must limit dispatch count, duration,
concurrency and unresolved-dispatch count without describing those limits as provider spend caps. Do
not auto-resume or fall back to paid/internal interfaces. Full release stays closed until a supported,
versioned evidence contract binds complete terminal outcomes (including incomplete, failed and
cancelled responses) to response, dispatch, thread/turn, model and attempt epoch, exposes loss and
late-correction semantics, and provides a complete denominator. An absolute spend cap additionally
requires provider-side enforcement. The current live-dispatch allowance remains zero under the
founder's runtime STOP. A future separately authorized bounded mode must reserve before send, allow at
most one active dispatch and one unresolved-dispatch slot, preserve an unresolved reservation across
turn completion/restart, and stop further dispatches on unknown or ambiguous settlement. This bounds
the number of exposures; it does not cap the charge of one request. Local source work and synthetic
checks continue while live inference stays disabled.

At `org/development` HEAD `07d2006`, deployment validation previously parsed `config.json` and
verified its digest through separate reads. It now captures UTF-8 bytes once, parses and hashes that
same snapshot, and rejects a digest replacement observed after capture. Sol accepted the exact
source/test diff `9113f9f41660bd052b30257216c17d959184ba3906b9ca04f19aa2f311353390`; the full
engineering release check passed Ruff, format, 527 tests and 112 subtests. This closes the split-read
witness only: later file changes, path races, the separately read digest file, and shared
SQLite/config-generation authority remain outside scope. It does not qualify deployment, runtime,
release or A10 completion.

Against `org/development` base `702819a`, A10 review freshness now compares canonical JSON for
source-contract, producer-task and participant-task snapshots. Type-only drift such as `1` to `true`
blocks dispatch and saved-response finalization while preserving the response and withholding ACK;
key ordering and whitespace remain immaterial. Sol accepted exact diff
`ef3120dd01547830b844e7547365b2bce7d12d348581b0ecb257470b480c8701`. The full engineering release
check passed Ruff, format, 533 tests and 112 subtests. Malformed JSON syntax in task fields still
failed earlier in `_task()` at that checkpoint. The follow-up below normalizes task decoding errors
at the review-freshness boundary. General database tamper detection, post-ACK freshness, A03 and
operational acceptance remain open.

Against `org/development` base `e5773be`, A10 review freshness now maps malformed JSON and invalid
UTF-8 in producer/participant task snapshots to the existing stale-snapshot blockers. Dispatch
rejects before journal mutation; saved-response replay records a blocked disposition, preserves the
custodied response and withholds that participant ACK, then continues to unrelated requests. Sol
accepted scoped source/test diff `2e26d89872276415c60bcf0d2814cbfc2759740be6d16cb6832aa611d0118c4f`.
The contract-transition module passed 103 tests; `make engineering-release-check` passed Ruff,
format, 542 tests and 116 subtests. The change catches only task JSON decoding/data errors; missing
tasks retain `StateError`, SQLite errors propagate, and broader database tamper detection, A03 and
operational acceptance remain open.

Against `org/development` base `a0ae4dc`, A04 now strictly decodes captured stdout for every bound
`acceptance_json` command after process-group cleanup. Invalid UTF-8 raises `ConfinementError` before
result validation; legacy and `exit_zero` output plus stderr keep replacement decoding. Sol accepted
exact four-file diff `1308d5b8917912f0e3c800c822aca3977a5ad90f46a895697c27160e26623677`. Focused tests
passed: 64 supervisor tests/10 subtests and 3 decode tests/4 subtests; Ruff and format passed. This
closes silent replacement of malformed proof bytes only; output provenance, same-inode execution,
loader closure, sandbox qualification and operational acceptance remain open.

Treat A03 as a tracked external release dependency, not a company-wide engineering stop. Continue
independent source work while that interface is unavailable. Its ranked program was updated after
source review:

1. **Keep A03 fail-closed and open the upstream dependency.** Luna owns a bounded safe architecture
   and records the exact supported-interface contract in the Founder Desk. Resume A03 implementation
   only when upstream exposes a versioned complete source or a new qualified provider interface is
   separately authorized. Unknown usage and its reservations remain unresolved.
2. **Build useful source work in parallel.** A04 binds validated tasks to controller-owned verifier
   policy in the same immutable admission decision; execution, verification and recovery resolve that
   exact binding. Acceptance: a synthetic off-catalog task reaches verification without config-task
   lookup; proposals cannot supply executable fields; policy, artifact, task, decision-version and
   attempt-epoch drift fail before acceptance; production admission remains disabled by default. A11
   supplies exact candidate identity, a complete review packet and a receipt; oversized diffs require
   verified pagination or an explicit block, and moved integration tips require a new review. A05
   implements a passive deterministic lifecycle state machine with one lease owner, durable wait
   reasons/backoff and recovery obligations; synthetic crash, quota, STOP and unresolved A03 cases do
   not busy-loop or dispatch. A10 adds idempotent producer/consumer acknowledgements and explicit
   contract transitions; stale or mismatched acknowledgements cannot activate a contract, and blocked
   dependencies do not suppress unrelated ready work. A12 adds bounded active projections and indexed
   dependency lookup without deleting history. Each workstream gets independent exact-candidate review
   and source checks; none enables a service or pilot.
3. **Continue STOP-preserving source and restore work:** ORG-A01/A02/A06/A07/A08/A09, with the
   deterministic-controller circuit breaker required before any task replay. Fix real bootstrap
   to pass real preflight, use versioned non-destructive ledger initialization, settle only qualified
   usage, stop active permits within a stated overshoot bound on fresh zero capacity, and include all
   engineering checks in one explicit local release command. Dynamic task admission and unattended
   continuation remain disabled in this first candidate. Luna is the sole implementation owner;
   Sol reviews the exact candidate.
4. **Checkpoint and release acceptance.** Before changing the historical pilot, prove a complete
   checkpoint and fresh-path restore, including STOP/reasons, policy/config/native binding, both
   databases, Git refs/objects, intents, attempts, receipts and recovery records. Verify checksum-bound
   manifest, STOP-preserving upgrade/rollback and rejection of incomplete snapshots. This is source
   and release evidence only; original pilot dispatch still needs separate founder authorization.
5. **First useful pilot cycle, only after that authorization.** Preserve all six failures and use the
   same pilot database. Independently accept/integrate EXP-001, CTX-001 and PLAT-001 through the
   released controller; prove native model identity, final JSON, brokered tool execution, usage
   settlement, exact merged-candidate review and one-time publication. A deterministic parser or
   controller fault must create a bounded recovery obligation/circuit break, not burn implementation
   retries. Then close ORG-A04 with one useful task outside the fixed catalogue through the same
   immutable trusted execution/verifier binding.
6. **Lifecycle and endurance.** Close ORG-A05/A06 and remaining A09 before
   unattended continuation. Then close A10-A12 with durable department plans/contracts, full review
   packets and bounded history projections. Qualify reboot/storage-loss separately and only then
   run 24 hours, 72 hours and seven days with useful work across at least two independent streams.
   Measure accepted throughput, idle causes, recovery latency, unknown usage and disk growth; expand
   concurrency only when measured accepted throughput improves.

The first A05 source increment is now in `org/development` at
`91c6cfaf85aaebf62f6bd36290d7fcaf27a1d2b6`. It is a pure deterministic lifecycle planner: STOP,
runtime, usage settlement, quota, process, lease, recovery and backoff observations fail closed; its
only start outcome is an inert owner/epoch-bound request. Stale owner metadata blocks launch. Sol
accepted these exact source bytes and the local engineering gate passed 274 tests and 42 subtests
with Ruff on 49 files. At that checkpoint durable fencing, persistence, request
consumption/deduplication, retry budgets, scheduler/process integration, and operational lifecycle
recovery remained open. A05 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-passive-lifecycle-scaffold-review-20261020.json`
(SHA-256 `87596c56a7e60b86a9ab85a70a2e03b15ab9ac613f2e9b8539a7fa2eafc88d97`).

On 2026-10-21 a bounded durable-fencing increment merged into `org/development` at
`55fa10b354317c2bbb6c304cc3602fbf83688497` (exact reviewed source tip
`10e127aae103a07dbb9f89dca35631f72f7d7a73`). Store now persists one exclusive lifecycle owner with
a monotonic epoch and lease, and records inert plans/requests as append-only decisions. Owner history
must be contiguous; request identity is immutable, retries are type-sensitive and idempotent, and
the generic decision writer cannot mutate lifecycle namespaces. Sol accepted this exact source for
bounded scope; the pinned engineering gate passed 298 tests, 45 subtests, and Ruff on 51 files. This
adds no request consumer, cross-epoch deduplication, retry policy, scheduler/process/service wiring,
or operational recovery. Full A05 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-fencing-review-20261021.json`
(SHA-256 `2052d58cf12bbc3a37d56b7804e8e42e26e6cd7b7fb34b685cf02e318d1f2af4`).

An A05 observation-binding increment merged at
`db04f23468744f736622ad5ea33f13d0166c4446`. Launch-request persistence now recomputes the
deterministic lifecycle plan inside the active owner-fenced transaction from an exact, detached
caller-supplied observation object and stores those observations beside the plan.
The legacy plan-only API rejects `launch_request`; non-launch legacy plans remain accepted. Sol
accepted this bounded source increment; Ruff passed on 56 files and the engineering gate passed
337 tests and 73 subtests. The receipt makes the claimed decision inputs reproducible, but does not
prove observation truth, freshness, completeness, trusted sensor origin, or current launch authority.
No request consumer, current-gate reconciliation, cross-epoch deduplication, retry policy,
scheduler/process/service wiring, or operational recovery was added. Full A05 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-observation-binding-review-20261021.json`.

A05 consumer design remains blocked at the authority boundary. The durable lifecycle owner epoch is a
replaceable lease fence, not a launch authorization generation, and `launch_authorized` is currently
caller-supplied observation data. No controller-owned stable authorization-generation source exists
in this Store contract. A consumer must not mint or promote that authority; it remains unimplemented
until a separately owned generation interface can be validated. A future consumer may persist only a
passive disposition, with process/service action qualified separately.

At `6691a2f`, Store adds a bounded read-only `lifecycle_request_worklist`. It reads immutable requests
and current owner lease state in one SQLite snapshot, validates exact request identity/history, and
recomputes observation-backed plans. Results are advisory and grant no dispatch or consumption
authority; `owner_current` is only a point-in-time lease match. Sol accepted the exact diff
`7d0b176f1792d6571447be4dbb175c6e0a6445abe30948edbee7970aa2a2e21b`; focused tests passed 17/3
subtests and the full engineering gate passed 408/96 subtests with Ruff. Limit 1..100 bounds returned
request count, not history scan or retained observation bytes. Legacy non-launch records without
observations cannot be replanned; recomputation proves consistency, not sensor truth or freshness.
The independent authority review found no existing controller-owned generation contract, so no A05
consumer or cross-epoch disposition was added. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-projection-review-20261021.json`
(SHA-256 `800f6620cacf320ac36bf19bf7d362d2a3c250f56ef02f5c23daa284aa1ce006`).

At `aebb77c`, a second-connection regression establishes the projection read snapshot by completing
its request query, pauses before owner-history reads, commits owner release on another Store, and then
resumes the projection. It must return the prior active/live owner and matching request while a fresh
read sees `released`. Sol accepted the exact one-test diff
`f67f6149f6e09c034d5ff4bbfe8923b52509c8a9221f612e57d5982a6729b321`. Focused test passed; the final
full gate passed 409 tests/96 subtests with Ruff. One earlier full run had a transient unrelated code
identity test failure; that test passed in isolation and the subsequent full run passed. This is one
synthetic interleaving, not exhaustive concurrency proof. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-projection-snapshot-review-20261021.json`
(SHA-256 `21997923339dfd33ef6e5aceeaa51caa2bcb8e35417448cc3f736ad32bb66a84`).

At `763fd5a`, lifecycle request persistence now checks the Store-owned controller-parse circuit
and recovery-obligation histories inside the current owner-fenced `BEGIN IMMEDIATE` transaction.
Paired histories must be contiguous, version-matched and alternate valid `open`/`resolved` records;
an open latest cycle or malformed history rejects an otherwise optimistic `launch_request`. Tests
exercise the real fault producer committing while evaluation waits on a SQLite writer lock, two
resolved recovery cycles, and malformed obligation identity. Sol accepted exact source/test diff
`25208d1da11c3058413cfa31b074ee0539fe8eb666e6e8f175520d7dc1532668`. Focused tests passed 22/3
subtests. The first full gate had one known intermittent macOS process-group cleanup `PermissionError`
(`killpg`) in the existing code-identity test; isolated rerun passed, and the final engineering gate
passed 415 tests/96 subtests with Ruff. This veto covers recorded controller-parse recovery only;
history validation cost scales with retained cycles, references are not independently authenticated,
absence means only no recorded obligation, and other observations remain caller supplied. It adds no
consumer, authorization generation, execution authority or operational A05 closure. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-controller-parse-recovery-veto-20261021.json`
(SHA-256 `53d5076ee97375fb3c025cc222f70eb5179017adc684865329ea4fbc46fe9b09`).

At `53120b6`, that veto also validates the SQLite decision journal links before hydrating controller
parse recovery bodies: version 1 must be a root, and later versions must point to the same journal's
immediately preceding version. Four regressions corrupt root, dangling, and cross-journal links;
each rejects the optimistic request without adding events or request decisions, while healthy
resolved cycles still permit an inert request. Sol-6.1 High accepted exact source/test diff
`9d7c1b5a3f67157620b60b0bcd3ab603b094bd4c6eee271a36b2d11ecf0b1934`; `make
engineering-release-check` passed Ruff, 504 tests and 105 subtests. This remains a check over the
recorded controller-parse obligation only; no A05 consumer, authorization generation, execution
authority or operational closure was added.

The lifecycle request writer now rejects serialized request bodies above 64 KiB before journal
insertion. The read-only worklist preflights each selected body's SQLite BLOB length and singleton
history in the same read snapshot, then rejects a record above 64 KiB or an aggregate above 1 MiB
before JSON hydration. Byte-limit failures return no partial projection and mutate no state. Sol
accepted the exact source diff
`d940d17234a3f8d55fb3ede3d5f1fef2a48cf98428c0de1626cea95554e9ca37`; focused tests passed 25
tests/3 subtests. The first full engineering gate had one existing intermittent sandbox code-identity
pin mismatch; its isolated rerun passed, and the final gate passed 426 tests/96 subtests with Ruff.
Historical oversized requests remain stored but make the whole worklist fail closed; valid records can
still exceed the aggregate budget, and no pagination exists. Input detachment/planning, owner history
hydration and SQLite scan/cast costs are not bounded by these request-byte limits. This adds no
consumer, controller-owned authorization generation or runtime qualification. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-byte-bounds-review-20261021.json`
(SHA-256 `618b68932fbed90c9cc650cf04c5dae60578eb58261502e798d6842439cc9058`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-byte-bounds-gate-20261021.json`
(SHA-256 `c08221ea42077fa90ebda351c096a279a5e2483ffecf8725bd94cac8586391ec`).

At `9bb0fb5`, lifecycle request worklist discovery replaces grouped namespace enumeration with
successive primary-key name seeks, capped at `limit + 1`. It preserves the exact BINARY namespace,
including the malformed bare prefix for fail-closed identity validation, and keeps discovery,
owner-state reads and request checks in one snapshot. Sol-6.1 High accepted the exact source diff
`81ac117dcad0e9df466df1856a3be4bebc6818ab8b4c42853f9ddfcb5af88531`; the focused lifecycle Store
suite passed 30 tests/3 subtests and the engineering release gate passed 449 tests/101 subtests with
Ruff. The seek count is bounded, but B-tree work, wall time, owner-history aggregation, historical
name length and total memory are not hard bounded. SQL planner/opcode assertions are SQLite-version
sensitive. No lifecycle consumer, controller-owned authorization generation or runtime qualification
was added. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-indexed-seek-review-20261021.json`
(SHA-256 `ec72170d340328e037f5cfb7bf871a53096af69c2376fcde0efe7c60debd582f`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-indexed-seek-gate-20261021.json`
(SHA-256 `a79d78682143cbef0bb80af18476bc9bed5bbfeb56b09df6040bb20193bba119`).

A10 now also has a bounded readiness-report scaffold at
`57f496f5f74258d00a09dc37b5818e409495dffc`. Its read-only SQLite snapshot exposes each task's
non-integrated or missing dependency and mismatched contract version without hiding unrelated ready
work. The transactional claim path remains the authority and rechecks readiness. Sol accepted this
exact source; the local gate passed 275 tests and 42 subtests with Ruff on 49 files. The follow-up
transition increment below closes durable producer/consumer acknowledgements and the current
schema-validation v2 transition only. Dependency obligations and broad A10 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-readiness-report-scaffold-review-20261020.json`
(SHA-256 `22037f53771b100bc1694f5f9133eec59ceeef519888389ff98f42d03af62176`).

On 2026-10-21, an A10 transition increment merged into `org/development` at
`ef953d5d95e0fb9bef50477957fcbd8f9fc90ed6` (exact Sol-reviewed source `0879b5efed1cf0942d080e02624da13fd5a9aede`).
Initial contract publication remains version 1; each later version now requires one explicit
content-hashed transition, immutable producer and consumer acknowledgements, an integrated producer
task, and atomic activation with its receipt. Generic decision writes cannot alter these records,
and supervisor lead acknowledgements and activation resolve the exact configured proposal. The
legacy exact schema-validation v2 remains readable without retroactive evidence. Sol accepted the
exact source for bounded scope; the merged engineering gate passed 312 tests, 45 subtests and Ruff on
52 files. This does not generalize department contracts, conflict adjudication, or department
plans/leases. Full A10 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-durable-contract-transition-review-20261021.json`
(SHA-256 `d94296b79abae78b7d22cce4348b750a2123046dbb860250ff13407490e9424e`).

At `dc6f7ba`, executive snapshots add a passive bounded projection of missing participant reviews,
independent of runnable consumer tasks. It lists exact contract name/version/hash and participant;
it creates no acknowledgement. Published transitions revalidate the activation receipt and all
historical acknowledgements. Sol accepted exact diff
`b6a173748f99b5163466a1a3ca41878cf3a267a8484bb58d1eb8c3e0e4485849`; Ruff and the engineering
gate passed 372 tests and 96 subtests. Proposal traversal is capped at 1,000 records and 1,000
participants, with at most 99 consumers per proposal. This exposes missing reviews but does not
dispatch them or resolve task-readiness deadlock; payload-byte and execution-time bounds, actual
controller review/ack dispatch, and full A10 remain open. Contract-transition decision reads also
require the singleton row's `replaces` and `replaces_version` fields to be `NULL`; malformed links
fail closed before activation. Focused regression:
`test_ack_root_link_metadata_corruption_blocks_activation`.
Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-lane-review-20261021.json`.

At `221e90e`, the Store adds an immutable task-independent contract-review journal. Each request
binds the transition, participant, explicit evidence revision, pinned source commit, old/proposed
contract, producer task and a bounded snapshot of participant tasks. Packets cap at 64 KiB; one-shot
dispatch intent, thread identity and bounded terminal results are persisted. Approval atomically
records the exact participant acknowledgement only while the proposal, source version, producer
snapshot and participant task inventory remain current. Stale or competing reviews are retained as
blocked evidence, and retry requires a new explicit evidence revision. Sol accepted exact diff
`37c20f79d5eaee17b553dda9b55e8883f832d4e76dbbc8f29c8eee2bfbc94002`; Ruff and the engineering
gate passed 383 tests and 96 subtests. This Store API is not wired to supervisor dispatch: it does
not perform or authenticate a review, verify evidence artifact contents or provider usage, or
resolve the task-readiness deadlock. Controller dispatch/recovery, full A10 and operational
acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-independent-contract-review-journal-review-20261021.json`.

At `a98744a`, SQLite quiescence revisions also advance for insert, update or deletion of
`contract-transition/`, `contract-ack/` and `contract-review/` decisions. A second-connection
lock-wait regression confirms that a review decision committed while a handoff waits makes the
older handoff stale. Sol accepted the exact diff
`f778bb096e60655e2ed9106b419c7e936f3094c4a900c934f0da0be720517960`; 38 Store tests and Ruff
check/format passed. This closes the concurrent-write freshness gap only: an already-pending review
does not yet defer quiescence, and review dispatch/recovery, A03 settlement, full A10 and operational
acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-quiescence-review-20261021.json`.

At `4302b47`, the review journal adds a `response_recorded` state. It stores bounded exact structured
review-result bytes in the append-only Store before parsing; recovery reopens and rehashes those
bytes, and finalization requires the parsed result to match them before an acknowledgement can be
written. Malformed output remains durable and non-sendable. Legacy terminal records remain readable,
but do not gain raw-response custody retroactively. Sol accepted exact diff
`5fee1397e07c518d5d047a182c38e7f2f9770543330b9ecc3cca9bca30115957`; focused tests passed 93 tests
and 9 subtests, and Ruff check/format passed. This is result-byte custody, not the full RPC
transcript; provider-turn and usage-ledger provenance remain caller assertions. No supervisor dispatch
or recovery path is wired, and A03 settlement, full A10 and operational acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-response-custody-review-20261021.json`.

At `58a95c5`, quiescence snapshots include the bounded pending participant-review projection from the
same SQLite read snapshot. When quota permits work, the supervisor records
`contract-reviews-pending` for Sol instead of misclassifying an open review as blocked dependencies
or an exhausted catalogue; quota-unavailable retains precedence. Projection matching uses the same
case-sensitive namespace semantics as decision reservation and revision triggers. Sol accepted exact
diff `20d1ab3b352574fc7faa20a4c8eb572fe6484d3577070de5aad3e11c23fafea1`; the full engineering gate
passed 392 tests and 96 subtests with Ruff. This makes pending review work visible in the inert
handoff path; it does not dispatch or recover reviews. A03 settlement, full A10 and operational
acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-aware-quiescence-review-20261021.json`.

At `86550d9`, `Store.replay_contract_review_response` provides a local recovery path for an already
custodied response. It requires the exact current packet hash, retrieves and rehashes the saved bytes,
strictly parses UTF-8 JSON (rejecting duplicate keys at every depth, non-object roots and non-finite
values), then reuses transactional finalization to recheck durable state and write the acknowledgement.
Stale or malformed responses remain unacknowledged; repeated exact replay is idempotent. Sol accepted
the exact diff `4e2fc44f80b3730d8437142c48eac30e090fd5eb3e872551e37734316c2758ae`; the focused suite
passed 37 tests and the full engineering gate passed 401 tests/96 subtests with Ruff. No provider
dispatch was added; full RPC custody, provider-turn/usage provenance, A03 settlement, full A10 and
operational acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-local-replay-review-20261021.json`
(SHA-256 `1ef4ae5e8c1db0178c7bd4fd0869311eab52248da71db9dd0608e9067b4f6b15`).

At `d298bfb`, a cross-connection lock-wait regression calls the local replay API while another
connection holds `BEGIN IMMEDIATE`, then changes participant inventory before releasing the lock.
Finalization returns a terminal blocked review, preserves the response hash, and writes no participant
acknowledgement. Sol accepted the exact test diff `5453fe0f828008e785ccd262f1ded02f297236ed72bea2ce6e7f8e523b16c4eb`;
the focused regression passed and the engineering gate passed 402 tests/96 subtests with Ruff. This is
one synthetic contention interleaving, not exhaustive concurrency proof. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-lockwait-review-20261021.json`
(SHA-256 `f5bcdef0fbec6b60a291408633c3e052d6a60d114f4f0614c8993b1b7aa90a5f`).

At `0c32290`, `Store.replay_pending_contract_review_responses` replays only `response_recorded`
entries from a coherent worklist snapshot capped at 100. Overflow returns no partial identities and
performs no replay. Each request uses the existing custody check and transactional finalizer; a
`StateError` is reported per request with bounded text while later requests continue. Prepared and
terminal entries are skipped. The result asserts no dispatch authority, and no provider dispatch or
resend is added. Sol accepted the exact diff
`bd4758b574333de217989fba40eb880b00e269dc87fa39148919b2c4aaa4dcbd`; focused tests passed 2, and
the engineering gate passed 404 tests/96 subtests with Ruff. The sweep is not atomic across requests;
worklist integrity errors abort before writes, while other unexpected exceptions may stop it after
prior commits. `evidence_revision` remains opaque: there is no newest-revision selection or conflict
adjudication; existing unique participant ACK semantics apply. Provider provenance, A03 settlement,
broad A10 and operational acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-sweep-review-20261021.json`
(SHA-256 `3e776872f8c928c0b265c931caa5721510c29f0545064364761ba41c42c6e90f`).

A follow-up replay-capacity increment adds a dedicated coherent inventory that validates review
journals and applies the maximum-100 bound only to their latest `response_recorded` state. Retained
prepared, in-flight and terminal requests no longer consume replay capacity; `count` is the number of
replay candidates and `skipped_count` is the number of other valid journals. More than the candidate
limit returns unknown counts and no partial replay. The public advisory worklist is unchanged. Sol
accepted exact diff `2313b4b89f305dc4286fd8edf1a18f060a74a5fa366137a1e463f543c3e5f833`; the full
engineering gate passed 411 tests and 96 subtests with Ruff. Discovery still scans retained review
histories, skipped journals do not receive full packet/currentness audit, and replay is not atomic
across requests. No provider dispatch or new execution authority was added. A03 settlement, broad
A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-capacity-review-20261021.json`
(SHA-256 `09b11e458a1465fbf00a95d79b7e223dd5496fdf1a886e66ab08d8b4d0ff621a`). Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-capacity-gate-20261021.json`
(SHA-256 `2c1ff6a3a57720680ab528c1b04eeb3c9e2a332e026b4150c05e4fcb3cc7c375`).

At `7a1aaba`, `supervisor.activate_contract` invokes transition-scoped local replay for each exact
configured proposal before the existing activation check. This lets already custodied approvals
complete their existing transactional acknowledgement and activation path without another provider
request. Each transition's 100-response overflow is isolated so later configured transitions still
reconcile. Sol accepted the exact source diff `5f1ea425dc8c6d1c2e2152fd9219584c35a1ef2d63bd64794f03d1b5f7fafc05`;
the focused transition suite passed 44 tests and the full engineering gate passed 423 tests/96
subtests with Ruff. The supervisor fairness regression covers 101 responses for one transition
followed by one response for another. Discovery still scans all retained histories, unrelated
malformed history can abort later scopes, replay and activation are separate transactions, and
repeated deterministic failures may recur on later passes. Provider/usage provenance, A03 settlement,
broad A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-supervisor-replay-review-20261021.json`
(SHA-256 `5c316756f565c4dc94d9f608115e840fd6f7558e5069c440815c4f22afafe49e`);
gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-supervisor-replay-gate-20261021.json`
(SHA-256 `96150e6162c14aa6b269d7dedfc7c1206be32ac378bb731cc09d4a270053689f`).

At `8fb3840`, replay integrity `StateError` is isolated to its configured transition: the supervisor
records a bounded `contract_review_replay_failed` event with dispatch authority and provider dispatch
false, then still runs the existing strict activation check and proceeds to later transitions. The
activation transaction independently requires the immutable proposal, integrated producer and exact
participant ACKs, so a malformed replay journal cannot supply an ACK. Sol accepted the exact source
diff `fa1e9d5a436484a2f898fe1208d5bc8f993f24d6e8c9a9eee3d15352ed9bbaea`; the focused transition
suite passed 45 tests and the full engineering gate passed 427 tests/96 subtests with Ruff. The
regression proves the affected transition stays at v1 while a later fully acknowledged transition
activates. Retained-history scans, separate replay/activation transactions, provider/usage provenance,
A03 settlement, broad A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-integrity-fairness-review-20261021.json`
(SHA-256 `59a1d68d9e789577f6365296033caf63faf4618e50adcb92ab82e6ee451e2893`). A follow-up reader
now normalizes persisted JSON parse/type/recursion failures to `StateError` before transition-level
isolation. Sol accepted source/test diff `db46fed25037fc8ba200e8de6cf5d1a5a6f09cf038353e1e1a6a0dc65c3532ed`;
the engineering release gate passed Ruff, 498 tests and 101 subtests. A matching corrupted journal
remains preserved and cannot supply an ACK; later configured transitions still reconcile. This does
not normalize SQLite text-decoding failures or qualify provider/runtime authority. Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-integrity-fairness-gate-20261021.json`
(SHA-256 `01d5627489783134a9f09822b59d8069ff9abb17c4021eaa41d3526b2fe1e6f9`).

At `7b56ddd`, replay rejects non-string review statuses before set membership. At `5a77f88`,
`_contract_review_record` also validates the required typed journal header before state traversal,
including packet hash, proposal/contract digests, request identity, participant role, version and
dispatch identity. Missing or malformed header fields now become `StateError`, so bounded/default
readers and supervisor transition replay retain their existing failure isolation. Regressions retain
malformed JSON and status-shape cases, cover missing and wrong-type packet hashes in singleton
worklist/quiescence, and remove or corrupt the packet hash in every version of a matching response
journal to prove a later acknowledged transition still activates without provider use. Sol accepted
the exact two-file source diff `e8ecc8c46639ab24964fadf32b64250f74f307ff23d9e47852a2afae9351ea4d`;
the engineering release gate passed Ruff, 503 tests and 101 subtests. The first full run hit the
existing timing-sensitive macOS code-identity deadline test; its isolated rerun and the final full
gate passed. SQLite text-decoding failures, retained-history cost, separate replay/activation
transactions, provider/usage provenance, A03 and broad A10 remain open.

At `927d19d`, terminal contract-review records with raw-response custody now require canonical
equality among the strict parsed response, `model_result` and `result`. Inconsistent terminal rows
fail closed in readers, finalization retries and local replay; legacy terminal rows without retained
raw responses remain readable. Regressions corrupt each result field alone and together, confirm no
new version/event or ACK, preserve whitespace-padded raw JSON, and prove a later configured
transition still activates without provider use. Sol-6.1 High accepted exact diff
`a3c161d23ba6e22740444260a1c72c47835b8dd838af4d595c730f8587f4d76d`; the engineering release gate
passed Ruff, 507 tests and 105 subtests. This validates stored-result consistency only; provider
provenance, complete terminal-schema validation, SQLite text-decoding failures, A03 and broad A10
remain open.

At `342f6ca`, contract-review history readers project stored bodies and link values as bytes, then
decode them strictly using the SQLite database encoding before validation. Invalid text becomes a
stable `StateError` that preserves transition-level isolation; valid multi-version UTF-16le journals
remain readable, and BLOB-typed links fail closed in both bounded and default readers. Regressions
prove malformed UTF-8 cannot starve a later configured transition, preserve the existing UTF-16le
path, and reject byte-identical BLOB links. Sol-6.1 High accepted exact diff
`28faad4d3d795a45be230f7f27cd1b65404aaa08df5569af6ae323fcc21ed09f`; the full engineering gate
passed Ruff, 509 tests and 105 subtests. This does not bound default-reader hydration, SQLite work,
provider provenance, A03 or broad A10.

The follow-up default contract-review reader query now returns at most six history rows before the
existing five-version integrity check, bounding Python row hydration for malformed overlong histories.
The regression inserts 1,000 versions and confirms the default path issues the limited query without
JSON hydration. Legacy valid body and link sizes, SQLite work, provider provenance, A03 and broad A10
remain unbounded. Sol accepted exact source diff `5f00db5162a448399e4a7116517eb94bc4794b7c601910635c326bec683c5352`.
The focused regression passed. The engineering gate passed Ruff and reported 508 passed/105 subtests,
with one unrelated macOS running-code-identity deadline failure; an isolated rerun hit the same
deadline failure.

At `9e6cd7e`, transition-scoped replay discovers review journals through a partial expression index
on the exact contract name, version, contract hash and proposal digest. It validates each matching
journal's full append-only history, then applies the existing cap only to latest `response_recorded`
requests; unrelated malformed IDs or JSON no longer poison this transition's replay. The public
worklist and global replay sweep remain unchanged. Sol accepted exact diff
`b7fc426eaee98609f53525dd68b7190940976cf4c52676258c1fdbc0d3a31e32`; focused tests passed 46 and
`make engineering-release-check` passed Ruff, 428 tests and 96 subtests. `EXPLAIN QUERY PLAN` used
the new covering index. A first full run hit the existing intermittent macOS running-code-identity
deadline test; it passed in isolation and the final full run passed. Initial index creation and
index storage/write cost grow with retained history; selected-transition discovery and validation
still scale with that transition's retained journals. Separate replay/activation transactions,
provider/usage provenance, A03 settlement, broad A10 and operational acceptance remain open. Review
receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-scoped-replay-index-review-20261021.json`
(SHA-256 `e38766e9046540cffc3a7fce62a88f24626a2ecc0dc6ee12c1a1ccbf087d5718`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-scoped-replay-index-gate-20261021.json`
(SHA-256 `29191edb31bf3cad2e0faa5eed226830a70cf2d0e8e316b876491c642b9f472a`).

In a reviewed candidate based on `dd779db`, a contract-review result that says
`approve` and `migration_required` is now retained as controller-blocked with
`migration_completion_unqualified`; it creates no participant ACK. The exact model result and raw
response custody remain intact, repeated finalization and local replay are idempotent, and a new
explicit evidence revision can create a separate review. This is a prospective restriction on this
review-finalization path: prior terminal approvals and ACKs, direct ACK APIs and lead ACK routing are
unchanged. No qualified migration-completion interface or transition-wide migration hold exists.
Independent source review accepted exact diff
`8825f98081b3053315dcdfeaecde5cd24ded85162290edd94017cfb9b37e01d5`; the
focused transition suite passed 73 tests and the engineering release gate passed Ruff, 469 tests and
101 subtests. A03, broad A10 and operational acceptance remain open. Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-migration-review-fail-closed-gate-20261021.json`
(SHA-256 `e86637bea8e8dd39086eace26d1753f83acbafe48fec0bbb2d673755eafc178c`).

The A10 dependency-obligation increment merged at `5e4d123ece95d6c87c0eb0ced6891d8473bedece`.
`blocked-dependencies` now reconciles duties and task readiness in one fenced SQLite transaction;
its content-addressed snapshot names blocked tasks and exact dependency/contract blockers while
preserving unrelated ready work. Matching active duties are reused, stale duties are cancelled with
an immutable readiness event, and cancellation evidence resolves to that event after later state
changes. The snapshot and obligation detail payloads are bounded. Sol accepted the exact five-file
source diff; Ruff passed and the gate passed 341 tests and 73 subtests. Automatic trigger freshness,
generic contract migration, department plans/leases, conflict adjudication, full A10 and operational
acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-dependency-obligations-review-20261021.json`
(SHA-256 `30a9fac250685cb73ad28db0575b6d75322c196c9f44d43bb122682b3c4c47f4`).

At `org/development` base `c1f1ead`, dependency-duty reconciliation now runs during every
admitted CEO episode instead of only when `trigger == "blocked-dependencies"`. Durable Store
readiness, not the diagnostic episode trigger, determines whether the duty is created, reused or
cancelled. It remains behind STOP and the active-owner check and uses the existing CEO-fenced
`BEGIN IMMEDIATE` reconciliation. The reconciler seeks only the case-sensitive
`blocked-dependencies/` primary-key range, validates every row in that range before writes, and
includes terminal rows when selecting the next content-addressed generation. Unrelated obligation
bodies are not hydrated. The range does not cap retained dependency history or total SQLite work.
Sol accepted the namespace-scoped source diff `40941b9d6e735c79963f03cb2404211f60c4b3f3c840b7a456b2088c20a5fd46`;
the engineering gate passed Ruff, format, 547 tests and 121 subtests. The earlier trigger-freshness
increment was separately accepted at `64ccd7a1b3177b904b767dcfb9c4216ecac7e7da6af68f8072dc0821633f742b`.
Reconciliation happens only when a CEO episode runs, so duties may still lag between episodes; this
adds no service, dispatch or runtime authority. Generic contract migration, broad A10 and
operational acceptance remain open.

Follow-up reserves the dependency-duty ID range from the public generic obligation API. Reconciliation
accepts historical rows only when their initial body, readiness snapshot/hash, generated ID,
transition state and contiguous generation history match the former producer format. An ambiguous
legacy row fails the whole reconciliation before cancellations or creation; it is retained for
separate review. This establishes semantic format compatibility, not proof of which old caller
created an exactly matching record. Sol accepted exact source diff
`f0c585cad833815e1399b32a61a96d2ea8fad5c1842d4369249be03207d2ee07`. Ruff and format passed; the
first full gate hit a transient macOS code-identity observation deadline, the isolated test passed,
and the repeated full gate passed 549 tests and 121 subtests. Dependency history and SQLite work
remain unbounded; STOP and all operational limits remain unchanged.

The A10 quiescence-trigger increment is on `org/development` at `714bb573fc659ba00c849c9b7e0be7c1f470b472`.
Handoff identity now includes the coherent task/readiness snapshot, and the Store rechecks it under
`BEGIN IMMEDIATE` before atomically deduplicating or recording the immutable handoff. Fresh eligible
work defers quiescence. Sol accepted the exact source diff; Ruff passed and the engineering gate
passed 344 tests and 73 subtests. Quota/resource observations remain outside that transaction,
state may change after commit, hashing scales with retained tasks/blockers; the second-connection lock-wait regression was added
in the follow-up below. Generic contract migration, department plans/leases, conflict
adjudication, full A10 and operational acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-trigger-freshness-review-20261021.json`
(SHA-256 `efc80cca51087c30d66414069359e7516eef86d415dbbcb6e4ffc1e699114c87`).

A follow-up regression at `01a4c14` now exercises the cross-connection lock-wait case: a second Store
holds `BEGIN IMMEDIATE`; the handoff writer reaches its write and remains pending; the blocker inserts
new ready work and commits; the stale snapshot is rejected, then the refreshed snapshot is accepted.
Sol accepted the exact one-file diff `03ca0279a63ba9f38f23691e67f69e5fa30f380dfcdc1cec74ce051db2a62965`.
The full gate passed 349 tests and 83 subtests with Ruff on 58 files. The timeout gives practical
contention evidence, not exhaustive scheduling proof. Quota/resource observations remain outside the
same transaction, state can change after commit, and fingerprint cost scales with retained tasks and
blockers. Generic A10 and operational acceptance remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-lockwait-review-20261021.json`
(SHA-256 `e8f16bbb23dcc955185586c72148de3a7bf18633500d8ee001547fb86ef5d254`).

The configured contract activation path is now generic across the controller's contract map: each
transition uses its configured version, producer team/task and consumer set, while Store still
requires the integrated producer and every exact immutable acknowledgement. A not-yet-ready contract
does not block another configured transition. Existing proposal records are revalidated on repeated
activation, and legacy schema-validation v2 remains readable with its original config and hash.
Sol accepted the exact source diff `259867174de4f18c7a0f36bbc66c7301e175cde6e388e599564d13286903f518`;
the engineering gate passed 351 tests, 83 subtests and Ruff. A follow-on at `886a2e2` routes exact
name/version/hash acknowledgements for any configured contract only to its producer/consumer teams,
preserves the legacy schema-validation config/hash and rejects stale source versions. Sol accepted the
exact diff `41b2b8f182a739bdbabb674dc2a7ba7428f59bb24431b551ed83ced8c4629dcc`; the gate passed 354
tests, 87 subtests and Ruff. Generic proposal/ack routing is now covered. The then-open department
plans/leases and conflict adjudication are covered by the subsequent bounded increments below;
full A10 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-generic-contract-ack-routing-review-20261021.json`
(SHA-256 `472a67f60b6669393416a3adb02a1fad69da2d4bfe9fbd40d8c70e72ff735cb5`).

The A10 conflict-adjudication increment at `49c311c9300596ca719e1301ef9d1ec2046c49a3` adds
current-plan-bound pair escalations, CEO-fenced append-only adjudications and explicit successor
records that require a changed plan revision. Bounded snapshot v3 reports open and stale conflicts
separately. Sol accepted exact diff `15d7a553edf4c0627181af0928ee991a26c24c186a0b5c55d57f0d82d1f73de3`;
the engineering gate passed 365 tests/96 subtests and Ruff. These records provide conflict evidence
only; no readiness, admission, execution, scheduling or runtime authority is added. Remaining trigger
freshness and broad A10 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-conflict-adjudication-review-20261021.json`
(SHA-256 `cbb8a0f9eeeea6e0716b46332361cfcf93655bc066e9df03ac25592a84f75925`).

A12 now has a bounded active-task projection at `e7e8f1d039cce909d91782c1cf51f4587ccbc016`.
SQLite selects at most 100 active rows through an indexed state/id query; full state counts are
reported independently, unknown states are summarized as `unclassified`, and the Founder report says
how many active rows are omitted. Integrated/failed history remains stored. Sol accepted the exact
source; the local gate passed 278 tests and 42 subtests with Ruff on 49 files. Exact full counts still
scan task rows; dependency indexing, other event/usage/receipt/checkout growth and full A12 remain
open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a12-active-task-projection-scaffold-review-20261020.json`
(SHA-256 `3e921d550fc67c2d295f6024a4fe24ebfcbe0e1572bb73e260607cf70961ba77`).

A04 now shares verifier-plan construction between `verify_one` and `reconcile` at
`066227ef14fec28ed9d8879e0b61668cd3448f1b`. Spec-backed tasks resolve their exact immutable binding
and never fall back to the fixed task catalogue; until executable manifest qualification exists,
their plans stop before Git, sandbox, model, acceptance or publication. Reconciliation checks the
plan before state transitions and continues past blocked intents; ordinary Git rejection does not
suppress the next candidate. Legacy policy digests remain unchanged. Sol accepted the exact source;
the local gate passed 284 tests and 42 subtests with Ruff on 50 files. `run_attempt` binding,
interpreter/sandbox/result qualification, production admission and A04 closure remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-shared-verification-plan-review-20261020.json`
(SHA-256 `0d2383df79740ae3dff73acfa6967de4788a424db8a24ad9cebca011d25a8a7d`).

At `838ff0882758a311334c0c67057a5796b8a617bb`, `run_attempt` resolves the same plan after lease
and circuit checks but before creating an attempt checkout or dispatching a model. Resolver errors and
unqualified plans fail the attempt closed; the worker packet carries plan, binding, decision and
acceptance identities. Sol accepted this bounded gate and the local gate passed 285 tests and 42
subtests with Ruff on 50 files. Candidate evidence does not yet compare the saved plan hash with the
verifier's re-resolved plan, so plan-to-attempt drift protection and full A04 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-run-attempt-verifier-gate-review-20261020.json`
(SHA-256 `7f8483c832e5bd14cdbf08da7b2e53f09edda9ba1e4549a326f2ca0f21df8ffb`).

At `b0e47312d496417c92efb5716babbd5713dce068`, the controller persists the full canonical plan
identity with candidate evidence and requires exact equality in verification and reconciliation
before all `submitted`, `verified` and `accepted` transitions. Bound evidence without identity and any
explicit `null` identity block; absent legacy identity remains compatible. Sol accepted the exact
updated source; the gate passed 286 tests and 42 subtests with Ruff on 50 files. A legacy evidence row
with its identity key removed still follows the compatibility path; full A04 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-attempt-plan-evidence-review-20261020.json`
(SHA-256 `7b773556fbe46f7aad7875c34ad2f53312c476660bf1203f097b3d1b31669ee8`).

At `9487fc70057bc8ff5a467394fe7b70b06391e7b3`, a source-only verifier-manifest resolver checks
canonical manifest bytes and digest, release/policy identities, the immutable binding digest, exact
verifier metadata, contained trusted harness paths, regular-file reads and harness bytes. Sol
accepted the exact two-file candidate for this bounded custody/identity scaffold; the engineering
release gate passed 320 tests and 55 subtests with Ruff on 54 files. This helper is not wired into
the shared plan and does not qualify interpreter, sandbox or result execution: bound plans still
fail closed. Off-catalog execution, production admission gates and full A04 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-manifest-review-20261021.json`
(SHA-256 `723d22cc639e466e9beb1e5277b44200d3aef26fe5995a82faf32d97349e08a8`).

At `e96dbd2ee4af7b0f2496730c97a1815b1f69a411`, `prepare_verification_plan` resolves the pinned
`controller/verifier-manifest.json` against the immutable binding and trusted harness, then adds the
manifest hash to the plan and attempt identity. Missing pins preserve the previous unqualified
behavior; malformed explicit pins fail closed. Even with a valid manifest, every spec-backed plan
remains unqualified until interpreter, sandbox and result execution are verified. Sol accepted the
exact two-file source diff; the full gate passed 322 tests and 55 subtests with Ruff on 54 files.
Off-catalog end-to-end execution, default-off production admission and full A04 remain open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-plan-manifest-review-20261021.json`
(SHA-256 `ae63abe99124eb4bfad6fe68b02b1b2177e1162037fdc2e347e9d747b9fc9e61`).

At `3c859e2`, bound result commands invoke the pinned harness directly and strict JSON result checks require the declared schema, acceptance identity and literal `true`. At `d985cff`, manifest v2 adds a canonical absolute interpreter path and executable digest; plan construction checks that digest against the configured interpreter, requires the first command to use `{interpreter}` then `{harness}`, and carries both identities into the plan and attempt evidence. Sol accepted the exact bounded source; the engineering gate passed 329 tests and 65 subtests with Ruff. This observes interpreter bytes during plan resolution, but does not prevent replacement before later invocation or qualify Python loader/invocation semantics. The bound plan remains `execution_qualified=False`; sandbox and executable integration acceptance remain open. Receipts: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-20261021.json` (SHA-256 `a045ffd8c9d747a9fbd848f28ee0917782023663c2acc04484be2b2b2954dfe0`) and its hash correction `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-correction-20261021.json` (SHA-256 `4a09da47cd6b643ff8853b782cdc2d5741ce136a2d8d9927294fb50ec0edfffe`).

The next A04 source increment rechecks the first bound command's canonical interpreter path,
regular-file/executable mode, and SHA-256 through a no-follow descriptor immediately before
`sandbox-exec`; cumulative reads are capped at 512 MB. The command result records the observation as
`prelaunch_executable_sha256`. This narrows the plan-to-launch replacement window, but
`sandbox-exec` still receives a path and resolves it later, so this does not prove that the hashed
inode ran. Parent-path replacement, post-check mutation, loader/invocation semantics, sandbox
qualification, executable integration acceptance, production admission defaults, and full A04
remain open. Plans remain `execution_qualified=False`. The updated engineering gate passed 332 tests
and 65 subtests; Ruff check/format passed on 56 files. Sol accepted this bounded source increment.

The next bounded A04 increment introduces manifest schema v3 and requires new verifier bindings to
start exactly with the pinned interpreter, `-I -S -B`, then the trusted harness. Admission rejects
other interpreter command prefixes and the manifest resolver keeps v2 manifests fail-closed. In
CPython these options ignore `PYTHON*` environment variables and unsafe path injection (`-I`), skip
automatic `site` initialization and its `.pth` processing (`-S`), and suppress bytecode writes
(`-B`); see the [Python 3.13 command-line reference](https://docs.python.org/3.13/using/cmdline.html)
and [`site` reference](https://docs.python.org/3.13/library/site.html). A synthetic run under a
disposable per-task Seatbelt profile exercised the manifest-shaped command with the pinned
interpreter and trusted harness, validated the strict result contract, and confirmed startup flags,
the prelaunch hash, profile identity and absence of checkout bytecode. The full engineering gate
passed 336 tests and 73 subtests; Ruff check/format passed on 56 files. This qualifies that
synthetic host/profile/command tuple only. Exact deployment roots, complete loader dependencies,
path-to-invocation identity, production default-off admission and full A04 remain open; bound plans
still have `execution_qualified=False`.

A source-only loader review of tracked `engineering/pilot_acceptance.py` finds direct standard-library
imports (`argparse`, `copy`, `hashlib`, `importlib`, `json`, `os`, `subprocess`, `sys`, `tempfile`,
`pathlib`, and `unittest.mock`) plus intentional dynamic loading and subprocess execution of candidate
code within the caller's sandbox. The harness file itself is hash-checked, but the interpreter digest
does not enumerate or hash the standard-library modules, Python native extensions, or macOS dynamic
loader dependencies used at startup. `-I -S -B` narrows startup inputs; it does not close that runtime
dependency graph. This source review neither inspects a deployed manifest nor executes the harness.
Exact runtime closure and deployment-root qualification remain open.

A disposable feasibility probe on macOS 26.7.1 / Darwin 25.6.0 (build 25G241) did not find a
reviewed public descriptor-to-exec path. The installed SDK's `execve` interface takes a pathname;
the SDK headers and current `libSystem` lookup expose neither `fexecve` nor `execveat`. Although the
SDK defines `O_EXEC`, `posix_spawn` of `/dev/fd/N` returned `EACCES` for an inherited `/bin/echo`
descriptor, both with `O_RDONLY` and `O_EXEC`. This is a host-specific negative result, not proof
that no private or alternative mechanism exists. No private interface is adopted. A04 remains
fail-closed until a supported, independently reviewed same-inode mechanism or an explicitly bounded
residual policy is established. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-darwin-fd-exec-probe-20261021.json`
(SHA-256 `1e22ca8a59053bf0b4b746a98b6d23b543391497ac7b92da2840defe92b0b07f`). The source basis is
Apple's [`execve(2)` reference](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/execve.2.html),
[Libc `unistd.h`](https://github.com/apple-oss-distributions/Libc/blob/main/include/unistd.h), and
[XNU `kern_exec.c`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_exec.c).
This does not address Python loader/import dependency closure.

A follow-up read-only inspection of the installed macOS 27.0 SDK on macOS 26.7.1 confirms that its
public `unistd.h` declares pathname-based `execve`/`execv` but no `fexecve` or `execveat`; the SDK
text stubs likewise expose `_execve` without either descriptor form. Header and stub hashes are in
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-macos-sdk-descriptor-exec-surface-20261021.json`
(SHA-256 `b80848f7fdea1898e5e00d2496c73f80f7b121d1c4c0b7a3bdb5c8d250345a8b`). This narrows the
supported public interface available on this host; it does not rule out every alternative mechanism
or establish execution qualification.

Apple's launch environment/library constraints are a separate code-signing policy, not a way for
the current `sandbox-exec` path to execute the already hashed descriptor. Apple documents
`SpawnConstraint` for launchd agents/daemons and launch environment constraints embedded in the
target executable's signature; neither establishes descriptor-to-invocation inode identity for
this A04 command. We therefore do not treat them as a substitute for the missing same-inode binding.
See Apple's [applying constraints](https://developer.apple.com/documentation/Security/applying-launch-environment-and-library-constraints)
and [defining constraints](https://developer.apple.com/documentation/security/defining-launch-environment-and-library-constraints)
documentation. This is a bounded applicability finding, not a claim that code-signing constraints
are ineffective for their documented use cases.

A subsequent A04 source increment raises the verifier manifest to v4 and pins both the canonical
runtime executable path and its 20-byte code-directory hash. The resolver checks that signed-code
identity against the controller manifest. For the first bound verifier command only,
`SandboxRunner` observes the launched PID through Apple's public Security API and requires the
reported code-directory hash and main executable path to match before accepting the command result;
a mismatch or failed observation kills the process group. A disposable macOS test confirms that the
reported signed-code identity follows the originally launched image across replacement of its path,
and that a mismatching hash or path is rejected. The engineering gate passed 348 tests and 83
subtests; Ruff check and formatting passed. Sol accepted the exact candidate diff in bounded
source-only review. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-identity-review-20261021.json`
(SHA-256 `dfbed547202fe6ec40cbc09c8612681264319d637e273944e2e6a91cfe783ec5`).

At `e9bb957`, `SandboxRunner` schedules repeated process-image checks every 100 ms while collecting
output and waiting for the first command of each bound verifier to exit. The synchronous Security API
call can make the actual spacing longer. The recorded observation count includes
the initial matching image; a disposable signed-binary regression waits for that initial match, then
execs a second image and verifies that the changed identity is rejected before a result is returned.
Sol accepted exact diff `54e33e3c984a18660433c97d6dc1d720d9b3128d0353f3fe25c56b739400f8d4`; the
engineering gate passed 366 tests and 96 subtests with Ruff. Periodic sampling leaves brief image
changes between checks, descendant images and stdout provenance unbound. The synchronous Security
call is not interruptible by the command timeout. It does not prove a literal inode binding or close
Python imports, native extensions, or dynamic-loader dependencies. It covers only the first command
of each verifier and does not qualify the sandbox or production deployment. Plans remain
`execution_qualified=False`; full A04 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-monitor-review-20261021.json`
(SHA-256 `5e65765997a18e9b75881156ef30c50aaf3b5ccbf78e9f9e8b1f101323aab342`). Apple
references: [`SecCodeCopyGuestWithAttributes`](https://developer.apple.com/documentation/security/seccodecopyguestwithattributes%28_%3A_%3A_%3A_%3A%29),
[`kSecGuestAttributePid`](https://developer.apple.com/documentation/security/ksecguestattributepid), and
[signing information keys](https://developer.apple.com/documentation/security/signing-information-dictionary-keys).

At `f4148cb`, the resolved manifest harness digest is carried into each `BoundVerifier` and
rechecked immediately before `Popen`. The runner requires the canonical harness path exactly at
`argv[4]` after `-I -S -B`, opens it without following the final symlink, requires a regular file,
limits the cumulative read to 16 MB, compares descriptor metadata before and after hashing, and
records `prelaunch_script_sha256` in command evidence. A synthetic regression changes the harness
after plan resolution and confirms rejection before `Popen`; Sol accepted exact diff
`3d69f91ff4b844d0c3fc030c36568c649a60048a5f6fd570380276a5338121d2`. Focused tests passed 31 tests
and 10 subtests; Ruff check and format passed on 58 files. Initial full-suite runs showed 400 passes,
96 subtests and one intermittent `PermissionError` in the existing process-group cleanup path of
`test_running_identity_survives_path_replacement`; the same failure occurred on clean base `78ff669`.
An isolated rerun and a diagnostic instrumented baseline run passed. A later uninstrumented full run
on this exact candidate passed 401 tests and 96 subtests. The cause of the intermittent cleanup
failure remains unknown; this candidate does not change cleanup. The hash records only a prelaunch
observation: Python later opens by pathname, so replacement or mutation after the check remains
possible. Synchronous filesystem reads have no hard deadline. Loader/import closure, descendants,
stdout provenance, deployment qualification and full A04 remain open; plans stay
`execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-harness-custody-review-20261021.json`
(SHA-256 `319bc7da1f94aa5366daa0ab690cf8cf6bdccfcfa033314546ba91d446182919`).
Gate rerun receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-harness-custody-gate-rerun-20261021.json`
(SHA-256 `02197749030f0be284acd1fbc5e1bcdae0fe0598dbf78fb3b09ac6c3caa3a77e`).

A synthetic macOS integration test now joins persisted task admission and verifier binding, manifest
v4 resolution, bound-command expansion, `SandboxRunner`, and strict result parsing in one disposable
path. It discovers the runtime image identity as observed under Seatbelt, distinct from the Python
launcher file path and digest. Sol accepted the exact one-file diff
`5e03ada485c56a4635da07d89c7cdb6679c21b3ac4cecf790b87af09ca6316cd`; Ruff and the engineering
release gate passed (370 tests, 96 subtests). The test invokes the low-level runner only to establish
synthetic plumbing; the plan remains `execution_qualified=False` and the production supervisor still
fails closed. This does not qualify deployment roots, loader/import closure, same-inode invocation,
continuous identity, descendants, output provenance, or sandbox policy. Fixed probe/harness sleeps
can fail on a heavily stalled host, and synchronous Security API calls remain uninterruptible.
Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-plan-sandbox-integration-review-20261021.json`.

The interpreter manifest resolver now compares descriptor identity metadata before and after
hashing and requires the bytes read to equal the initial descriptor size. A controlled same-size
overwrite during the descriptor read is rejected. The independent A04 review accepted the exact
source diff `415857999494287bc3b65f2e7d7035c4ecd6babbaaa187d90d9f23e361badeaf`. Its focused
regression passed. An initial attempt under Python 3.9.6 was incompatible with existing `zip(strict=)`
and JSON decoder behavior; the full engineering release gate was then rerun with the project Python
3.13.6 and passed: 410 tests, 96 subtests, Ruff check/format. Descriptor stability narrows read-time
mutation evidence only. It does not bind later
execution to the descriptor, close parent-path races, or qualify loader/import closure; plans remain
`execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-stability-review-20261021.json`
(SHA-256 `3d9f73874c37bd0992e96e8292bc9d2df246481087f3f2a873802ddb8090ff76`). Gate rerun receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-stability-gate-rerun-20261021.json`
(SHA-256 `fd14da053f07fa9a99fd0280f0a2edd7e8d3fb72a81d5a5cf3dfa2aedd6af4f3`).

Before any future A05 request consumer is designed, its inputs must come from one coherent,
source-identified gate snapshot with capture time or sequence. Consumption must reject unknown or
stale observations and re-evaluate database gates under the current owner fence using the Store
clock, including after lock waits. Deduplication must use a stable controller-owned authorization
generation distinct from the replaceable owner epoch, with at most one terminal passive disposition
per generation. A consumer may persist that disposition only; process/service action requires a
separately qualified path. Current observation binding does not provide these guarantees, so no
consumer is implemented and A05 remains open.

The source candidate, merged-system acceptance, operational release, and founder authority to resume
the original pilot are distinct gates. A03 currently blocks full operational release, not independent
engineering progress. Passing synthetic tests or the local release command alone does not pass them.

### Terminal usage evidence source qualification — 2026-10-20

Read-only generation of the app-server JSON schema from pinned Codex CLI `0.163.0` confirms that
`thread/tokenUsage/updated` carries `threadId`, `turnId`, and `last`/`total` token counters. The
notification schema has no terminal/finality field. In the current RPC adapter, matching updates are
persisted as evidence but treated as progress; `turn/completed` returns `usage_terminal=false` and no
usage delta. `account/usage/read` can report estimated account or thread usage, but the response does
not bind counters to a dispatch, turn, or attempt epoch. No live model turn or account usage request
was made. Sol independently accepted the first receipt only as static schema/source correlation and
rejected terminal-settlement qualification: those identifiers do not prove same-epoch dispatch/model
attribution, finality, event ordering, completeness, or late-correction handling. A follow-up
read-only probe of the same pinned CLI found `rawResponse/completed`, whose schema carries
`responseId`, `threadId`, `turnId`, and per-response usage or `null`. The first follow-up schema export omitted `experimentalRawEvents` because it used the default
experimental-field filter. A corrected read-only export with `--experimental` confirms that exact
pinned v0.163.0 includes the boolean opt-in, but its schema description says it is for internal use
only; the `RawResponseCompletedNotification` schema is itself described as internal-only. The raw
payload has no dispatch/model/epoch binding. Pinned source inspection shows core emits the
per-response event after completed upstream Responses API results (including foreground compaction);
core tests reject incomplete streams as completed. However, `response.incomplete` can include reported
spending that core records separately without emitting `RawResponseCompleted`; upstream tests assert
that raw event is absent while retaining the nonzero usage, and the turn still reaches `TurnComplete`.
Therefore raw completion events are not a complete turn-usage ledger even with perfect delivery. The
app-server adds thread/turn identifiers and forwards this event before its turn-completion
notification, but gates it behind the internal-only opt-in. These are source semantics only: no runtime
delivery, reconnect, duplicate, or late-correction behavior was exercised. This is not an authorized
owner-adapter source. Exact default and experimental schema hashes are in the correction receipt below;
source hashes and URLs are in `terminal-usage-raw-response-source-semantics-20261020.json` and
`terminal-usage-raw-response-incomplete-gap-20261020.json`. Until a reviewed supported interface
establishes complete accounting, full dispatch/model/epoch binding and delivery completeness, ORG-A03
stays fail-closed. Unknown usage remains reserved; `turn/completed` remains lifecycle-only.

A public-snapshot alternative also fails completeness. If an upstream response completes without
usage before any token-usage state exists, core emits a token-count event with no usage info and the
app-server suppresses `thread/tokenUsage/updated`. A later response with valid usage can therefore
produce a positive cumulative snapshot that appears complete against a zero baseline, although the
earlier response's spend is unknown. Public lifecycle/item events carry no guaranteed response count
or marker that rules out this hidden response. Sol independently rejected settlement based on such
snapshots. Evidence is source-only in
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-public-snapshot-null-gap-20261020.json`;
SHA-256 `810efd6528d1cdcdcb0d0283ac2a0877ae93bb5d049f56a136cddf88b42fff38`; it supports the
fail-closed boundary, not a settlement implementation.

Sol reviewed a narrower guard: if valid provider token state is independently proven before dispatch,
any in-turn snapshot with zero delta could invalidate settlement because a normal null-usage response
with existing state republishes the unchanged counters. That snapshot is indistinguishable from no
new spend or a service publication, so even exact identity matching cannot safely ignore it. More
fundamentally, the ledger baseline does not prove core had provider state; the public stream does not
qualify baseline provenance/completeness, model identity or attempt epoch, and no current supported
path seeds such a baseline for settlement. This is a conditional guard only, not an accepted settlement
source. The source-only review is in
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-public-snapshot-guard-review-20261020.json`
(SHA-256 `3e082f264a19e92f11b3023556150613059e03ee186573080a9b1573bfa89f8e`).

Core also persists internal `TokenUsageRecord` rollout rows with response IDs and usage, including
reported incomplete-response spending when both response ID and usage exist. The pinned public
`thread/read` history projection explicitly ignores these rows; item list/read APIs return projected
items, and resume replays only aggregate `TokenCount` state. Null usage creates no record, and these
rows contain no dispatch/model/epoch or missing-response completeness marker. Sol independently
rejected this as a public or sufficient internal settlement path. No private rollout data was read.
Evidence: `/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-token-record-public-api-review-20261020.json`
(SHA-256 `a72445772e0a4aabff3b59621deb881b30ebc8d0161164b22c1d7ba08a6a26e3`).

Sol also identified a conditional parser compatibility gap in `metered_codex.capture`: the generated
schema permits an optional `cacheWriteInputTokens` counter, but canonicalization currently requires
the counter object to contain exactly the five other known keys. When the optional sixth key is
present, the raw receipt is retained while the canonical progressive observation is dropped. Live
payload frequency and cache-write pricing semantics were not established. The `3c16388` source
checkpoint accepts the optional field only as a nonnegative integer, keeps it in raw evidence,
and does not add it to or subtract it from the five canonical counters. The existing invariant
`totalTokens = inputTokens + outputTokens` remains required. Focused synthetic five- and six-field
cases were added. The pinned engineering release gate subsequently passed with Ruff lint/format and
pytest (251 tests, 42 subtests). This is parsing/retention only, not terminal settlement or
cache-write pricing qualification.

The independent review is recorded in native Sol thread
`01a15a5e-7fc3-7bf0-9a10-73199636bfcd`.

The immutable read-only schema/source receipt is stored outside Git at
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-source-qualification-20261020.json`
(SHA-256 `a29a219a65650aaee8f048dbbd54e142fd5cc4d32a1dd3544ed8b54a17298422`).
The follow-up raw-response schema probe is stored separately at
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-raw-response-source-probe-20261020.json`
(SHA-256 `b4372bd744c4f3c61a71410df99e25eba101439959b13e939a43847b6642cf05`); the earlier receipt
remains unchanged. The earlier no-`--experimental` schema omission is corrected by
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-schema-filter-correction-20261020.json`
(SHA-256 `89ef2e6a6017b5dd9a6bff446742a3263e43238e4e298c3c2084e927903e1640`); it does not authorize
using the internal-only stream.

A follow-up comparison against official Codex source closes two apparent alternatives. Stable
v0.163.0 contains `experimentalRawEvents` in its experimental schema, but labels the opt-in and
`rawResponse/completed` internal-only. The v0.164.0-alpha.7 prerelease carries the same internal-only
restriction.
The stable `codex exec --json` exporter stores the latest `thread/tokenUsage/updated` snapshot and
copies its cumulative `tokenUsage.total` into successful `turn.completed`; missing snapshots become
zero usage, while failed and interrupted turns do not carry terminal usage. That output is not
independent terminal evidence and does not establish same-dispatch/model/epoch completeness. No
binary was downloaded and no internal opt-in or provider request was used. A03 remains fail-closed.
The original read-only source-comparison receipt remains immutable at
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-source-contract-review-20261020.json`
(SHA-256 `f414ecb8a9bb9576d8a3e33d3cc32e3d5017722718f56c97ece6790b732f3aff`); its claim that v0.163.0 lacked the opt-in is superseded by the schema-filter correction receipt above.

The exact `d4d6590` service code also passed a direct end-to-end synthetic run of
`test_snapshot_uses_sqlite_backup_and_copies_policy_and_receipts`: it created a complete checkpoint,
moved the source state aside, restored into a fresh path, and checked preserved STOP, SQLite contents,
copied private files and integration Git ref, persisted host-requalification barrier, and
`runtime_ready=false`. The fixture and Git repository were temporary and synthetic. This does not
qualify a restore of the owner deployment or pilot; those were not accessed or restored.

On 2026-10-20, the current Mac passed the candidate's no-inference native permission-profile
qualification (21 checks) using pinned Codex CLI 0.163.0 and the adapter hash from exact candidate
`3c16388`, plus the OS confinement qualification (27 checks) with Python 3.13.6. Receipts are
`/Users/root1/Developer/brain-org-state/resumption-20261020/native-profile-qualification-20261020.json`
(SHA-256 `dfbdddeb79533198f27c63c35e85026d641b2d3c45cc281dea73a4b33d821889`) and
`/Users/root1/Developer/brain-org-state/resumption-20261020/confinement-qualification-20261020.json`
(SHA-256 `d17acddff1ea8f8521b80bee4b9508906d9dce1adcdfcdb64908e56691bf546f`). No model, account,
thread, or turn request was made. These receipts qualify the current host controls and pin; they do
not bind a particular restored deployment or clear its persisted requalification barrier. The exact
candidate's restore receipt remains `runtime_ready=false`.

### Evidence established in this audit

- Current read-only `thread/goal/get` returned `paused` for both former roots. The implementation
  root's last rollout Goal event still said `active`; log tail is not current native Goal state.
  Native metadata and the last turn context identify that implementation owner as `gpt-6-luna/high`.
- Pilot SQLite still contains three tasks, six failed attempts, 106 events, zero acceptances and
  zero integrations. EXP-001 and CTX-001 exhausted three attempts each; PLAT-001 remains dependent.
  All six failures are JSON parsing failures at the Sol lead stage, before any worker stage.
- With a canonical temporary-directory path: **216 engineering tests and 42 subtests passed;
  two failed**. The failures are unchanged legacy raw-adapter acceptance wrappers requiring the
  now-mandatory Permit. The first run additionally exposed two fixture path failures through the
  macOS `/var` alias; changing only the test temporary directory resolved them.
- Real disposable Seatbelt tests ran in that suite. A separate installed native-profile probe
  passed **21/21**, Codex CLI `0.163.0`, with no model, thread-start, turn-start or account request.
  These results do not qualify real dynamic-tool delivery, release, reboot or endurance.
- Engineering Ruff and the synthetic Brain smoke passed. The repository Ruff stage still reports
  **107 findings**; the full repository check was not declared green. No training suite ran.
- A new experiment connected real temporary Git repositories, `GitGate`, `Store` and
  `supervisor.reconcile`: two disjoint changes from the same original base survived sequential
  integration, with recovery before database verification and after Git publication. Repeated
  reconciliation produced exactly two integrations. Acceptance in this experiment was a synthetic
  content check, not an independent model review or a physical process-crash trial.
- All 69 recorded original commit pins are reachable from the archive; all selected component
  files match their source pins, all four archived dirty-source hashes match, and the 11 bundles
  and three private dirty archives match their recorded hashes. Local Git fsck passed. Advertised
  remote branch/archive pins matched the consolidation handoff at observation. This audit did not
  repeat a fresh network clone or the nine ignored-artifact restore trials.

### Material findings

P1 means a blocker for the intended integrated release or sustained operation. P2 means a design
or assurance gap that must be bounded before expansion. These severities do not imply demonstrated
production corruption or a successful sandbox escape.

| ID | Priority | Evidence and consequence |
|---|---|---|
| ORG-A01 | P1 | **Bounded source repair accepted by Sol at `3c16388`:** bootstrap writes `codex_executable_sha256`, matching `service.validate_deployment`. Prior synthetic bootstrap evidence reproduced the rejection as `codex_executable_binding`. The pinned engineering release gate passed. |
| ORG-A02 | P1 | **Bounded source repair accepted by Sol at `3c16388`:** bootstrap initializes the ledger only after both qualification steps, before creating organization state. Schema creation, legacy column additions and `user_version=1` commit together. Existing version-1 ledgers must match exact ordered columns, types, nullability, primary keys, grouped composite foreign keys and unique indexes; newer, partial, extra-table and unknown schemas fail closed. A split-FK regression test rejects two single-column constraints in place of the required composite key. The pinned engineering release gate passed. |
| ORG-A03 | P1 | The operational adapter always returns `usage_terminal=False`; progress counters are deliberately unattributed. `metered_client` sets `terminal_usage_qualified=False`, and there is no operational caller of `reconcile_dispatch`. A completed synthetic turn retained its reservation and `model_attribution_unverified`; scheduler capacity became zero. Correct conservative accounting has no qualified operational settlement path. Do not fix this by assuming unknown usage is zero. |
| ORG-A04 | P1 | `Store.admit_task_spec` atomically persists a valid spec and controller-owned verifier metadata binding; a shared plan at `066227e` resolves that exact decision in `verify_one` and `reconcile`. Spec-backed tasks never fall back to `cfg['tasks'][id]['checks']`; plans stop before execution when unqualified. At `838ff08`, `run_attempt` resolves the plan before checkout or dispatch and puts plan identities in the worker packet. At `b0e4731`, candidate evidence stores the plan identity and verification/reconciliation require exact equality before transitions; bound missing identity and explicit `null` block; absent legacy identity remains compatible. At `e96dbd2`, configured manifest and harness identity enter plan/attempt identity. At `3c859e2`, the first result command must invoke the pinned harness and its JSON result must match schema and acceptance identity with literal `true`. At `d985cff`, manifest v2 also pins canonical interpreter path and executable SHA-256, requires that interpreter at argv[0] and harness at argv[1], and binds interpreter identity into plan/attempt evidence. Sol accepted bounded source. The gate passed 329 tests and 65 subtests; Ruff check/format passed. At `a6f2dfb`, manifest v4 adds runtime path/CDHash pins and one Security API process-image observation for the first bound command; Sol accepted the exact bounded diff, gate passed 348 tests/83 subtests and Ruff. It does not establish literal inode or prior/later execution/result provenance. At `e9bb957`, `SandboxRunner` schedules process-image checks every 100 ms during output collection/wait for the first command of each bound verifier; synchronous Security API calls can extend the spacing. A marker-controlled signed-binary regression proves an exec image change was observed after the initial match. Sol accepted exact diff `54e33e3c984a18660433c97d6dc1d720d9b3128d0353f3fe25c56b739400f8d4`; gate passed 366 tests/96 subtests with Ruff. Sampling still leaves brief inter-sample changes, descendant identity and stdout provenance open, and the synchronous call is not interruptible by command timeout. Spec-backed plans remain `execution_qualified=False`; replacement between resolution and invocation, interpreter/loader semantics, sandbox qualification, executable integration acceptance, production admission default-off gates and full A04 remain open. Receipts: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-result-contract-review-20261021.json` (SHA-256 `ecfb8f8a9888a96b059e1fa2122eb6331aeb5de81353ad3f8ab43fc85a6e4278`), `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-20261021.json` (SHA-256 `a045ffd8c9d747a9fbd848f28ee0917782023663c2acc04484be2b2b2954dfe0`; test hash correction at `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-interpreter-pin-review-correction-20261021.json`, SHA-256 `4a09da47cd6b643ff8853b782cdc2d5741ce136a2d8d9927294fb50ec0edfffe`); process-identity review: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-identity-review-20261021.json` (SHA-256 `dfbed547202fe6ec40cbc09c8612681264319d637e273944e2e6a91cfe783ec5`). |
| ORG-A05 | P1 | Successful supervisor exit after its bounded episode is not restarted by the generated `KeepAlive={'SuccessfulExit': False}` plist. Quiescence writes a Sol handoff and exits; no qualified consumer of that handoff exists. `reset_recovery_decision` has no operational caller. Chat Goal continuation and the frozen pilot are separate control paths, not one qualified lifetime service. A pure lifecycle planner scaffold at `91c6cfa` deterministically gates an inert launch request on STOP/runtime/accounting/quota/process/recovery/backoff observations, rejects stale owner metadata, and requires request owner/epoch. Sol accepted the exact source; the 274-test/42-subtest Ruff gate passed. Durable fencing, state persistence, request consumption/deduplication, retry policy, scheduler/process integration and operational recovery remain open. See `/Users/root1/Developer/brain-org-state/resumption-20261020/a05-passive-lifecycle-scaffold-review-20261020.json` (SHA-256 `87596c56a7e60b86a9ab85a70a2e03b15ab9ac613f2e9b8539a7fa2eafc88d97`). |
| ORG-A06 | P1 | **Bounded source repair accepted by Sol at `3c16388`:** snapshot v2 copies the private state tree, backs up both SQLite databases, requires exact organization column/type/nullability/default/PK/FK/unique signatures, checks FK data integrity and no live attempts, and verifies the integration repository offline with sanitized Git configuration while rejecting alternates, shallow repositories and promisor remotes. It retains STOP, intents, attempts, logs and Git objects/refs; holds the supervisor lock; canonicalizes destination containment; and restores only to a fresh path. Synthetic restore after moving the source copy rebinds bundled executables and permits unavailable host executables only behind the persisted barrier. Relaxed structural validation requires `restore_requires_host_requalification=true`; restore receipts say `runtime_ready=false`. Fresh-path structural checks passed. Host qualification and operational/pilot restore remain open. |
| ORG-A07 | P1 | **Bounded source repair accepted by Sol at `3c16388`:** the quota watchdog cancels active work for explicit `stop`, `quota_critical`, or `reserve_threshold` decisions. A zero count of additional admission slots alone does not cancel a permitted active team. Synthetic threshold and high-quota continuation probes passed. Drain timing, bounded overshoot and provider billing remain unqualified. |
| ORG-A08 | P1 | **Bounded source repair and release target accepted by Sol at `3c16388`; pinned gate passed:** `make VENV=/Users/root1/Developer/memecoin-research/.venv/bin engineering-release-check` used Python 3.13.6 and exact `requirements-dev.lock` versions. Ruff lint and format passed; pytest passed 251 tests and 42 subtests. The external venv was not modified. Hosted workflows and native controller acceptance remain separate. |
| ORG-A09 | P2 | **Bounded source repair accepted by Sol at `3c16388`:** malformed or wrong-shaped lead/worker output opens a durable circuit and recovery obligation in one lease-fenced SQLite transaction that also fails the active attempt. Queued attempts read the decision body and stop while the circuit is open. Recovery uses one transaction bound to both current decision versions and requires a qualified fix, independent release review and separate bounded authorization. Output object fields, enums and list element types are checked before submit. The old six-attempt pilot history is unchanged. |
| ORG-A10 | P2 | Runtime contract transitions are moving beyond the original EXP-001/schema-validation v2 special case. Before the bounded plan/lease scaffold, department names were task labels without durable plans or leases; conflict adjudication and a research experiment service remain separate gaps. A read-only Store readiness report at `57f496f` identifies non-integrated/missing task dependencies and non-current contract requirements while preserving unrelated ready work; transactional `claim` remains authoritative. At `ef953d5` (exact reviewed source `0879b5e`), initial v1 publication is distinct from a content-hashed one-version transition with immutable producer/consumer acknowledgements; activation atomically requires the integrated producer and all exact acknowledgements. Supervisor binds acknowledgements to configured content. The `4fbf196` increment reconciles every configured contract independently using its version, producer identity and consumer set, preserving legacy config and revalidating prior activation evidence; Sol accepted the exact diff, the gate passed 351 tests/83 subtests with Ruff. At `886a2e2`, configured proposals are routed to participant leads and acknowledgements bind exact name/version/hash; stale source versions fail closed, legacy schema-validation config remains compatible, Sol accepted the exact diff and the gate passed 354 tests/87 subtests with Ruff. At `9b18609`, the bounded coordination scaffold adds two-team epoch-fenced leases, immutable owner-attributed plan revisions, review deadlines and bounded snapshot/report projections; plan reads use one SQLite snapshot, covered by a cross-connection interleaving regression. Sol accepted exact diff `ec616e1e`; Ruff and the gate passed 362 tests/96 subtests. The scaffold grants no admission, execution or scheduling authority; conflict adjudication and broad A10 remain open. At `5e4d123`, the trigger atomically reconciles blocked dependency duties against readiness, records exact blockers and ready tasks in a bounded content-addressed snapshot, cancels stale duties with a durable snapshot event, and preserves unrelated ready work. Sol accepted the exact diff; the gate passed 341 tests/73 subtests and Ruff. At `714bb57`, quiescence handoffs bind a coherent readiness snapshot and Store rechecks under `BEGIN IMMEDIATE`; the `01a4c14` regression tests a real second-connection lock wait with stale rejection and fresh acceptance (Sol accepted; gate 349 tests/83 subtests, Ruff). The timeout is practical contention evidence only. At `86a3b55`, task and contract SQLite triggers maintain a monotonic quiescence revision; each Store binds a canonical snapshot digest to its issuance, and handoff recording checks that digest and revision after `BEGIN IMMEDIATE` without rebuilding the task/readiness catalogue under the write lock. Snapshot canonicalization remains outside that lock. Deployment validation accepts the exact previous organization schema v2; opening it with the current Store installs the revision table and triggers. Sol accepted exact diff `ab487e9f2898e1c36c41945f8009e9527c3751c2ff8ca84c69dcf624cd0e38c2`; the full gate passed 369 tests/96 subtests with Ruff. Quota/resource observations remain outside the transaction, task state may change after commit, and snapshot construction/fingerprinting still scale with tasks and blockers. This does not qualify runtime or close broad A10. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-revision-review-20261021.json` (SHA-256 `8e89894afd372733cf75baa07823994ebd1ff292ceb43269de539bebf794158f`). Remaining trigger freshness limits and broad A10 remain open. Readiness receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-readiness-report-scaffold-review-20261020.json` (SHA-256 `22037f53771b100bc1694f5f9133eec59ceeef519888389ff98f42d03af62176`); transition receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-durable-contract-transition-review-20261021.json` (SHA-256 `d94296b79abae78b7d22cce4348b750a2123046dbb860250ff13407490e9424e`); dependency receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-dependency-obligations-review-20261021.json` (SHA-256 `30a9fac250685cb73ad28db0575b6d75322c196c9f44d43bb122682b3c4c47f4`); trigger freshness receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-trigger-freshness-review-20261021.json` (SHA-256 `efc80cca51087c30d66414069359e7516eef86d415dbbcb6e4ffc1e699114c87`); lock-wait test receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-lockwait-review-20261021.json` (SHA-256 `e8f16bbb23dcc955185586c72148de3a7bf18633500d8ee001547fb86ef5d254`); generic activation receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-generic-contract-activation-review-20261021.json`; generic proposal/ack routing receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-generic-contract-ack-routing-review-20261021.json` (SHA-256 `472a67f60b6669393416a3adb02a1fad69da2d4bfe9fbd40d8c70e72ff735cb5`); department plan/lease receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-department-plan-leases-review-20261021.json` (SHA-256 `7ea47eacb174c8c2723c385b00d84977453554ac7f14fbbd71f19912b3fdb84b`). At `58a95c5`, quiescence includes bounded pending participant reviews and emits an inert `contract-reviews-pending` handoff when quota permits; case-sensitive namespace matching aligns with reservations and revision triggers. Sol accepted exact diff `20d1ab3b352574fc7faa20a4c8eb572fe6484d3577070de5aad3e11c23fafea1`; the gate passed 392 tests/96 subtests with Ruff. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-aware-quiescence-review-20261021.json` (SHA-256 `1be3d2736d398ce058bfc32fb547f75d1f9bbeb2020617eb151a35dabbd90b9a`). At `a386bd4`, Store-owned review currentness revalidates the immutable request, proposal/source contract, producer snapshot, participant inventory and exact acknowledgement; dispatch intent rechecks under `BEGIN IMMEDIATE` immediately before its one-shot journal write. Stale state is rejected without mutation, including a cross-connection lock-wait race. Sol accepted exact diff `2eb7797849c01ce236151c4664803e8399c9bbbac4ef4303dd59ef14f6adbdf0`; Ruff and the engineering gate passed 397 tests/96 subtests. This is a point-in-time dispatch-intent check: later drift remains subject to finalization checks. No supervisor/provider dispatch or recovery is wired; A03 settlement, broad A10 and operational acceptance remain open. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-currentness-review-20261021.json` (SHA-256 `f9c5cf1be1181d4309132c467abe318cd306ddc609aa3f3611edb6171ec8f04e`).  At `8a68dd1`, `Store.contract_review_worklist` discovers and validates bounded request journals in one read snapshot, returns request/status/hash/currentness without packet contents, and marks only current prepared requests as advisory-eligible. It fails closed when the caller bound (maximum 100) is exceeded; terminal records count toward the limit and query cost can still scale with retained decisions. Sol accepted exact diff `043e73f781be21ad93390ea516b0fb19707fdd3601c840e16e618cee1dbaecc5`; full engineering gate passed 399 tests/96 subtests with Ruff. No supervisor/provider dispatch is wired; A03 settlement, recovery and broad A10 remain open. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-review-20261021.json` (SHA-256 `be7ca8ca9cd92c890227f363da874c5979ec1694598ebe6a50b99d7479adcc2e`). At `edbc1f4`, quiescence adds the bounded prepared-review worklist to the same SQLite snapshot and carries that exact projection in Sol’s inert handoff. Store validates it against the issued snapshot and rechecks the decision revision under `BEGIN IMMEDIATE`. Above 100 retained requests, the handoff explicitly reports overflow with unknown count and no partial identities; public worklist shape and strict overflow behavior remain unchanged. Sol accepted exact diff `e168b3617d4636850c81a9a47a208abbe4a6bcaef5f9e79730e2004ac5b7f44d`; full gate passed 401 tests/96 subtests with Ruff. This adds no provider dispatch or recovery. Retained-decision query cost and overflow detail loss remain; A03 settlement and broad A10 remain open. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-review-request-handoff-review-20261021.json` (SHA-256 `a08a2cd7e32c4f3cafdc5615b5d0d5471f469e7697d0627d1d62d202db57b600`).|
| ORG-A11 | P2 | Every attempt creates a Sol lead and Luna worker; every candidate gets a Sol review. The previous packet silently truncated diffs at 50,000 characters and omitted exact merged-candidate identity. A bounded packet builder now includes task brief, submitted/base/integration-tip/merged SHA, submitted and exact merged-range path sets, acceptance identity, verifier-policy digest, complete command results and full text diff. Binary summaries, invalid path encodings, diffs over 50,000 bytes and final packets over 256,000 bytes fail before reviewer dispatch. GitGate's content-addressed receipt includes the packet digest, while publication still compare-and-swaps the captured integration tip; a moved tip fails closed and requires a new candidate/review. Sol accepted this bounded source repair; the full pinned gate passed 265 tests and 42 subtests with Ruff on 47 files. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a11-review-packet-source-review-20261020.json` (SHA-256 `2f3bc78360743201c6673c9b044405f33fe27ef7ac7d7e2a0e6e4d90ae94dab7`). Fixed catalogue routing, dynamic risk-aware model routing, reviewer file inspection and broad A11 remain open. Independent verification stays mandatory. |
| ORG-A12 | P2 | Store passes all historical tasks to a validator with a 1,024-element JSON list bound; 1,025 rows fail before semantic validation. A bounded active-task projection at `e7e8f1d` now selects at most 100 active rows through an indexed state/id query, reports full per-state counts plus an unclassified count, and makes the Founder report state omitted active rows. Terminal history remains stored. Sol accepted the exact scaffold; the pinned gate passed 278 tests and 42 subtests with Ruff on 49 files. Exact aggregate counts still scan task rows; dependency indexing, full event/usage scans, raw-RPC persistence, accumulating candidate checkouts and multi-month scale remain open. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a12-active-task-projection-scaffold-review-20261020.json` (SHA-256 `3e921d550fc67c2d295f6024a4fe24ebfcbe0e1572bb73e260607cf70961ba77`). |

The bootstrap and scheduler failures predate consolidation: their source bytes match the selected
validator/metering lineage. Sol preserved source custody accurately; that does not certify the
interfaces between independently approved components. `org/development` is a valid development
baseline and an unsuitable operational release.

The bounded source candidate also adds optional cache-write counter retention (see the terminal
usage review above), bootstrap/ledger wiring, quota cancellation, a controller parser circuit, a
full private snapshot/restore path, and an explicit engineering check target. Earlier Sol reviews
accepted A01/A07 and A02 bootstrap ordering, then found and drove repairs for schema, output typing,
split foreign-key acceptance, source-path executable dependency, backup destination containment,
unowned destination cleanup, delayed restore barrier, missing Git object validation, and incomplete
synthetic organization schema. Sol independently reviewed the exact `3c16388` candidate (`gpt-6.1-sol/high`) and accepted it for
bounded source repair, including the persisted requalification barrier required by relaxed structural
validation. The pinned engineering release gate passed: Ruff lint/format and pytest (251 tests, 42
subtests) using the exact development pins. The test fixture corrections supply the required permit
and matching thread IDs and assert that progress plus `turn/completed` does not settle usage. This is
not full release qualification: ORG-A03 settlement remains fail-closed, and operational restore/host
qualification remain separate. Unknown and late usage still retain reservations. No pilot restore or
replay occurred.

## Architecture retained

- **Local transactional authority.** SQLite WAL/FULL, append-only events, epoch-fenced leases,
  immutable candidates and idempotent acceptance/integration are appropriate for one host. Attempts
  can repeat; stale attempts cannot submit under a replaced fence. SQLite is not distributed consensus.
- **One trusted publisher.** Import explicit commits, enforce scope/protected paths, build an exact
  merged candidate, verify it independently, persist intent before publication, and CAS the expected
  integration tip. Revalidate after the integration tip changes. Workers never hold Git publication
  authority. Main has a separate release boundary.
- **Separate trust domains.** Workers get isolated checkouts, a brokered tool and explicit file
  grants. Frozen acceptance and controller policy stay outside their writable scope. Candidate code
  executes inside OS confinement. Read-only native policy and broker confinement are separate gates.
- **Replaceable model sessions.** Objectives, tasks, contracts, attempts, evidence and obligations
  belong to durable storage. Conversations are caches. Requested model names are not identity proof;
  preserve native thread/model/effort evidence and reject silent rerouting.
- **Honest uncertainty.** Missing/late/ambiguous usage remains recorded; cumulative streams are
  deduplicated and disjoint usage is unioned. Cached input is a subset of input; reasoning is a subset
  of output. Quota availability, accounting completeness and planning estimates are separate facts.
- **Scientific boundaries.** Code acceptance, dataset admission, evaluation and promotion remain
  separate. Paper outcomes do not authorize real execution; F01/D43/reserved holdout remain protected.

Do not replace this foundation with a distributed queue, a new repository per department or a general
agent framework merely to increase organizational appearance. The immediate need is complete
interfaces, one coherent lifecycle, and useful accepted work.

### Trust and recovery limits

The trusted computing base includes macOS, the owner account, Codex harness, interpreter, pinned
controller and acceptance policy. Hash manifests detect drift; they are not secure boot. Protection
against confined agent commands does not protect against an unconfined administrator under the same
owner UID. Platform runtime reads remain permitted. Process birth witnesses reduce PID-reuse risk;
Darwin signals lack an atomic pidfd equivalent. CPU, process and disk consumption are not hard OS
quotas. Host/storage loss remains a single failure domain.

Pilot STOP is a graceful dispatch pause, not an emergency kill. Already-authorized bounded work may
finish and submit; preserve this distinction from a separately designed emergency cancellation path.
Recovery must retain failed attempts, usage uncertainty, fences, pending intents and every independent
STOP reason. Quota restoration cannot clear a scientific, security, release or founder STOP.

## Selected operating design

Astra High is the episodic CEO for strategy and exceptional decisions and is never invoked
automatically. Luna-6 High is the sole implementation owner on `org/development`; Sol-6.1 High handles
substantial technical decisions and independent exact-candidate review. `brain/development` owns Brain
research work; `brain/linux` remains a separate unaccepted candidate; `research/active` is a frozen
compatibility alias, not a development lane; `main` remains unchanged. No standing idle model
supervisor or competing implementation roots.

Implement one trusted non-inference lifecycle owner with separate desired state, observed process
state and scheduling eligibility. It owns wake/retry/backoff/reconciliation and reasons for waiting;
models receive bounded work when evidence changes. Use native Goal support as a worker continuation
facility if qualified, not as the sole organization scheduler or a second competing authority.
Startup must be inert until release identity, STOP reasons, storage, quota, accounting and publisher
ownership reconcile. A model cannot authorize its own deployment upgrade.

The durable path must be:

`objective -> department backlog -> validated task -> frozen verifier binding -> admitted attempt
-> candidate -> exact merged verification -> integration receipt -> next eligible task`.

Persist one canonical task execution binding: objective/revision, base/integration policy, department,
scope, dependency and contract pins, risk, capabilities, resource/deadline bounds, independent acceptance
identity and immutable artifact references. Planner, worker, verifier and recovery must consume the
same binding. Separate proposed, admitted, running, awaiting verification, accepted and integrated
states; classify infrastructure pauses separately from implementation rejection.

Add generic producer/consumer contract proposals and acknowledgements, explicit version transitions,
conflict escalation and supersession. Detect dependency dead ends; continue unrelated work. Use a
bounded ready-task projection with durable dependency resolution rather than deleting historical tasks
to bypass the validator limit. Department plans and obligations need durable owners and review dates,
without requiring a permanently running conversation for each department.

### Brain department interfaces

These are responsibility boundaries, not a request to start five simultaneous teams. The existing
maximum two-team envelope remains until measured evidence and reviewed policy justify expansion.

| Department | Existing Brain boundary to retain | Missing organization binding / first suitable work |
|---|---|---|
| Senses | Immutable captures, source qualification, observation/support provenance | Versioned source contracts, coverage/failure denominators, synthetic decoder and acquisition-planner tasks; provider/writer access separately gated |
| Experience | `observations`, `schema`, partitioned snapshots and composition | Producer/consumer schema versions, compatibility tests, artifact pins and ownership; original EXP-001 is a real defect |
| Cortex | Owned `generations`, pinned recipe/snapshot/code and sealed artifacts | Registered experiment job, compute/storage lease, resumable artifact evidence and independent comparison; no training authorization in this audit |
| Evaluation | Preregistered protocols/populations, separate evaluation role, baseline/coverage metrics | Independent acceptance owner, negative-result retention, leakage/lineage checks and promotion separation; candidate authors cannot change the exam |
| Policy/Paper | Decision/observation separation and virtual ledger invariants | Versioned model-policy interface, replay/ledger contracts and isolated synthetic tests; no AUTO, production ledger or trading grants |
| Platform | Controller, storage binding, developer tools and release broker | Full task lifecycle, local release gate, recoverable checkpoint, telemetry and failure handling |

The repository already contains meaningful data, generation and evaluation interfaces. The company
needs adapters to those existing contracts, not another Brain rewrite. Research tasks additionally
need experiment declarations (hypothesis, input role/pins, baseline, metrics, budget, output ownership,
stop criteria), artifact publication and negative findings. A Git commit alone cannot accept a
scientific experiment. Large artifacts remain under explicitly bound research storage, with verified
volume identity and descriptor-bound live writes; there is no internal-storage fallback or legacy
`work/` writer configuration.

## Resource and productivity policy

The latest founder resource amendment remains
[CEO-20261020-02](evidence/brain-org-founder-quota-amendment-20261020.json), subject to the current audit
hold. It permits useful Sol/Luna work within actual subscription entitlement, not paid credits,
upgrades or a billable API fallback. Legacy 68/69%, 750k lifetime, 72-hour, 200-WPU and renewal-count
ceilings are historical and do not override that amendment.

Before dispatch and after completion read actual account limits; while active poll at most every
five minutes, at most every 60 seconds from 95%. At 97%, use one productive team and bounded short
turns; at 99%, pause productive inference with the default one-point recovery reserve. A measured
in-flight/granularity need may justify two points with recorded evidence. Provider denial or unknown
required entitlement fails closed. The existing watchdog does not fully implement active-turn policy
(ORG-A07). Two fresh successful readings at least 60 seconds apart plus state reconciliation are
required for quota recovery; predicted reset time alone grants nothing. This rule is currently a
helper, not a completed recovery service.

Keep reservations and bounded uncertainty for in-flight/unknown outcomes. Separate unknown model
attribution from unknown aggregate usage where independently justified, without inventing terminal
counters or cancelling debt. WPU is an internal planning proxy, not subscription consumption or a
bill. Track elapsed intervals, accepted useful tasks, time to acceptance, retries by cause, review
reversals, queue age, maintenance share, model usage and recovery latency. Do not equate tokens,
commits, passing component tests or time in Goal mode with useful delivery.

The founder supplied 14h28 elapsed, about 7h59 registered activity and 4h37 idle after HTTP 403.
These are about 55.2% and 31.9% of elapsed time respectively. The remaining 1h52 is unclassified by
those rounded observations; it must not be called extra idle time or productive work. The audit did
not independently reconstruct that full interval or prove the cause of 403. These figures show an
availability/recovery problem, not a controlled Luna-versus-Sol efficiency comparison. Historical
source repairs are real work, but pilot useful acceptance remains zero.

Reduce duplicate coordination through reusable versioned contract decisions, deterministic selection
when only one task is eligible, bounded failure classification and complete read-only review packets.
Use risk-scaled regression scope and one accountable independent acceptance owner. Rerun affected gates
when their inputs change; do not repeat entire unrelated review campaigns or weaken mandatory gates
to improve throughput statistics. After two rejections for the same root cause, require a compact
interface-level diagnosis before another implementation loop.

## Evidence gates for later authorization

1. **Small coherent release candidate.** Fix the startup/accounting/active-quota path (ORG-A01,
   A02, A03, A07) and put engineering checks in an explicit local release command (A08).
   Validate the exact assembled finite-pilot release in a disposable deployment with real local
   Git/SQLite, synthetic transport and frozen external acceptance. Test clean startup and old-state
   migration, terminal/late/ambiguous accounting, invalid messages, drift and STOP precedence.
   Require a complete recoverable checkpoint before mutating the original pilot. Unsupported dynamic
   admission and unattended service continuation remain explicitly disabled until their later gates;
   do not postpone the first bounded useful result for a general five-department framework.
   No worker starts merely because configuration hashes match.
2. **First real bounded loop.** After a separately reviewed STOP-preserving release and explicit
   restart authorization, recover the original pilot in the same database. Preserve all six failures.
   Independently accept and integrate EXP-001, CTX-001 and PLAT-001; demonstrate producer/consumer
   contract gating and unrelated progress. Qualify actual model identity, final JSON, tool execution,
   exact merged review and publication on that release. Then close ORG-A04: admit and complete a
   useful task absent from the original catalogue through the same trusted execution/verifier record.
3. **Failure and restoration.** Close lifecycle and restore gaps ORG-A05/A06. Inject loss of
   internet/provider access, model/tool failure, worker
   and controller death, duplicate/late responses and crashes at each publication boundary. Distinguish
   missing entitlement from quota reset. Restore a complete checkpoint into a fresh path with no model
   calls, STOP present and matching tasks, usage, receipts, Git reachability and pending intents.
   Demonstrate a reviewed release upgrade/rollback without replacing history. Test logout/reboot and
   storage loss separately; a Git bundle is not an organization backup.
4. **Measured endurance.** Qualify 24 hours, then 72 hours, then seven days on a pinned release with
   a real useful backlog, at least two department streams and cross-department dependencies. Require
   zero unverified integrations or unauthorized effects; no founder nudges for covered recoverable
   failures; complete classification of idle/blocked intervals; bounded unresolved usage and disk
   growth; at least one demonstrated recovery and useful accepted output after recovery. Sleeping
   safely with no admissible work is valid but not evidence of productive endurance. Do not fabricate
   work to satisfy a duration or task-count target. Expand concurrency only when accepted throughput
   improves within quality, host and resource limits.

A successful stage permits only its measured operating envelope. Multi-host, GPU, provider, dataset,
training, production/promotion and real-money capabilities require their own authorization and
qualification. Weeks/months of reliability cannot be inferred from one automatic Goal turn or a
successful unit fault harness.

## Source and historical custody

Current organization source combines metering `3733ce43`, validator `2b81ebbc`, additive Store
`a16d8b8a` (receipt `963a830`) and founder/Luna custody `afc6bea`. Source selection is verified in
[evidence/git-consolidation-2026-10-20.json](evidence/git-consolidation-2026-10-20.json).
Brain development is `brain/development`; stable main remains `2f8da5f`; `brain/linux` is isolated
and unqualified. `research/active` remains the frozen remote compatibility alias and the old private
publisher's ref. A future publisher lane binding needs a reviewed release, not a documentation edit.

Archive `archive/consolidation-20261020` preserves all original refs/pins and dirty organization
files. Its tree is custody only and must never be merged as source. Backup/restore `613c5e9` plus
its final dirty repair, UI reconstruction, DR v3 and unreviewed maintenance remain archived and
unaccepted. Preserve frozen pilot/owner checkouts, original SQLite/WAL and prior archive/handoff tags.

Earlier source-review, quota, transfer and pilot receipts remain immutable evidence for their exact
scope. Superseded execution narratives remain in Git history at the audited source commit. They do
not establish current Goal activity, current capability grants, or acceptance of a later composition.

A04 deadline checkpoint at `98f4ace`: `SandboxRunner` includes pinned executable and script hashing
in the command deadline, rejects matching initial or repeated Security identity observations that
return after expiry, and recomputes remaining time after a repeated observation before waiting on the
process. Expired prelaunch executable or script hashing prevents `Popen`; late normal completion is
classified as timed out. The independent A04 process-monitor reviewer accepted exact diff
`f3ed047e15fe4287b19fb63d1046ec34734b46c268b29064622c26a80b8346ff`. Python 3.13.6
`engineering-release-check` passed Ruff check/format on 58 files, 419 tests and 96 subtests. This is
fail-closed classification once synchronous Security/filesystem calls return, not a hard prompt-return
guarantee. A direct late-completion regression remains absent. Sampling still does not bind the
executed inode, descendants, loader closure or stdout provenance; plans remain
`execution_qualified=False` and full A04 remains open. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-deadline-review-20261021.json`
(SHA-256 `f96992390067dd843fdbdd6077674e811f2672219ac224fbe71511d9fe32f18c`).

A04 regression checkpoint `dacd393` directly covers the final late-completion branch: a controlled
monotonic clock lets the fake process return code zero after the deadline, and `SandboxResult` must
report `timed_out=True`. The independent process-monitor reviewer accepted exact test diff
`d426704b4eda1a8c069c5c2bb51398444c4d19a8f0ba06020d8ac1583ee18ed2`. The focused regression passed.
The first full gate had one intermittent existing `MacCodeIdentityTests.test_running_identity_survives_path_replacement`
`killpg` `PermissionError` (419 tests passed, 96 subtests); isolated rerun passed. Final Python 3.13.6
engineering release gate passed Ruff check/format on 58 files, 420 tests and 96 subtests. This
synthetic clock test proves classification only, not real process duration or cleanup. Synchronous
Security/filesystem calls remain uninterruptible; sampled identity, loader closure, descendants, stdout
provenance, deployment and full A04 remain open. Plans remain `execution_qualified=False`. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-late-completion-regression-20261021.json`
(SHA-256 `09b4a823acc077d626ae4b1bf93f7f38bf6ed3190303f3a797bfee6fc1ba3c3e`).

At source `90f99df`, manifest v5 binds each verifier command to its own canonical executable path,
file SHA-256, runtime main path and code-directory hash, optional trusted-script path/hash, and
declared result mode. The supervisor passes those pins to every `SandboxRunner` call and enforces
strict JSON contracts for every command that declares one. Manifest v4 still adapts a single command;
multi-command v4 fails closed without command pins. Synthetic tests exercise a later Python script,
a native command, identity argument forwarding, and rejection of an exit-zero command with a failed
JSON result before review or publication. Sol accepted exact six-file diff
`25088ae44b2ee84a73ccbbf8139eb7ab4d7bd5279bbb5f86e5340282714c6f36`; Python 3.13.6
`engineering-release-check` passed Ruff check/format, 433 tests and 101 subtests. Earlier full runs
had intermittent existing macOS identity/cleanup failures; the final full run passed, and the cleanup
failure cause remains unknown. This closes the gap where only command 0 received pins; it does not
bind execution to the hashed inode, continuously prove process identity, cover descendants, close
loader/import dependencies or establish stdout provenance. Sandbox/deployment qualification and full
A04 remain open, and spec-backed plans stay `execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-all-command-binding-review-20261021.json`
(SHA-256 `cadbff90b91805a92efeb1969ece19c50f572044378b2a7497b7fa64328e217f`). Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-all-command-binding-gate-20261021.json`
(SHA-256 `a31e33887732cb43d39a9edff83d6894f1d7236642b377c708f366e795bef098`).

At `f9dc873`, bounded manifest, harness and trusted-script reads compare descriptor device, inode,
mode, size, modification time and change time before and after reading, and reject metadata drift or
incomplete reads before hashing. Regression coverage forces a same-size overwrite during the read
and confirms stable oversized files retain the `too_large` classification. Sol accepted exact diff
`897da669b9c92904fe112d074bcf1157a83e953af5aef087041773f8ca65dd9d`; the focused suite passed
21 tests/29 subtests and the final engineering gate passed Ruff, 468 tests and 101 subtests. One
earlier full run hit the existing process-identity observation failure; the isolated test and final
full gate passed. This checks read-time descriptor stability only; it does not bind later invocation
to the verified inode or qualify loaders, sandbox behavior, output provenance, deployment, or A04.
Plans remain `execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-artifact-read-stability-review-20261021.json`
(SHA-256 `423e76fc1ed1dea94caa4ef60ed25876a3e85bec5f92ebc167fcc720a12bdaca`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-artifact-read-stability-gate-20261021.json`
(SHA-256 `bbfbe0f82fba028112e1d39d97f12990ea16a82c3ffe84c8c241df1e13713ab0`).

At the source candidate based on `1823233`, the legacy fixed-catalogue acceptance callback now uses
the same strict JSON result parser as manifest-bound verifiers. It requires schema
`org-pilot-acceptance-v1`, the exact task ID in `case`, and literal `true` in `passed`; duplicate
object keys and nonstandard numeric constants fail before independent reviewer dispatch. Tests cover
duplicate `passed`, `schema`, and `case` keys and `NaN`; the existing valid-completion path remains the
positive control. Sol accepted exact diff
`495d69c627c7eadddfa721d03e820fc743cb7aa5222e1d23cd5f2bdb4a6d1988` with no P1-P3 findings. The
focused suite passed 58 tests/10 subtests; the engineering release gate passed Ruff, 493 tests and
101 subtests. This removes parser ambiguity only; it does not establish stdout provenance, runtime
qualification or full A04. Plans remain `execution_qualified=False`. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-legacy-result-strict-json-review-20261021.json`
(SHA-256 `a1dccfe84057f48ed200a33433fecb7daa6a6aededd16948796d1d8a623e6d29`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a04-legacy-result-strict-json-gate-20261021.json`
(SHA-256 `6af24d8d09ab8d515200dcfabc7c87aa39e4c80ca8d1151092a6e3099ec9a1c7`).

At `7591be0`, contract-review replay bounds discovery to 1,000 distinct journals, separately from
the existing 100-response candidate limit. In one read snapshot it checks each retained version's
serialized byte length before hydrating any history: at most 512 KiB per version, 2 MiB per journal,
and 32 MiB for the inventory. A fifth-version schema bound rejects a sixth metadata row. Any
discovery or byte overflow returns unknown counts and no partial replay. Replay finalization repeats
the byte preflight after acquiring its write transaction, closing the gap between initial selection
and acknowledgement. Historical record/response readers retain their prior unrestricted behavior;
oversized legacy records remain present and readable there, while bounded replay overflows. Exact and
plus-one tests cover per-version, per-journal and inventory limits, global and transition scan limits,
and an interleaving journal enlargement before finalization. Independent review accepted exact diff
`6309a82fcd0fa7503ee69a6cd73a1233be9d4078bbc3bb6c5b5e55b31d04ea03`; the focused suite passed 54
tests and `engineering-release-check` passed Ruff, 441 tests and 101 subtests. These are serialized
history hydration limits, not hard limits on SQLite execution time, index work, or total process
memory. No provider or runtime authority was added; A03 settlement, broad A10 and operational
acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-bounds-review-20261021.json`
(SHA-256 `33b618a465b0d11dc34e5ae2da7bbcd1c1fd327e91efd5fff085bd0c004c2a47`);
gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replay-bounds-gate-20261021.json`
(SHA-256 `b2a9aefbe7cd7f31d9f9f9d33096384227c8e3c53119063d4c077bf50df64a5f`).

At `1f87739`, lifecycle owner-version continuity checks now use SQLite aggregates (`COUNT`, `MIN`,
`MAX` and stored-type validation) instead of materializing the full version list in Python. With the
existing unique `(name, version)` key, integer-only versions spanning `1..COUNT` establish the same
contiguity invariant. The write fence and read snapshot remain unchanged. Tests cover repeated renewals,
gap detection, empty history, malformed SQLite version type, and the cross-connection snapshot hook.
Sol-6.1 High accepted exact diff
`59c47b9589e18ab1d9030a5024123baeea332be551fc847fbce7004594249e7d`; focused lifecycle tests passed
28 tests/3 subtests and the final engineering gate passed Ruff, 444 tests and 101 subtests. The first
full gate had one transient existing macOS code-identity deadline mismatch; that test passed alone
and two subsequent full runs passed. This reduces Python-side memory only: SQLite still scans all
retained owner versions, so work remains O(N). A later owner-history check now validates the root
`replaces`/`replaces_version` chain in the same aggregate query without projecting raw links; owner
bodies and decision-event provenance remain unvalidated. No A05 authorization generation, consumer,
execution authority or runtime wiring was added. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-owner-history-head-review-20261021.json`
(SHA-256 `00b79e6c1fbfd3f9d3e5a7486048521ddbb4d4f488b8bc6c01213691e9b88be9`);
gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-owner-history-head-gate-20261021.json`
(SHA-256 `88ac386d694a08cffe670b33f63064401634e6ee8d2d6bf71f66deae99fe8c36`).

At `9d168e1`, global contract-review replay discovery now executes a shared SQL constant whose
regression test inspects `EXPLAIN QUERY PLAN` for a covering primary-key range on decision names.
Sol-6.1 High accepted the exact diff `4bc517f672af01c74383961d65721f38d7da98076d33a43ac3b13339bdf40af7`;
the focused transition suite passed 55 tests and `make engineering-release-check` passed Ruff,
445 tests and 101 subtests. This planner assertion is tied to the installed Python 3.13.6 / SQLite
3.53.4 environment and does not establish bounded query time, index work, or process memory. A03,
broad A10, and operational acceptance remain open; no provider, runtime, or service authority was
added. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-global-replay-query-plan-review-20261021.json`;
gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-global-replay-query-plan-gate-20261021.json`.

At `5c12b89`, global contract-review replay discovery replaces grouped namespace enumeration with
successive indexed name seeks. The inclusive first cursor and exclusive next cursor run within the
existing read snapshot; the explicit BINARY range preserves all `contract-review/` names, including
the malformed bare prefix so identity validation still fails closed. Discovery returns at most 1,001
names and checks overflow before hydration or replay. The regression verifies the covering primary-key
range and that the bound cursor register directly drives SQLite `SeekGE`/`SeekGT`; a synthetic journal
with 1,000 versions confirms the next distinct name is found. Sol-6.1 High accepted exact diff
`f7f35d7b1a426ce69cb269f2b8c72cd911aef3b0262a17580d1a964b4f169204`; the focused transition suite
passed 56 tests and `make engineering-release-check` passed Ruff, 446 tests and 101 subtests. This
bounds discovery calls and returned names, not B-tree work, SQLite time, or process memory. A03,
broad A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-global-replay-indexed-seek-review-20261021.json`
(SHA-256 `40bb3924f0849c607eb8dc792922aa22b6a40c7b42aa661b8985eeb952d7e5a4`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-global-replay-indexed-seek-gate-20261021.json`
(SHA-256 `3f646f8bb350099d1c5415c7688390de8a610e400f98a71929f4d929a3199a5c`).

At `e26fed1`, transition-scoped contract-review replay now finds distinct journals with successive
seeks over the existing partial expression index. Exact proposal predicates remain the index equality
prefix; the next-name cursor drives the indexed name range. Reads stay in one snapshot, matching
journals still undergo complete history/identity checks, and scan, byte, or candidate overflow still
returns before replay. Sol-6.1 High accepted exact diff
`d1e40736c046daf90212e799218e89469b495502d0fa56cd14f2e7088fb8f7cc`; the focused transition suite
passed 57 tests and `make engineering-release-check` passed Ruff, 447 tests and 101 subtests. A
synthetic 1,000-version journal confirms discovery proceeds to the next distinct name. This caps
calls/returned names but does not hard-bound expression-index work, SQLite time, or memory. Review
receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-transition-replay-indexed-seek-review-20261021.json`
(SHA-256 `cec854567d1f30c79de418bd415cdef8361912bffdc8eb456739a8ed3b81930d`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-transition-replay-indexed-seek-gate-20261021.json`
(SHA-256 `30ce4655892aa903eecdd3fd906cc3a96a0a8a23d97879b232d32705d73d43ea`).

At `274c380`, the advisory contract-review worklist preflights all selected journal versions and
serialized body sizes in its existing read snapshot before hydrating any JSON. The shared checks
enforce contiguous histories, 512 KiB per version, 2 MiB per journal, and a 32 MiB aggregate budget.
Byte overflow raises a worklist-specific error through the public API; quiescence returns an explicit
unknown-count, empty overflow projection. Direct legacy record reads remain compatible. Sol-6.1 High
accepted the exact source diff
`05d37c2d839c3de68e29b1c66e318b5d7a0e20beab004a9bedc3b61886c87b01`; the focused transition suite
passed 59 tests and the engineering release gate passed 451 tests/101 subtests with Ruff. SQLite
discovery/cast work, wall time, non-review quiescence hydration and total memory remain unbounded;
A03, provider/usage provenance, broad A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-byte-preflight-review-20261021.json`
(SHA-256 `cef13dcbc76bd7287c662e6102e0b259a1ac4ca8bd4910a410e79197e29dc10c`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-byte-preflight-gate-20261021.json`
(SHA-256 `ef21441eb37adee0721d66f5d22c4fdba48cca433227c1b04b7313d1835c5f5d`).

At `a2a313f`, the contract-review worklist reuses successive primary-key range seeks instead of
grouping retained decision versions. Discovery now makes at most `limit + 1` seeks and checks
overflow before identity validation, byte preflight or hydration; the shared BINARY range retains
the malformed bare namespace prefix for strict rejection. The read snapshot and the preceding
history/byte bounds are unchanged. Sol-6.1 High accepted exact diff
`8bd7dcac89b441a637a7c6b4082cf785981cb93da29fc64e7087c731a7d851fa`; the focused transition suite
passed 61 tests and the engineering release gate passed 453 tests/101 subtests with Ruff. This caps
seek count/name results, not B-tree work, SQLite time, name length, or currentness reads. A03, broad
A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-indexed-seek-review-20261021.json`
(SHA-256 `7a52e724acf9bc8c8a8a376f5bf903aa51555c35485645f513d58a1264f17911`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-indexed-seek-gate-20261021.json`
(SHA-256 `7bac1301055fc753f9602a441721bd7da84e8787cf5c263397d479b1e7ecec6d`).

At `7192e9d`, a test-only WAL interleaving regression verifies that contract-review history byte
preflight and JSON hydration share one SQLite read snapshot. A second connection commits a 600 KiB
legacy field after preflight begins; the hydrated worklist retains the old body, while a fresh read
sees the new field, and no acknowledgement is written. Sol-6.1 High accepted the exact test diff
`d4dcbdf3f53cce778753b1dd25faf3742f44aab66be103cc25aabfd62a57d7f4`; focused transition tests
passed 62 and the final engineering release gate passed 454 tests/101 subtests with Ruff. This is one
synthetic interleaving and does not change implementation behavior or prove exhaustive concurrency.
A03, broad A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-snapshot-review-20261021.json`
(SHA-256 `6a151359673b286bb30373f65eb0e744263699f1991180abcc373487dd640d00`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-worklist-snapshot-gate-20261021.json`
(SHA-256 `141b58d4cb879cb3dd50636b6879a3b8cea29ece3072b57ea0cc8fadec6cce8c`).

At `8f34074`, lifecycle request and contract-review journal seeks now project a key to Python only
when it has no embedded NUL, stays within the exact namespace identity character bound, and stays
within four storage bytes per allowed character. Oversized keys become SQL `NULL` and fail closed
before Python decodes the original text. This covers A05 lifecycle discovery and both global and
transition-scoped A10 review discovery. Regressions cover oversized ASCII names, embedded NUL,
malformed UTF-8 TEXT, and a valid UTF-16le Store database. Sol-6.1 High accepted exact diff
`1ae8db5841aa36ed47cea6a56617bbeb04e972c3086b72cb34c8e86f88bf7900`; focused suites passed 99 tests
and 3 subtests, and the final engineering release gate passed Ruff, 461 tests and 101 subtests. This
bounds host-side key materialization only; SQLite still processes the stored key and its scan, cast,
B-tree, time and internal memory costs remain unbounded. Small malformed text within the output bound
can still raise a SQLite decode error. Broad A05/A10, A03 and operational acceptance remain open.
Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-a10-bounded-journal-name-review-20261021.json`
(SHA-256 `891718da32ef20ce5358105ea9546e2664f9ab79eddd442f20a05ae50fcedd4c`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-a10-bounded-journal-name-gate-20261021.json`
(SHA-256 `4de7f0247e44be290ea586accf7347091ed5e378b351b6ea01864cf2866371fe`).

At the source candidate based on `bf64e3d`, A05 lifecycle request retry and worklist reads now require
the singleton version's `replaces` and `replaces_version` fields to both be NULL. SQL validates this
root-link condition without projecting the raw link values. Retry checks aggregate version continuity
and serialized body size before selecting or decoding the body; worklist keeps its aggregate byte
preflight and then selects only `name`, `version`, and `body` inside the same read snapshot. Regressions
cover a 2 MiB link, malformed UTF-8 links (with malformed body behind an invalid link on retry), an
oversized legacy body, and gapped version history. Sol accepted exact diff
`ba182fd800676899ecf66b05f05287dbcff269c65858b524540cae6c31f29968` with no P1-P3 findings. Focused
tests passed 38 tests/3 subtests; the engineering release gate passed Ruff, 489 tests and 101 subtests.
SQLite history scans and internal work/memory remain unbounded. The change adds no consumer or
authorization generation; A05 runtime and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-root-link-review-20261021.json`
(SHA-256 `e74ce3ff8b89a3714a0f79e15ed34c4c37bc5f4dc72c207328a6621a012a2fec`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-request-root-link-gate-20261021.json`
(SHA-256 `5dad98bdb42da7510ef2dead297a7c02c6caef06227614af39c64b3029f625f8`).

At `4a1d581`, controller-parse recovery history continuity and root/self-link validity are summarized
with SQL aggregates rather than materializing every version number into Python lists. Equal validated
counts still drive the full ordered circuit/obligation pair validation, and a 1,000-version regression
checks acceptance plus interior-gap rejection. Sol accepted exact diff
`cf95cd512a112c4eb4b477ead0bfc5d2da4fc036ad59f750c04228bb754a60ab`; Ruff and the engineering gate
passed 512 tests and 105 subtests. SQLite scans and per-version body reads still scale with retained
cycles; A05 has no consumer or authorization generation, and operational acceptance remains open.

At `b4f134e`, controller-parse recovery writes reject malformed resolution references and records
whose serialized SQLite storage bytes exceed 64 KiB before paired journal or task/event mutations.
History preflight rejects oversized retained bodies before JSON hydration, using the same database
encoding byte measure as the writer. Sol accepted exact diff
`9cb1955d29bdb359418fe98a9b12f15de767bcfd3a52db3bf467ba29055a4d06`; focused tests covered
no-mutation rejection and a UTF-16le open/resolve/history-validation round trip, and the engineering
release gate passed Ruff, 515 tests and 110 subtests. SQL scans, per-record serialization, and body
reads for records within the cap remain separate costs; historical oversized records fail closed.
A05 still has no lifecycle consumer or authorization generation, and operational acceptance remains
open.

Follow-up reserves the exact `circuit/controller-parse` and
`recovery-obligation/controller-parse` names from the generic `put_decision` API. Dedicated open and
resolve paths continue through the internal fenced writer; regressions cover generic bypass rejection
without version/event writes, allowed adjacent names, stale-version rejection and successful exact
resolution. Sol accepted exact diff
`02d8b8b7332c61a834cbb4367d32e97a21277de135a7afb526e351327051ba2b`; the engineering gate passed
Ruff, format, 550 tests and 123 subtests. Existing ambiguous historical records remain unchanged,
recovery references are not authenticated, and no consumer/runtime authority follows.

At `f44626d`, owner history validation also requires exactly one matching `decision_recorded` event
for every owner version and rejects orphan owner events. Owner and event bodies are preflighted at
64 KiB in SQLite storage bytes and decoded one at a time using the database encoding; event
timestamps are projected only for SQLite numeric storage classes. A partial index bounds matching
to owner decision events rather than scanning unrelated events. Sol accepted exact diff
`682ac0451e27c7dad2d3a6d9f83788ee39f1c00a35ef891bc489d5da915486d2`; regressions cover missing,
orphaned, mismatched and oversized events, malformed UTF-8 and oversized timestamps, plus UTF-16le.
The engineering release gate passed Ruff, 523 tests and 112 subtests. Two preceding full runs hit
existing macOS code-identity deadline/unavailable observations; both affected tests passed in
isolation and the subsequent full gate passed. First-time partial-index construction scans existing
events; each check still processes retained owner history. Event consistency does not establish
authenticity or historical lease-expiry proof. No lifecycle consumer or authorization generation
was added; operational acceptance remains open.

At `67058bf`, contract-review journal reads used at dispatch, session, response custody and
finalization now validate at most five versions and project each body only when its serialized
size is at most 512 KiB, in one SQLite statement before JSON hydration. All five append paths also
enforce the per-version 512 KiB and cumulative 2 MiB journal limits inside `BEGIN IMMEDIATE`, so a
successful write cannot create a version that its bounded reader later refuses. General historical
record and default response reads remain compatible with oversized legacy rows; explicitly bounded
replay still fails closed. Sol-6.1 High accepted exact diff
`54cac814657085affa5a56531ac98564fdbda4488cf8f2196c82feddbcc95696`; the focused transition suite
passed 70 tests and `make engineering-release-check` passed Ruff, 464 tests and 101 subtests. The
single query can project up to 3 MiB before count/aggregate rejection; SQLite internal memory/work,
`replaces` scalar bytes, and execution time remain unbounded. Provider/usage provenance, A03, broad
A10 and operational acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-journal-read-write-bounds-review-20261021.json`
(SHA-256 `0804f7893d5ce1e943cacac4826488e59a102a2aa5d261bdea448525280a7b84`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-journal-read-write-bounds-gate-20261021.json`
(SHA-256 `ced0bfadc372a6954a06ee0bdb0eab765ce08677a035cce6412c848d32422533`).

At `fb590e8`, the bounded review-history query also stops projecting arbitrary `replaces` values:
it returns only `NULL` or the exact expected journal key, and validates first/later-version link
shape in SQL before JSON hydration. Sol-6.1 High accepted exact diff
`1d2682ba79db7eb46a97f521d433b04c58ca14bbeab4e2da808fc8c3bfa3d7ad`; an independent matrix
covered NULL, valid and wrong keys, oversized text, embedded NUL, BLOB and malformed UTF-8, and
the engineering release gate passed Ruff, 465 tests and 101 subtests. SQLite still compares and
reads stored values internally, and the six-row projection can return 3 MiB before rejection;
unbounded historical readers retain their existing costs. Broad A10, A03 and operational
acceptance remain open. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replaces-projection-review-20261021.json`
(SHA-256 `7a425375c14660800fbec577f73e9af719c8376082757b0fe8693b76e6e59d99`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-replaces-projection-gate-20261021.json`
(SHA-256 `13577d088633f87645cf986b3a689acabd724134dcc169d3bcf37a1999138866`).

At `6cea96c`, the bounded record query computes a six-row window count and capped serialized-byte
sum before projecting bodies. Returned Python body bytes are at most 2 MiB; each returned body is
at most 512 KiB. An oversized individual version is projected as NULL and rejected before JSON
parsing; because the window sum caps each oversized row at 512 KiB plus one byte, other eligible
bodies may still be returned within the 2 MiB output ceiling. Sol-6.1 High accepted exact diff
`55b1729a84eb17b9a460a8ac4a1ada104f5d0eb76e90d7642b372d00a71251af`; independent boundaries
covered empty history, exact limits, plus-one overflow, oversized rows, and six/seven row histories.
The focused suite passed 72 tests. The first full gate encountered the existing
`MacCodeIdentityTests.test_running_identity_survives_path_replacement` observation-deadline flake;
the isolated test passed and final gate passed Ruff, 466 tests and 101 subtests. SQLite may still
materialize full bodies internally, so SQLite memory, I/O and execution time remain unbounded. This
source increment does not close broad A10, A03 or operational acceptance. Review receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-aggregate-projection-review-20261021.json`
(SHA-256 `d04e406e2cc24836e1122db376061fcbae321ddbd9ab2dbd675dba3d11f98c39`); gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-aggregate-projection-gate-20261021.json`
(SHA-256 `b3ff178f171e33deeb2f75c6abff6b0a294e90a9eb3f6740a7165947e7252435`).

At `8fd3eff`, configured active contracts without transition proposals now fail
closed except for initial v1 publication and the exact supported legacy `schema-validation` v2
publication (Experience to Platform). The legacy exception rejects orphan activation or participant
ACK decisions. Regressions cover generic v2 and `schema-validation` v3 without proposals, a modern
proposal deleted while ACK/activation evidence remains, and orphan evidence on the legacy identity.
Sol-6.1 reviewed exact diff `c591abd557d89596f84b2cf605d26a28a2a517fee43d779d06bcc458affd975d`
with no P1-P3 findings or merge blocker. Focused supervisor tests passed 52 tests/10 subtests; the
engineering release gate passed Ruff, 474 tests and 101 subtests. This checks selected publication
evidence keys, not a complete historical provenance audit or coherent-state proof. A03, broad A10
and operational acceptance remain open. Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-configured-contract-missing-transition-evidence-gate-20261021.json`
(SHA-256 `ef68283fb4aff15941cb5361db00d8978c34861202413dfbdf8844366c0670c1`).

At `00cf162`, configured publication identity compares canonical finite JSON rather than Python value
equality. This prevents `false`/`0` and `true`/`1` from collapsing into the same already-active v1
or supported legacy v2 contract, while preserving object key-order independence. Regressions verify
both drift cases reject without state changes and an exact reordered v1 body remains accepted. Sol
accepted exact diff `342b9eb2665bd77f8544ec972046b40d54cf8266046133c589e1ff0cceafac53`; Ruff and the
engineering gate passed 511 tests and 105 subtests. Configuration reads remain outside the proposal
write transaction; complete historical provenance, broad A10, A03 and operational acceptance remain
open.

At `a22b423`, configured contract proposals now validate as a set before persistence and are
inserted or exact-retried atomically by `Store.propose_contract_transitions`. The existing
single-proposal API uses this same transaction path. If a later configured entry fails validation,
supervisor creates no earlier proposal. Regressions cover late resolver rejection and rollback
after an earlier staged insert conflicts with immutable history. A lock-wait regression mutates
the input body, participant list and outer proposal list while the batch waits on a
second connection's SQLite write lock. Sol's read-only review accepted exact diff
`c656515f140f47eac42aee3f6761a32c1784934b3a559d2679fe7aa3d05e0a78` with no P1-P3 findings.
Focused tests passed 128 tests/10 subtests; the engineering release gate passed Ruff, 477 tests and
101 subtests. Configuration reads and `already_active` observations remain outside the write
transaction; review replay and activation remain separate. A03, broad A10 and operational
acceptance remain open. Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-configured-contract-proposal-batch-gate-20261021.json`
(SHA-256 `89fe703be05d5bb4796ff42ccc2fc1bf7c9a21b45b5f3ff91460796badf28e66`). A bounded follow-up
snapshots caller batch membership before enforcing a 1,000-proposal cap, limits serialized records
to 512 KiB each and 32 MiB per batch, and rejects oversized configurations before Store reads. Sol
accepted exact diff `b772097534c51c314ac86cf720a79aad919f86e8a8b77a50dfa6d0dc917ca1cc`; focused tests
passed 132 tests/10 subtests and the engineering gate passed Ruff, 481 tests/101 subtests. These
limits do not bound temporary serialization memory, SQLite work or retained history, and downstream
review capacity remains a separate bound. Gate receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-configured-contract-proposal-bounds-gate-20261021.json`
(SHA-256 `46f83f067334a1d141ee43d929a4f3e1b7174bcaa015e2bf8635dc21b4fc5d73`).

At `6919e8e`, supervisor contract reconciliation now validates plain JSON-compatible proposal and
participant types for the whole configured batch, then canonically detaches the complete mapping
before its first SQLite read. This prevents changes during later reads from mixing a proposal body,
producer identity/task and consumer list across two config versions. Regressions mutate both the
current and a later configured proposal during the first DB read and require both persisted proposals
to retain their original internally consistent bodies and participants; tuple-valued consumers and
non-finite values reject before DB reads without events. Sol-6.1 High accepted exact diff
`677e35bac0f7664ff5a728933adb77318b764304ea92de78a58280b27dacb395` with no P1-P3 findings. The
engineering release gate passed Ruff, 526 tests and 112 subtests. Input capture is not atomic against
mutation during serialization; serialization work remains unbounded, and config has no shared durable
generation authority with SQLite. Existing contract-head reads and `already_active` observations also
remain outside the proposal write transaction. This bounds input consistency after capture; it does
not close broad A10, A03 or operational acceptance.
At base `e7d09ab`, A10 now caps a newly recorded `thread_known` session journal body at 320 KiB,
reserving 96 KiB for exact response custody and 72 KiB for terminal results/blockers. The maximum
terminal version is 488 KiB and the five-version journal bound is 1,864 KiB under current limits.
An exact-boundary regression finalizes the largest supported raw/parsed result and maximally escaped
provider ID; a plus-one session is rejected without writes. Oversized legacy sessions fail closed
before further work, while an already returned fitting response remains locally custodiable. Sol
accepted exact diff `5373f24d9364196b740afb74adf7057278e818ecebb0b1db5d723b69701f9dc7`; focused tests
passed 80 and the full gate passed Ruff, 483 tests/101 subtests. This relies on the current response,
result and blocker bounds, does not retrofit old session capacity, and does not qualify provider or
runtime authority. Receipt:
`/Users/root1/Developer/brain-org-state/resumption-20261020/a10-contract-review-session-response-headroom-gate-20261021.json`
(SHA-256 `4669a7b63293a3418fad409b3acd1e9ae40dd9b167850b02b90f86f913769aaf`).

At the `7eda299` source base, `run_attempt` now validates the complete paired controller-parse
circuit/recovery history inside one read snapshot before verifier planning. Open recovery remains a
nonretryable stop; malformed, mismatched or deeply nested invalid journal JSON fails closed before
verifier, Git, sandbox or model work. Sol-6.1 accepted exact diff
`d029f6483feaf29b479e78e2aaf2c962659111eac6a789dfc35ef0f2318cefa9`; the engineering release gate
passed Ruff, format, 553 tests and 123 subtests. This is a point-in-time source guard; SQLite
operational errors still propagate, and recovery-reference provenance, lifecycle consumption,
authorization generation, full A05, A03 and operational acceptance remain open.

At `d670c17`, lead-result persistence is now fenced by the current attempt and atomically records
its lead decision with its exact contract acknowledgements. `run_attempt` uses this Store API after
usage settlement. The plan is detached before the write-lock wait; task/team identity comes from the
fence, and any invalid later ACK rolls back the lead decision and every earlier ACK in the batch.
Sol-6.1 accepted final diff `583b9a2e2ee46ad00732c1700b4f65b6378bb45a3910aa9fff1dd2ed6ae723d8`.
Focused supervisor tests passed 68 tests/10 subtests; the final engineering release gate passed
Ruff, format, 555 tests and 123 subtests. An initial full run hit the known transient macOS code
identity deadline race; its isolated rerun and the final full gate passed. This fences persistence at
commit only; direct trusted ACK calls remain, payload/transaction-time bounds and reviewer
provenance remain outside scope, and A03/release/operational acceptance remain open.

At the `200870b` source base, `SandboxRunner` now kills the verifier process group and closes both
captured streams on every post-launch exit path, including selector setup, selection and output-read
errors. This avoids the `Popen` context manager's unbounded wait when output collection raises.
Synthetic regressions cover each failure point and cleanup failure while preserving the original
error. Sol-6.1 accepted exact diff
`652f72acd610caaa82e1b5ca2e3ccaec5f426acf16db1f868f40cd842480df25`; `make
engineering-release-check` passed Ruff, format, 556 tests and 127 subtests. A failed OS-level kill
can still leave a process alive, Security API calls remain non-interruptible, and process/descendant
provenance and full A04 qualification remain open. A03 continues to block operational release only.

At `609e922`, `Store.open_controller_parse_fault` no longer leaves a later active faulting attempt
running when the parser circuit is already open. It preserves the first circuit/recovery records,
while fencing and atomically failing the current attempt/task with its own failure event. The
regression checks two active attempts, both failure records, retained usage, unchanged first-fault
versions and stale duplicate rejection. Sol-6.1 accepted exact diff
`0000787d1d13c17471651fb73b120b99f82ff14046943ee8f2814d7a955ccfe0`; `make
engineering-release-check` passed Ruff, format, 557 tests and 127 subtests. This does not cancel all
other active work or add recovery scheduling, execution or runtime authority; A03 remains an
operational-release blocker only.

At `7e0668f`, `SandboxRunner.run` snapshots caller `argv` as an immutable tuple before validating
and uses the same command for pin checks, process launch and `SandboxResult`. A synthetic regression
mutates the caller's list during executable hashing and verifies the launched command retains the
validated script and arguments. Sol-6.1 accepted exact diff
`fe2e69d3b35ebb12bf6593646cc14b11dacac06d6a3a6e1989613c4832c086f1`; the full gate passed Ruff,
format, 558 tests and 127 subtests. This closes post-capture caller-list mutation only; filesystem
replacement, output provenance and full A04 qualification remain open. No runtime authority is added.
