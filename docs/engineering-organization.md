# Brain Engineering Organization

Canonical owner of the engineering control system, distinct from the live Brain operator.
The founder directive of 2026-10-19 appoints Astra High CEO / Engineering Director; Sol High
leads departments and Luna High implements bounded tasks. The [Founder Desk](../engineering/OWNER-DESK.md)
is Russian by explicit request. Research contracts remain in `docs/contracts.md`.

## Authority and implementation

`engineering/` is a stdlib controller, not a trading runtime. Independent Codex app-server root
threads provide episodic Astra (`gpt-6-astra`, high), Sol leads/planning/review (`gpt-6.1-sol`, high)
and Luna execution (`gpt-6-luna`, high). There is no parent conversation dependency. No model has
publisher or controller-write tools. Codex0.163.0 provides threads, turns, dynamic tools, usage,
rate-limit reads and native permission profiles; durable scheduling, fencing, recovery, contracts,
acceptance, publication and the Founder Desk are implemented here. Native chat resume exists but
is not the recovery guarantee: replacement reconstructs a compact packet in a fresh root thread.

Private deployment state contains SQLite WAL/FULL, append-only events, immutable config and code
hash manifest, pinned acceptance scripts, isolated candidate checkouts, verification receipts,
integration intents, process birth witnesses, and a generated `OWNER-DESK.md`. Store transactions
serialize ownership. Attempts renew leases independently of supervisor; expired attempts lose the
right to submit. Epoch-fenced acceptance/integration binds one candidate. Execution can repeat after
failure; acceptance/integration is idempotent. SQLite is a single-host coordinator, not a distributed
consensus system. The host/storage remain a failure domain.

An Astra role lease is independent of worker leases. Decisions, obligations, requests and their
history survive replacement. Requests never grant authority or globally block scheduling. Short CEO
episodes review durable evidence; they end rather than burn idle turns. Sol may select the next
justified task from an executive-approved immutable catalogue. Admission supplies fixed scope,
dependencies and independent checks; generated proposals cannot supply executable policy. Expansion
beyond this envelope requires a reviewed controller release, which is an engineering duty rather
than a routine founder approval. Exhaustion opens the next-policy-horizon obligation.

The first envelope has Experience schema validation and Platform context/CLI work. Contract v2
requires producer and consumer acknowledgements of the same proposal hash, then producer integration.
The CLI is blocked until those conditions hold; unrelated Platform context work remains runnable.
This is bounded continuity, not evidence of indefinite task invention or months of operation.

## Isolation and gates

All model-generated commands pass through a default-deny macOS Seatbelt runner: readonly checkout,
write grants only to task subdirectories and scratch, sanitized environment, no network or IPC,
no owner home, protected evidence, runtime, credentials, controller, policy or publisher access.
Readonly verification imports candidate code inside the same OS boundary. Its frozen independent
acceptance harness is outside candidate scope, checks completion, then focused regressions and a
separate Sol review must pass on the exact merged SHA. Candidate scope, modes, symlinks, protected
paths, source ancestry, cleanliness and receipts are enforced by `GitGate`.

External MCP/plugins/hooks/apps and several native execution features are disabled; native tools
are not literally absent. The final catalog still included readonly-denied apply_patch, clock and
resource helpers, plus brokered org_exec through the required code host. The probe saw only internal
node_repl and zero exposed resources. Actual unauthorized native writing was rejected. Empirical
qualification found `:minimal` implicitly granted shared/per-user temp access; explicit deny overlays
close those paths. Positive task reads and negative sibling/home/temp/symlink/network checks passed
on the tested binary. Platform runtime reads remain allowed; this is not a claim that every byte
outside cwd is inaccessible. The custom runner has separate actual read/write/link/socket/signal/
publication probes. Full unattended Sol maintainer behavior remains unqualified.

The trusted computing base includes macOS, Codex harness, interpreter, this pinned controller and
the owner account. This protects against confined agent commands, not an administrator or an
unconfined hostile process under the owner UID. Manifest checks detect drift after trusted startup;
they are not secure boot. Policy tests are not a proof against a malicious interpreter exploit.
Process recovery uses kernel microsecond birth witnesses and rechecks ownership/group before kill;
Darwin lacks an atomic pidfd-equivalent signal. Detached descendants retain the sandbox but process
counts/CPU/memory/disk quotas are not hard OS limits. These limitations must remain explicit.

