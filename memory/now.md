# Current state — 2026-10-22

Native Luna Goal `01a15f55-903b-7de2-adec-19733734ac78` is verified `active` and remains the sole
implementation owner on `org/development`. It is the controlled successor to paused Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b`; predecessor history, counters, receipts and checkpoints are
preserved in `/Users/root1/Developer/brain-org-state/resumption-20261020/handoff.json`. `main`, Pilot
STOP, protected data and runtime boundaries remain unchanged.

Current A10 department planning selects fixed cards only from current objectives and leases, and keeps
unknown injected transport outcomes held without resend or usage release. A new controller boundary
requires a fixed card's persisted department nomination and exact trusted-card projection before
supervisor claim, worker dispatch, candidate verification and recovery reconciliation; off-catalogue
work must use task-spec admission. Supervisor Store instances apply that catalogue inside readiness and
claim transactions; task-spec IDs cannot collide with fixed cards, including an ID mutation during
SQLite lock wait. Fresh quiescence durably reports `controller-assignment-required`. Sol accepted the
boundary and race fix. `make engineering-release-check` passes Ruff/format, 672 tests and 193 subtests;
smoke is reproducible with `edge_established=false`. The full loop remains deterministic and injected;
reviewer/provider identity, A03 settlement, restored-runtime continuation and operational release are
unqualified.
Unknown outcomes retain their reservations. `make check` was previously blocked by 107 unrelated Ruff
findings under `src/`, `tests/` and `tools`; this narrow change does not touch those paths.

The synthetic source-only path connects planning, trusted task binding, isolated execution, independent
acceptance/review, GitGate receipt/CAS integration, dependent continuation, contract reviews,
snapshot/restore and interruption recovery. A05 passive restored-copy hold binds a fresh read-only gate
snapshot and rechecks STOP, restore receipt and parser history before recording; same-content filesystem
ABA remains indistinguishable, and no request consumer or authorization generation exists. A10
participant requests are controller-prepared from integrated producer/verifier/receipt evidence and
rechecked before dispatch intent and finalization; real provider dispatch and usage settlement remain
unqualified.

A04 admission-to-gate now includes off-catalog PLAT-003 at the integrated PLAT-002 tip. Store binds
spec contract digests to exact canonical contract bytes; v2 also revalidates transition hash, activation,
integrated producer and reviewed ACK provenance. Readiness/claim/plan resolution reject contract drift,
activation type substitution and a missing spec row; a forged legacy catalogue cannot replace the saved
verifier binding. Sol accepted exact diff
`04f44369c5a2434a6136b835ad2a8e90a897b6a20417f1cee3bd892778c230f9`. `make engineering-release-check`
passes Ruff/format, 617 tests and 189 subtests; `make smoke` is reproducible with
`edge_established=false`. PLAT-003 remains `execution_qualified=False` and `run_attempt` fails before
checkout or dispatch. This does not establish off-catalog execution, full A04, provider identity or
operational readiness.

A10 hard-process recovery projection is now tested at four child-process death points around
`process_pending_reviews`: after dispatch intent, after session custody, after synthetic review
returns but before response custody, and after durable response custody. On SQLite reopen, the first
three remain `hold_no_resend` with `provider_usage=unknown`; an independent prepared review still
progresses. Only the custodied response locally replays, creates its ACK, and permits contract
activation with zero transport calls. Sol accepted exact source/test diff
`9de1e75b0409d1bb2e915c34e90848509a5aa8bd91df562a1d36671c1cd47c56`. `make engineering-release-check`
passes Ruff/format, 618 tests and 189 subtests; `make smoke` is reproducible with
`edge_established=false`. This is synthetic local process/SQLite recovery only: pre-custody lost
responses remain unresolved; it does not prove provider identity, usage settlement, A03, authorization
generation or runtime recovery. Continue source-only integration.

A05 adds a manifest-bound `restore-reconcile` CLI for fresh-path restored controllers. In disposable
synthetic restore, the command records a source-bound passive hold only when STOP, the restore receipt
and parser recovery gate remain valid; it never imports supervisor or metered provider code. A child
process exits after the durable hold and lease release but before worklist readback; reopen/retry yields
exactly one hold. Missing STOP and tampered receipt fail closed. Sol accepted the exact source/test diff
`df12b50414bc52abe776b3d861c25aa1d3dbf67fecd6d99011dfa6d4c5fbd562d` at base `97894c8`. Full gate:
Ruff/format, 619 tests and 189 subtests; smoke is reproducible with `edge_established=false`. This does
not qualify host/runtime recovery; identical-byte filesystem ABA remains indistinguishable, and no
authorization generation or lifecycle consumer exists. The full PLAT-002 integration test also calls
the copied controller CLI against its integrated Git/contract checkpoint; the focused regression and
full release gate pass. Continue source-only integration.

The dependent PLAT-002 task runs through `supervise` to acceptance, receipt/CAS and integration. The
frozen controller's real local child records one immutable PID/birth identity before work; duplicate
entry cannot redispatch. A work-start journal record precedes each provider client, and STOP no-work
cancellation requires the registered owner plus no work/session/usage/candidate. Hard exits before or
after custody remain expired and consume retry. Direct Store claim and `ready_tasks` now block expired
attempts until reaping is durably recorded against attempt, epoch and PID/birth set; readiness reports
the blocker while unrelated tasks proceed. New claims carry `child-start-v1`; only these may receive
`no_work_before_custody`, after Store proves no child/session/work/usage/candidate was recorded. Legacy
attempts stay held. Reaper validates child/session ownership, confirms process
disappearance or PID birth change, commits SQLite receipt, then writes a recoverable marker. Failed kill
or uncertain identity leaves the receipt absent. Reopen recovers both the hold before receipt and marker
after receipt. The receipt is a trusted local reaper assertion, not authenticated against direct
Store/SQLite writers. This relies on macOS process witnesses; expired legacy attempts without receipt
remain held for positive reconciliation. Synchronous `Popen` failures atomically cancel the unstarted
attempt and persist capped exponential task cooldown; claim/readiness enforce it, unrelated tasks
continue, and child custody resets the streak without spending retry budget. During cooldown, planning
runs once per changed durable revision and pending contract reviews can progress; the earliest cooldown
is observed even with unrelated dependency blockers. Each normal bounded/STOP episode exit now writes
an idempotent passive checkpoint bound to deployment root, STOP, readiness revision/digest and earliest
retry. Unknown runtime/accounting/quota/process gates remain `hold`; read-only status can report due for
recheck but never starts another episode. Synthetic process exits before the checkpoint call and after
its committed return, plus explicit cross-episode PLAT-002 continuation, cover local SQLite recovery
only. Full gate: Ruff/format,
649 tests and 193 subtests; smoke is
reproducible with `edge_established=false`. This is
synthetic source-only acceptance, not A04/provider/runtime qualification; remaining limits are in the
canonical organization record.

The A10 department-plan admission link now requires a current team lease/plan nomination for the active
objective at both `plan_next` and public fixed-card `admit`; admission rechecks exact revision/digest
and conflict state in one transaction. The Experience→Platform synthetic contract/dependency cycle
preserves both admission provenance records through integration and STOP-preserving snapshot/restore.
Open conflicts do not change readiness for unrelated
Store-ready tasks. Snapshot validation supports Executive tables and previous Store-only schemas. Full
gate after API change: 651 tests/193 subtests; reproducible smoke, `edge_established=false`. The
review packet now binds each admitted fixed card's exact persisted nomination in its canonical digest;
the integrated test checks Experience and Platform against durable admission decisions. Reviewer sees
the admission projection, not a source plan row or renewed-currentness check. Generic `put_decision`
is fenced from `admission/`; legacy/direct-created tasks without nomination carry null. Previous
five-file A10 candidate and packet-binding increment were accepted by Sol; review of this latest
namespace fence is pending. Digests from current plan rows do not independently prevent direct SQLite
tampering.

`make check` still stops before later checks on 107 existing Ruff findings in `src/`, `tools/` and
`tests/`; repository-wide pytest collection still lacks the historical PumpSwap IDL capture manifest.
These remain separate from the engineering release gate. ORG-A03 is the external operational-release
blocker. Continue source-only synthetic work; do not start the pilot/runtime, collect market data,
train, enable AUTO/trading, access protected data, use paid APIs, invoke Astra, or change `main`.
