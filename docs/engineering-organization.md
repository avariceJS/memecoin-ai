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

Native tools/MCP/plugins/hooks/apps are disabled; native fallback additionally uses a qualified
readonly permission profile. Empirical qualification found `:minimal` implicitly granted shared
and per-user temp access. Explicit deny overlays now close those paths; positive task reads and
negative sibling/home/temp/symlink/network checks must pass on the installed binary. Platform runtime
reads remain permitted; this is not a claim that every filesystem byte outside cwd is inaccessible.
The custom runner has separate actual read/write/link/socket/signal/publication probes.

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