The publisher holds only trusted Git authority. It serializes local `research/active` CAS updates
and reconciles write-ahead integration intent after crashes. A changed active tip requires a newly
merged candidate and new verification. `main` is outside this first release authority. Before any
remote active movement, preserve old `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` under immutable
`archive/research-active-pre-org-20261019`, verify remote ancestry and unchanged main, then normal
fast-forward publication with fixed remote/ref and receipt. Never force push. GitHub protection and
hosted CI are not trusted gates. Remote automatic publication remains disabled until local trial
and independent security/publication qualification are evidenced.

## Resource management and evidence

Current resource authority is the explicit founder amendment
[CEO-20261020-02](evidence/brain-org-founder-quota-amendment-20261020.json), superseding the resource
ceilings of CEO-20261020-01. The founder allocates essentially all remaining weekly subscription quota
to useful Sol/Luna engineering and authorizes qualified continuation after account access is restored.
The existing Sol root `01a15657-3558-79c1-86dc-6ed033c264df` owns implementation; no competing team.
Its supported Pursuing Goal is active for the full sustained mandate, not just the accounting repair.
The prior blocked root remains a predecessor record, not a falsely resumed goal. The exact API receipt
and current owner/worker/next-gate projection are private under
`/Users/root1/Developer/brain-org-state/founder-quota-20261020/` as
`continuation-goal-receipt.json` and `sol-live-status.json`. Independent review rejected candidate
`0d74d69` for three accounting/recovery defects; bounded actual Luna6 High repair is active. This is
source work, not operational acceptance or unattended-service qualification. No automatic Astra.

Actual entitlement and applicable account-limit telemetry govern availability. Productive inference
pauses at99% with a default1-percentage-point checkpoint/recovery reserve; at97% use one productive
team and short bounded turns. A measured in-flight/granularity requirement may justify at most2 points
of reserve, recorded by Sol. The former68/69% controls and1-point/15-minute automatic STOP are revoked.
Read limits before dispatch and after completion, every5 minutes while active, and at most60 seconds
once a bucket reaches95%. Provider denial or a reached limit stops inference; optional absent buckets
are distinguished from missing required evidence. No paid credits, upgrades or billable API fallback.

WPU, raw tokens and task-duration estimates remain efficiency/accounting tools. The72-hour hard expiry,
200-WPU cumulative cap, five-renewal cap and fixed20-WPU/2-hour repair expiry are superseded. Sol records
routine scope/progress/quota reviews and may renew planning allocations without Astra. Bounded turns,
finite attempts, leases, reservations and independent code/release acceptance remain mandatory.
Review ongoing strategy at least daily; do not create work merely to consume available quota.

Keep per-agent/task/model cumulative snapshots and incremental deltas, deduplicate overlapping sources
and union disjoint usage. Cached input is within input; reasoning is within output. Preserve all old
receipts, initial T0, failures and unknown/late usage. Unknown historical totals are not zero or newly
charged debt; current in-flight uncertainty must be bounded. Fresh actual account access, rather than
an arbitrary historical WPU reserve, establishes subscription availability.

At quota pause, preserve edits, commits, attempts, leases, usage and integration intents. Qualified
non-inference polling may await restoration; a wall-clock reset estimate alone grants no authority.
After two fresh successful account reads at least60 seconds apart confirm access and restored capacity,
Sol may reconcile state and resume the same objective through qualified recovery without CEO renewal.
Reset changes the quota window, never accounting history. Keep resource pause separate from other STOP
reasons; no safety/release STOP is cleared merely by a quota reset. No idle model polling or automatic
Astra. Existing concurrency, security, scientific, protected-data, acquisition, training, trading, Git
and publication restrictions remain. The frozen pilot config remains unchanged. Source work in an
isolated candidate does not authorize operational resumption; preserve STOP until an independently
accepted, controlled release passes its gates.

The following initial policy is historical and remains in the frozen old deployment until reviewed
replacement; it is not an alternative current resource authorization.


