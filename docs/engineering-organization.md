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
The audit changed documentation and evidence only. It did not repair controller source, resume a
Goal, dispatch a worker, install/start a service, clear pilot STOP, run training, access protected
datasets, request market data, or authorize publication. **The founder must explicitly resume the
next implementation stage.** The gates below are selected strategy, not a restart authorization.

The [audit receipt](evidence/brain-org-independent-audit-2026-10-20.json) binds source identities,
local reproductions, test results and limits. Private scripts and raw results are under
`/Users/root1/Developer/brain-org-state/audit-20261020-independent/`. Original consolidation custody
and pilot state remain intact. The [handoff](../engineering/SOL-MANDATE.md) owns the resumption entry.

### Founder resumption and technical program — 2026-10-20

The founder subsequently authorized autonomous **engineering development**. This does not authorize
operational release, original pilot dispatch, removal of STOP, market collection, training, AUTO,
trading, protected-data access, production or `main` changes, paid resources, or automatic Astra.
The native implementation owner is the existing thread `01a15657-3558-79c1-86dc-6ed033c264df`
(`gpt-6-luna/high`); preserve its prior Goal, counters, checkpoints and all six pilot failures. The
current Astra audit thread has no Goal. Resume only the existing Luna owner after the Sol plan and
handoff are durable; do not create a competing coordinator or worker team.

Independent Sol technical lead `01a15a5e-7fc3-7bf0-9a10-73199636bfcd`
(`gpt-6.1-sol/high`) verified the current checkout, selected private evidence hashes, pilot STOP,
configuration, manifest and SQLite against the audit. It made no source or pilot changes. Its
ranked program is:

1. **Settle the usage evidence contract first.** Sol owns source feasibility and semantics; Luna owns
   a bounded implementation only after the evidence source is established. Bind provider evidence
   to dispatch, native thread/turn, model and attempt epoch; define baseline, terminal and late-event
   handling. Prove duplicate, missing, conflicting, wrong-turn, restart and model-ambiguity cases.
   Unknown usage remains reserved. If the native interface cannot provide sufficient evidence,
   document the exact limit and keep the release blocked; `turn/completed` alone is not settlement.
2. **Build one finite STOP-preserving release candidate:** ORG-A01/A02/A03/A07/A08 and the minimal
   ORG-A09 deterministic-controller circuit breaker needed before any task replay. Fix real bootstrap
   to pass real preflight, use versioned non-destructive ledger initialization, settle only qualified
   usage, stop active permits within a stated overshoot bound on fresh zero capacity, and include all
   engineering checks in one explicit local release command. Dynamic task admission and unattended
   continuation remain disabled in this first candidate. Luna is the sole implementation owner;
   Sol reviews the exact candidate.
3. **Checkpoint and release acceptance.** Before changing the historical pilot, prove a complete
   checkpoint and fresh-path restore, including STOP/reasons, policy/config/native binding, both
   databases, Git refs/objects, intents, attempts, receipts and recovery records. Verify checksum-bound
   manifest, STOP-preserving upgrade/rollback and rejection of incomplete snapshots. This is source
   and release evidence only; original pilot dispatch still needs separate founder authorization.
4. **First useful pilot cycle, only after that authorization.** Preserve all six failures and use the
   same pilot database. Independently accept/integrate EXP-001, CTX-001 and PLAT-001 through the
   released controller; prove native model identity, final JSON, brokered tool execution, usage
   settlement, exact merged-candidate review and one-time publication. A deterministic parser or
   controller fault must create a bounded recovery obligation/circuit break, not burn implementation
   retries. Then close ORG-A04 with one useful task outside the fixed catalogue through the same
   immutable trusted execution/verifier binding.
5. **Lifecycle, restore, departments and endurance.** Close ORG-A05/A06 and remaining A09 before
   unattended continuation. Then close A10-A12 with durable department plans/contracts, full review
   packets and bounded history projections. Qualify reboot/storage-loss separately and only then
   run 24 hours, 72 hours and seven days with useful work across at least two independent streams.
   Measure accepted throughput, idle causes, recovery latency, unknown usage and disk growth; expand
   concurrency only when measured accepted throughput improves.

