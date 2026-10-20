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
| ORG-A01 | P1 | `bootstrap.py` writes `codex_sha256`; `service.validate_deployment` requires `codex_executable_sha256`. Real bootstrap construction with synthetic qualification/Git effects produces a deployment rejected as `codex_executable_binding`. The test deployment fixture uses the other field and misses the interface mismatch. |
| ORG-A02 | P1 | Bootstrap does not initialize `usage-ledger.sqlite`. `measured_usage` reports `ledger_missing`; `choose_capacity` returns zero slots even with valid fresh quota and ready tasks. A first metered worker would create the ledger, but normal scheduler admission blocks that worker. A reviewed migration/initialization path is missing. |
| ORG-A03 | P1 | The operational adapter always returns `usage_terminal=False`; progress counters are deliberately unattributed. `metered_client` sets `terminal_usage_qualified=False`, and there is no operational caller of `reconcile_dispatch`. A completed synthetic turn retained its reservation and `model_attribution_unverified`; scheduler capacity became zero. Correct conservative accounting has no qualified operational settlement path. Do not fix this by assuming unknown usage is zero. |
| ORG-A04 | P1 | `Store.admit_task_spec` atomically persists a valid spec, but planner uses fixed cards and verifier resolves `cfg['tasks'][id]['checks']` plus pilot-only acceptance. A Store-admitted PLAT-010 reaches submitted state and `verify_one` raises `KeyError` before its guarded verifier block. Spec capabilities, deadline and acceptance references are not an executable trusted task binding. |
| ORG-A05 | P1 | Successful supervisor exit after its bounded episode is not restarted by the generated `KeepAlive={'SuccessfulExit': False}` plist. Quiescence writes a Sol handoff and exits; no qualified consumer of that handoff exists. `reset_recovery_decision` has no operational caller. Chat Goal continuation and the frozen pilot are separate control paths, not one qualified lifetime service. |
| ORG-A06 | P1 | Current `backup_snapshot` omits STOP, usage ledger, Git objects/refs, integration intents, attempts and operation logs. A synthetic snapshot reproduced all omissions and failed deployment validation at a different path. There is no accepted full restore/upgrade path in this source. Archived repair is not accepted implementation. |
| ORG-A07 | P1 | The quota watchdog calls `refresh` but ignores a valid zero-slot decision. Fresh 99% evidence returned `reserve_threshold` without cancelling the active permit. New sends are blocked, but active work can continue to its deadline. The release must define and test active-turn drain/cancellation and bounded overshoot; no provider billing cap is proven. |
| ORG-A08 | P1 | `pyproject.toml` default test discovery targets `tests`, and `make check` lints `src tests tools`; `engineering/` is outside both. Hosted workflows call those targets and use Ubuntu. Fixing the historical CI account block alone would not provide controller/native release acceptance. |
| ORG-A09 | P2 | Infrastructure, parser, provider and task failures largely share the attempt retry path. The six lead parser failures consumed both original tasks' retry allowances in about 41 seconds between first and last failure. A repeated deterministic controller fault needs a circuit breaker and a separate recovery obligation, not fresh implementation attempts. |
| ORG-A10 | P2 | Runtime contracts remain special-cased to EXP-001, schema-validation v2, Experience and Platform. Department names are task labels, not durable department plans/leases, generic contract migration, conflict adjudication or a research experiment service. |
| ORG-A11 | P2 | Every attempt creates a Sol lead and Luna worker; every candidate gets a Sol review. The reviewer receives at most 50,000 diff characters and cannot inspect files. Fixed catalogue selection can spend a Sol episode to choose one eligible card. General risk-aware routing, reusable contract decisions and complete review packets are missing. Independent verification must remain mandatory. |
| ORG-A12 | P2 | Store passes all historical tasks to a validator with a 1,024-element JSON list bound; 1,025 rows fail before semantic validation. No completed-history projection exists. Full event/usage scans, raw-RPC persistence and accumulating candidate checkouts also need retention/capacity policy. Multi-month scale is unmeasured. |

The bootstrap and scheduler failures predate consolidation: their source bytes match the selected
validator/metering lineage. Sol preserved source custody accurately; that does not certify the
interfaces between independently approved components. `org/development` is a valid development
baseline and an unsuitable operational release.

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