The initial envelope permits at most two concurrent teams, measured token admission budget750k,
60k reserve, known ordinary quota permission and all exposed buckets below90% utilization. Near the
ceiling, parallelism reduces. Memory/CPU and overlapping file scopes further constrain dispatch.
Count chief, planner, lead, worker and verifier usage; persist partial usage notifications so failed
attempts do not disappear. Reservations and bounded turn deadlines constrain admission; missing final
usage and tokens already in flight mean this is not a strict monetary or instantaneous token cap.
A model turn has an additional measured180k ceiling. Unknown quota fails closed.

Measure accepted useful changes, verified invariants, time to acceptance, rework/retry rates, blocked
versus independent progress, model usage and recovery time. Do not score productivity by lines of
orchestration code. The offline pilot can improve schema and tooling reliability; it cannot establish
forecast accuracy, edge, execution quality or financial returns. Broad historical repo checks have
known failures; scoped green must never be represented as whole-project green.

## Reproduction and operation

Use the existing dev Python3.13 environment and installed Codex0.163.0 authentication. No credentials
belong in source, model packets or Founder Desk. From the implementation checkout:

```sh
/Users/root1/Developer/memecoin-research/.venv/bin/python -m pytest -q engineering/tests
/Users/root1/Developer/memecoin-research/.venv/bin/python -m ruff check engineering
/Users/root1/Developer/memecoin-research/.venv/bin/python -m engineering bootstrap \
  --state /Users/root1/Developer/brain-org-state/pilot-20261019 \
  --source /Users/root1/Developer/brain-org-build \
  --python /Users/root1/Developer/memecoin-research/.venv/bin/python
```

Bootstrap is create-only: qualify OS/native boundaries, copy pinned controller+acceptance, clone
source, preserve historical active locally and seed objectives/tasks. Run the frozen controller
from STATE/controller, never from a worker checkout:

```sh
python -m engineering chief --state STATE
python -m engineering supervise --state STATE --max-seconds 14400
python -m engineering status --state STATE
python -m engineering desk --state STATE
python -m engineering stop --state STATE
```

Use the explicit dev interpreter above in place of `python`. STOP prevents new dispatch/CEO episodes;
already-authorized bounded workers may finish and persist submission. It is a graceful pause, not
an emergency kill. A restart reconstructs leases/intent; do not delete the SQLite WAL or overwrite
state. Snapshot SQLite through its backup API and retain config/controller/receipts/intents together.
Original research/runtime/data locations are never part of an organization cleanup.

Acceptance must include actual overlapping Sol/Luna root sessions; interrupted chief and worker;
supervisor kill/restart; stale submit/duplicate accept rejection; automatic contract gating and an
unrelated team's progress; independently verified merged commits; unauthorized file and publication
attempts; and one subsequent admitted task. Unit/fault simulations and actual model/OS evidence are
reported separately. Physical reboot, prolonged unattended endurance, external backup restore and
cross-machine recovery remain unqualified until those particular trials occur.

## Final architecture handoff — 2026-10-19

The founder ended the Astra architecture sprint and delegated the next implementation horizon to
an independent root Sol6.1 High with Luna6 High workers. The self-contained
[Sol mandate](../engineering/SOL-MANDATE.md) owns this handoff. Astra must not remain active overnight,
be polled by the organization, or be an automatic routine escalation target. The current frozen
supervisor can invoke Astra when work becomes quiescent; STOP must remain until a reviewed Sol-owned
release removes that automatic invocation. Do not represent the current subagent or a prepared
LaunchAgent as a qualified independent Sol service.

Astra retains strategic executive authority; routine queue and engineering decisions are delegated
to Sol inside the founder's unchanged objective and capability envelope. Exceptional unresolved
decisions may enter a durable escalation for a later deliberate Astra episode, with no automatic
nightly invocation or requirement that the founder approve each technical judgment. Sol can repair and release the controller through isolated changes, independent
review, tests, a pinned manifest and an explicit upgrade receipt. Routine controller work does not
require another Astra session. A genuine capability/resource/objective expansion remains a recorded
internal request for Astra through the Russian Founder Desk. Astra remains the executive and sole
founder communicator; Sol holds delegated implementation/queue authority, not the executive role.