The source candidate, merged-system acceptance, operational release, and founder authority to resume
the original pilot are distinct gates. Passing synthetic tests or the local release command alone
does not pass them.

### Terminal usage evidence source qualification — 2026-10-20

Read-only generation of the app-server JSON schema from pinned Codex CLI `0.163.0` confirms that
`thread/tokenUsage/updated` carries `threadId`, `turnId`, and `last`/`total` token counters. The
notification schema has no terminal/finality field. In the current RPC adapter, matching updates are
persisted as evidence but treated as progress; `turn/completed` returns `usage_terminal=false` and no
usage delta. `account/usage/read` can report estimated account or thread usage, but the response does
not bind counters to a dispatch, turn, or attempt epoch. No live model turn or account usage request
was made. Sol independently accepted the receipt only as static schema/source correlation and
rejected terminal-settlement qualification: the identifiers do not prove same-epoch dispatch/model
attribution, finality, event ordering, completeness, or late-correction handling. The release remains
blocked until an authoritative finality contract and these bindings are established. Unknown usage
remains reserved.

Sol also identified a conditional parser compatibility gap in `metered_codex.capture`: the generated
schema permits an optional `cacheWriteInputTokens` counter, but canonicalization currently requires
the counter object to contain exactly the five other known keys. When the optional sixth key is
present, the raw receipt is retained while the canonical progressive observation is dropped. Live
payload frequency and cache-write pricing semantics were not established. The current unreviewed
source checkpoint accepts the optional field only as a nonnegative integer, keeps it in raw evidence,
and does not add it to or subtract it from the five canonical counters. The existing invariant
`totalTokens = inputTokens + outputTokens` remains required. Focused synthetic five- and six-field
cases were added; pytest/Ruff are unavailable in this checkout, while syntax compilation and direct
execution of those cases passed. This is parsing/retention only, not terminal settlement or
cache-write pricing qualification.

The independent review is recorded in native Sol thread
`01a15a5e-7fc3-7bf0-9a10-73199636bfcd`.

