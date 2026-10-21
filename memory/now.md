# Current state — 2026-10-21

Native Goal `01a15f55-903b-7de2-adec-19733734ac78` is verified `active` and is the sole Luna
implementation owner. Its predecessor, counters, history, receipts and checkpoints remain preserved
in `/Users/root1/Developer/brain-org-state/resumption-20261020/handoff.json`. Work is on
`org/development`; `main` and the founder's Pilot STOP remain unchanged.

A synthetic source-only vertical now drives two fixed-card choices through real `plan_next` using
deterministic local responses (no live provider), then claims and prepares a verifier-plan identity.
The actual `supervisor.run_attempt` path uses an injected lead/worker transport; its worker calls the
real Seatbelt tool callback and writes the scoped file. Seatbelt grants that file's parent directory,
denies a write into the protected integration repo, and the controller stages only declared scope.
The candidate is bound to attempt/epoch/plan identity. Actual `supervisor.verify_one` runs a
temporary trusted acceptance harness through its verifier sandbox, then passes the packet to an
injected synthetic reviewer that asserts task, scope, diff and passing acceptance result before
returning approval; GitGate then receipts and CAS-integrates it. The acceptance command is the
legacy fixed-policy path-only harness, not manifest-bound A04. An injected crash
after CAS exercises SQLite close/reopen and durable reconciliation; missing receipt and tampered
candidate ancestry fail closed before successful recovery and dependent task claim. Planning binds
the next task to GitGate's active ref, and the test asserts its base is the integrated candidate.
Experience produces a contract consumed by Platform; the consumer is blocked until the exact version
is activated by both participant reviews. The test runs the real durable review journal with an
explicit injected synthetic transport, recording, replaying and acknowledging receipt-referencing
responses in two stages, with contract activation attempted after the first ACK. Packets declare the
candidate SHA and GitGate receipt digest, but Store does not validate the external receipt; the
unadmitted Platform task is absent from its team review inventory. The durable review helper is
reachable through `supervise` only when an in-process transport is explicitly injected: it handles
one request per iteration and attempts activation before planning resumes. The ordinary CLI and
quiescence path pass no transport, preserving the inert Sol handoff. The integrated test uses two
synthetic reviews, including SQLite reopen after response custody and replay without resend, then
plans, claims and dispatches the dependent task; live dispatch remains unqualified. At the quiescent
point after producer integration and response custody, the integrated test now uses real
`backup_snapshot`/`restore_snapshot` with an unmocked preflight against a temporary manifest-bound
controller copy. It verifies STOP, selected artifacts, Git/SQLite readback and the persisted
host-requalification barrier. On the restored copy, the test calls only
`Store.replay_contract_review_responses_for_transition` for the exact transition hash; the API returns
no provider dispatch or dispatch authority. One ACK is restored, activation is not invoked, and
contract v2 and the consumer remain blocked. This does not qualify a STOP guard on
`activate_contract`. Deployment qualification data is synthetic; the manifest-bound
controller copy is not executed and source remains available during restore, so this does not prove
runtime, source-loss or host-loss recovery. Sol accepted the exact replay extension
`f8a3f9da4fbdf7db68567690e103c84e7f886ce829f065b8b9dab97feb4b81a6`; the full gate passes Ruff/format,
596 tests and 189 subtests. Proposal JSON
rejects duplicate keys,
and task admission plus terminal planning
decision commit atomically; an injected hard interruption rolls both back, and client cleanup failure
remains visible to caller without relabeling committed admission. JSON `true` for an integer epoch
fails closed. The restored copy remains STOPped and does not dispatch workers. After the second
participant review activates
the exact contract, the same test now manually completes PLAT-002 after intercepting the supervisor
spawn: a Seatbelt worker reads integrated PLAT-001 output and writes only `src/consumer.py`, trusted
acceptance executes the consumer, and a synthetic independent reviewer checks its exact packet.
The test rehashes the canonical GitGate receipt; GitGate/Store integrate the second candidate, with
its first parent equal to PLAT-001, `main` unchanged and one integration event per task. A negative
test moves an existing active ref after candidate creation and confirms publication fails closed.
This exposed/fixed GitGate's trusted-history check:
dependent bases from `research/active` are now trusted even when checkout `HEAD` remains `main`.
The manual continuation is not an A05 restart proof; provider/reviewer authenticity and bound A04
remain open. Sol independently reviewed the recovery binding and found
no remaining bypass in this bounded path. Lead, worker and reviewer transports, trusted acceptance
harness and both review transports remain synthetic. This does not qualify real provider identity/settlement,
reviewer judgment/authenticity, full A04, remote checkpointing or operations.

The synthetic fresh-restore path now records a passive A05 `hold` using the copied STOP and restore
receipt, preserves it across SQLite reopen, and rejects a later optimistic write from the old owner
epoch. The worklist reports no dispatch/consumption authority. `snapshot_stop`, host requalification,
accounting and quota are conservative fixture inputs; this adds no trusted freshness-bound gate
capture, consumer, restart or operational recovery. Sol accepted the exact test diff
`67fbfcfc97ce0363ebac32df6e727598667c43ad351f2a69adf81a655e85490f`; the engineering gate passes
Ruff/format, 596 tests and 189 subtests.

Before the PLAT-002 fake-transport scope fix, one focused run passed with `verify_one` outside the
mock; an installed Codex reviewer may have been invoked. Attribution and usage settlement are
unknown, so do not call them zero or settled and do not release any reservation. The subsequent full
gate was interrupted after 451 tests. Owner/production ledgers were not changed; the test used a
disposable state. The test now holds the same synthetic metered-client patch over lead, worker and
reviewer and asserts the exact role trace. The corrected full gate and smoke now pass. The current
engineering gate passes Ruff/format and 596 tests plus 189 subtests; Sol accepted the exact
PLAT-002/GitGate source candidate.

`make smoke` passes with deterministic synthetic fixtures. Repository `make check` stops at 107 Ruff
findings in untouched `src/`, `tools/` and `tests/`, before type and package tests. A macOS process
identity integration test has intermittent Security.framework observation errors; tests accept only
fail-closed `ConfinementError`, with deterministic exact mismatch covered separately.

The organization objective remains active. Trusted worker isolation through the full dispatch path,
reviewer/provider authenticity and settlement, operational release, A03, runtime and production remain
unqualified or prohibited. Continue source-only work; do not access protected data, start the pilot,
training, AUTO, trading, paid APIs, Astra or change `main`.