The finite task catalogue is the initial qualification mechanism. The next implementation must admit
Sol-issued structured task specifications against the immutable envelope: objective, team, exact
scope, dependencies and contract versions, bounded attempts/resources, risk and independently owned
acceptance references. An independent verifier fixes acceptance before implementation; a worker
cannot change it. Admission is deterministic and rejects unsupported execution/authority. New task
content is not permission to rewrite the controller policy. No useful admissible work means a durable
reason and stop, not indefinite model polling.

The first live pilot failed before any accepted worker implementation: commentary contaminated final
JSON and real dynamic-tool delivery was not qualified. EXP-001/CTX-001 exhausted three attempts each.
A controller repair and three extra attempts per task are approved only after parser and real tool
qualification. `Store.reopen_failed` records an immutable recovery decision and preserves all failed
attempts, events, evidence, epochs and usage in the original database. The750k admission envelope is
unchanged. Do not bootstrap another clean database or omit the failed run from resource accounting.

The next one-to-three-day horizon is qualification and useful accepted work, not a duration promise:
repair the named adapter blockers; release with no automatic Astra; recover and independently accept
the original tasks; qualify task-spec admission and independent Sol recovery; then extend observed
run intervals within measured remaining capacity. Long-duration, reboot and cross-machine claims
remain separate unmeasured acceptance. Final custody/shutdown facts are recorded in the Founder Desk
and the final handoff receipt; do not infer them from an earlier source commit or a prepared plist.

The final narrow security probe succeeded with `code_mode_host=True`, `code_mode=False`, `goals=False`:
real Sol delivered the brokered tool, native apply_patch could not alter the readonly canary, and the
native profile again passed21/21. The four probe turns consumed80447 tokens outside the organization
telemetry; retain their private handoff receipt and account for them once in remaining capacity.
This does not close the strict-parser end-to-end, deployment-upgrade/STOP-maintenance or independent
unattended Sol qualification gaps. The failed pilot was not resumed during the handoff.

### Final decisions and rejected alternatives

| Decision | Selected | Rejected for this stage | Reason / revisit condition |
|---|---|---|---|
| Durable authority | Local SQLite transactions, journal and detached supervisor | Parent conversation tree as authority; Kafka/distributed orchestration now | Reconstructable local state already exists; cross-host coordination is unqualified and not presently required. Revisit after measured multi-host need. |
| Code organization | One repository with explicit department/module contracts and isolated task checkouts | Brain rewrite, a second permanent baseline, repositories split merely to mimic departments | Preserve existing contracts/tests/history; boundaries and ownership provide isolation without migration churn. |
| Integration | One trusted publisher; exact merged candidate, independent gate, CAS against expected tip | Worker push, concurrent blind merges, acceptance of an outdated merge result | One serialized integration authority and fresh checks prevent lost updates and stale acceptance. |
| Availability | Explicit single-host failure domain; launchd only after qualification and login | Calling a detached process or plist durable multi-host/24x7 availability | Sleep, logout, reboot, disk loss and account authentication need measured recovery; no remote uptime claim. |
| Planning | Sol-issued validated task specs inside immutable objective/capability bounds | A permanently finite demo catalogue; arbitrary generated executable tasks | Continue useful work while deterministic admission constrains authority. Catalogue remains first qualification evidence. |
| Acceptance ownership | Independent verifier fixes acceptance before worker implementation | Workers rewriting their own checks; planners granting themselves new gates | Preserve an independent reason to reject incorrect or out-of-scope work. |
| Capabilities and maintenance | Explicit task file/tool grants; Sol-maintained controller through reviewed pinned releases | Blanket filesystem/network/publisher access; editing active policy to bypass failure | Separate implementation from policy release. Expansion is a durable request; routine maintenance stays delegated. |
| Executive continuity | Astra strategic authority and founder channel; Sol routine queue/implementation mandate | Overnight idle Astra, automatic Astra retries, treating a subagent as an independent root | Latest founder directive closes this sprint; exceptional escalation is durable and deliberately resumed later. |

These are selected architecture decisions, not claims that every runtime path has already passed
acceptance. The final measured state and custody are in
[`brain-org-handoff-20261019.json`](evidence/brain-org-handoff-20261019.json).

### Recovery limits retained at handoff