The immutable read-only schema/source receipt is stored outside Git at
`/Users/root1/Developer/brain-org-state/resumption-20261020/terminal-usage-source-qualification-20261020.json`
(SHA-256 `a29a219a65650aaee8f048dbbd54e142fd5cc4d32a1dd3544ed8b54a17298422`).

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
| ORG-A01 | P1 | **Candidate fix, unreviewed:** bootstrap now writes `codex_executable_sha256`, matching `service.validate_deployment`. Prior synthetic bootstrap evidence reproduced the rejection as `codex_executable_binding`; the exact candidate still needs release checks and Sol review. |
| ORG-A02 | P1 | **Candidate fix, unreviewed:** bootstrap initializes the ledger only after both qualification steps, before creating organization state. Schema creation, legacy column additions and `user_version=1` commit together. Existing version-1 ledgers must match exact ordered columns, types, nullability, primary keys, grouped composite foreign keys and unique indexes; newer, partial, extra-table and unknown schemas fail closed. A split-FK regression test rejects two single-column constraints in place of the required composite key. |
| ORG-A03 | P1 | The operational adapter always returns `usage_terminal=False`; progress counters are deliberately unattributed. `metered_client` sets `terminal_usage_qualified=False`, and there is no operational caller of `reconcile_dispatch`. A completed synthetic turn retained its reservation and `model_attribution_unverified`; scheduler capacity became zero. Correct conservative accounting has no qualified operational settlement path. Do not fix this by assuming unknown usage is zero. |
| ORG-A04 | P1 | `Store.admit_task_spec` atomically persists a valid spec, but planner uses fixed cards and verifier resolves `cfg['tasks'][id]['checks']` plus pilot-only acceptance. A Store-admitted PLAT-010 reaches submitted state and `verify_one` raises `KeyError` before its guarded verifier block. Spec capabilities, deadline and acceptance references are not an executable trusted task binding. |
| ORG-A05 | P1 | Successful supervisor exit after its bounded episode is not restarted by the generated `KeepAlive={'SuccessfulExit': False}` plist. Quiescence writes a Sol handoff and exits; no qualified consumer of that handoff exists. `reset_recovery_decision` has no operational caller. Chat Goal continuation and the frozen pilot are separate control paths, not one qualified lifetime service. |
| ORG-A06 | P1 | **Candidate implementation, unreviewed:** snapshot v2 copies the private state tree, backs up both SQLite databases, requires exact organization column/type/nullability/default/PK/FK/unique signatures, checks FK data integrity and no live attempts, and verifies the integration repository offline with sanitized Git configuration while rejecting alternates, shallow repositories and promisor remotes. It retains STOP, intents, attempts, logs and Git objects/refs; holds the supervisor lock; canonicalizes destination containment; and restores only to a fresh path. Synthetic restore after moving the source copy rebinds bundled executables and permits unavailable host executables only behind the barrier. Restore receipts say `runtime_ready=false`. Host qualification and operational/pilot restore remain open. |
| ORG-A07 | P1 | **Candidate fix, unreviewed:** the quota watchdog now cancels active work for explicit `stop`, `quota_critical`, or `reserve_threshold` decisions. A zero count of additional admission slots alone does not cancel a permitted active team. Synthetic threshold and high-quota continuation probes passed. Drain timing, bounded overshoot and provider billing remain unqualified. |
| ORG-A08 | P1 | **Candidate fix, unreviewed:** `make engineering-release-check` explicitly runs Ruff lint/format and pytest over `engineering/`. The command was not runnable in this checkout because its pinned development tools are absent. Hosted workflows and native controller acceptance remain separate. |
| ORG-A09 | P2 | **Candidate fix, unreviewed:** malformed or wrong-shaped lead/worker output opens a durable circuit and recovery obligation in one lease-fenced SQLite transaction that also fails the active attempt. Queued attempts read the decision body and stop while the circuit is open. Recovery uses one transaction bound to both current decision versions and requires a qualified fix, independent release review and separate bounded authorization. Output object fields, enums and list element types are checked before submit. The old six-attempt pilot history is unchanged. |
| ORG-A10 | P2 | Runtime contracts remain special-cased to EXP-001, schema-validation v2, Experience and Platform. Department names are task labels, not durable department plans/leases, generic contract migration, conflict adjudication or a research experiment service. |
| ORG-A11 | P2 | Every attempt creates a Sol lead and Luna worker; every candidate gets a Sol review. The reviewer receives at most 50,000 diff characters and cannot inspect files. Fixed catalogue selection can spend a Sol episode to choose one eligible card. General risk-aware routing, reusable contract decisions and complete review packets are missing. Independent verification must remain mandatory. |
| ORG-A12 | P2 | Store passes all historical tasks to a validator with a 1,024-element JSON list bound; 1,025 rows fail before semantic validation. No completed-history projection exists. Full event/usage scans, raw-RPC persistence and accumulating candidate checkouts also need retention/capacity policy. Multi-month scale is unmeasured. |

The bootstrap and scheduler failures predate consolidation: their source bytes match the selected
validator/metering lineage. Sol preserved source custody accurately; that does not certify the
interfaces between independently approved components. `org/development` is a valid development
baseline and an unsuitable operational release.

The current source candidate also adds optional cache-write counter retention (see the terminal
usage review above), bootstrap/ledger wiring, quota cancellation, a controller parser circuit, a
full private snapshot/restore path, and an explicit engineering check target. Sol accepted the
`f73e030` A01/A07 findings and A02 bootstrap ordering, but rejected A02 schema compatibility, A09
wrong-shaped output validation, and full release qualification. A follow-up found and repaired split
foreign-key acceptance, source-path executable dependency, backup destination containment, unowned
destination cleanup, delayed restore barrier, missing Git object validation, and insufficient
synthetic organization database schema. The exact `81aa8eb` review accepted the covered schema,
Git, restore and quota fixes, but found that the structural-validation API could be called without
the persisted requalification flag. The uncommitted follow-up now requires that flag whenever
relaxed host validation is requested. This exact follow-up remains unreviewed and is not
release-qualified. ORG-A03 settlement remains fail-closed; unknown and late usage still retain
reservations. Restore remains structurally verified only and requires host requalification.

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

Astra owns strategy, objective/capability expansion and exceptional unresolved decisions in short
founder-invited episodes. Luna is the single implementation owner after explicit resumption; Sol
owns difficult architecture and independent exact-candidate verification. No automatic Astra,
standing idle model supervisor or competing implementation roots.

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