`service.backup_snapshot` currently preserves SQLite, policy/controller and receipts but omits the
integration Git objects/refs, intents, attempt artifacts and logs. It is a partial snapshot, not a
restorable organization backup. A consistent checkpoint barrier, complete content manifest and actual
restore trial are required before that claim. Do not fix this by copying a live SQLite file alone.

`service.validate_deployment` checks qualification flags/manifests but does not yet bind native
qualification to the adapter/executable hashes during restart. New source pins the executable hash;
the existing deployed configuration lacks that field. The reviewed upgrade and restart binding remain
open requirements. The manual independent Sol UI handoff grants ordinary delegated engineering;
it does not qualify unattended full-maintainer execution or eliminate the STOP/startup gates.

### Sol implementation checkpoint — 2026-10-20

Persistent goal continuation was actually activated in root `01a1563e-1a26-75e2-bdab-ca2b38c7b51f`.
Rollout metadata confirms the independent root is `gpt-6.1-sol` high and maintenance workers are
`gpt-6-luna` high. Work is on `org/sol-recovery-20261020`; the pilot remains STOPped at its old
release, with all six failures and the original database preserved. Main and historical active
remote pins were read and remain unchanged. No Astra episode or unattended service was started.

A real Luna run exposed missing timeout units in the dynamic tool interface: milliseconds were
rejected by the unchanged 1..90-second boundary. Source now explains seconds and gives an actionable
broker error. Adapter phase filtering and timeout regressions pass: 142 engineering tests and
18 subtests, plus engineering Ruff. These are source checks, not independent release acceptance.
The second confined Luna run produced an unreviewed supervisor proposal at `1546e4c` on
`org/proposal-no-auto-astra-20261020`; it crossed the measured turn ceiling before completion.
Do not adopt it without independent review and collected quiescence/STOP/quota regression tests.

Maintenance admission initially counted historical known usage without incorporating the measurable
outer-root cumulative counter. This is an accounting failure, not permission to expand the envelope.
At resource stop, the known aggregate lower bound was 2,342,879 tokens including repeated cached input,
above the 750,000 admission budget; later shutdown context and historical killed-chief usage remain
additional/unknown. The separate account weekly window read 67% before maintenance. New model dispatch
is stopped; a further resource horizon needs a durable internal Astra decision, never automatic Astra.
Next engineering requirements remain executable/adapter restart binding, reviewed STOP-preserving
upgrade, bounded original-task recovery, independent acceptance and complete backup/restore.
Exact custody and private evidence locator: [checkpoint receipt](evidence/brain-org-sol-checkpoint-20261020.json).


### Reviewed source repair — 2026-10-20

Independent Sol accepted exact source `1d6d44f`: cumulative usage primitives, atomic late
corrections, consistent SQLite summaries, quota decisions, no automatic Astra in the supervisor,
and shared frozen-controller/native/executable preflight for direct runtime entries. Six frozen
checks, 82 focused tests and six subtests passed inside the OS boundary. Scope, pins and the
independent direct-entry probe are in the [source receipt](evidence/brain-org-accounting-source-acceptance-20261020.json).

This was source acceptance, not an operational release. Local source checkpoint
3b5c1cff8868733dbc219c34368a44e7751f99b0 wired metered dispatch into planner and manual-chief
episodes, but exact Sol review rejected it for seven accounting/admission issues. Follow-up commit
a26dbef2a90fffd309a301347a962f38deb3d1c3 addressed those findings, but exact Sol review again
rejected it for seven pause and lifecycle defects. Current uncommitted candidate fixes add atomic
pause recovery, supervisor quota gating, two-sample rolling recovery, lifecycle receipts, and
owner-state reconciliation. Its 114 focused tests, 18 subtests, Ruff, frozen ledger/quota/atomic/
controller-gate checks, and real usage-interval reproduction pass. Independent review is pending.
Two older frozen raw-RPC checks still omit required Permit/episode identity and fail; frozen files
remain unchanged. This is not release acceptance. The original pilot remains STOPped with six
failures and no accepted tasks.
The full independent-root Goal remains active; its observed automatic continuation, live
process custody and next actions are private durable state.
The source-only structured task validator has separately passed its frozen 37-check acceptance;
independent source review and transactional scheduler integration remain pending. Full recovery,
useful tasks and every organization acceptance gate remain in scope.
