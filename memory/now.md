# Current state

As of **2026-10-21**, independent source development continues in successor Luna Goal
`01a15b1c-c263-71b0-bfca-c5b46d17a89b` (`gpt-6-luna/high`). Predecessor Goal/thread
`01a15a6e-0e03-77d2-b3ce-d81b95b5a89f` remains blocked and preserved after repeated ORG-A03 research;
the native Goal API cannot resume it. The successor is the sole implementation owner for bounded
A05/A10/A04 source development and verification. Astra remains episodic and is not to be started
automatically.

- A03 is an external Codex terminal-settlement interface dependency at pinned CLI 0.163.0. Public
  snapshots, internal raw events and hidden `TokenUsageRecord` do not provide complete, supported
  dispatch/model/epoch-bound settlement. No more identical probes without an upstream interface or
  source change. Unknown/late/ambiguous usage stays reserved. Full release remains blocked; bounded
  dispatch/duration/concurrency limits are not provider spend caps. Founder Desk records the upstream
  interface request and safe limits. Live dispatch remains prohibited; a future separately authorized
  mode would permit at most one active and one unresolved dispatch, retain reservations, and stop on
  unknown settlement. This bounds exposure count, not the cost of one request.
- Continue independent source work: A04 execution/verifier binding, A11 complete review packet, A05
  passive lifecycle, A10 durable contract/dependency transitions, and A12 bounded history projection.
  A03 blocks release, not unrelated engineering. Full operational acceptance remains required.
- A04 macOS 26.7.1 probe found no reviewed public descriptor-to-exec mechanism; Apple launch
  constraints do not bind current `sandbox-exec` invocation to the hashed inode. A05 consumer design
  must require coherent fresh source-identified gates, current-fence revalidation and stable
  authorization-generation deduplication. Both remain open; see `docs/engineering-organization.md`.
- Source-only A04 review of tracked `pilot_acceptance.py` found stdlib imports and sandboxed dynamic
  candidate loading; interpreter hash plus `-I -S -B` does not pin stdlib/native loader closure.
  Deployed manifest/runtime were not inspected or executed.
- A04 scaffold is in isolated worktree
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-execution-binding-checkout`, branch
  `org/a04-execution-binding`, based on `d8b5d5350725564ac2771ed2d67cc97fac6db12b`. It atomically
  persists and resolves trusted verifier metadata and checks task/spec drift. Supervisor execution,
  reconciliation, manifest qualification and production feature-gate integration are not complete.
  Sol rejected its first draft; drift and snapshot gaps were corrected and Sol accepted the bounded
  persistence/resolver scaffold. Exact receipt is in the resumption directory. Full engineering gate:
  255 passed, 42 subtests passed; Ruff lint/format passed (45 files).
- A04 shared verification-plan construction is merged at
  `066227ef14fec28ed9d8879e0b61668cd3448f1b`. `verify_one` and `reconcile` resolve the immutable
  controller binding; spec-backed plans fail closed before Git, sandbox, model, acceptance or
  publication until executable manifest qualification exists. Reconciliation continues past
  blocked intents and legacy policy digests are unchanged. Sol accepted exact source; gate passed
  284 tests/42 subtests and Ruff (50 files). `run_attempt`, manifest/interpreter/sandbox/result
  qualification, production admission gates and full A04 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-shared-verification-plan-review-20261020.json`.
- `run_attempt` now resolves that plan after lease/circuit checks and before checkout or model
  dispatch at `838ff0882758a311334c0c67057a5796b8a617bb`; unqualified or unavailable plans fail
  closed. Sol accepted this bounded gate; full gate passed 285 tests/42 subtests and Ruff (50 files).
  Candidate evidence does not yet compare the saved plan hash with verifier re-resolution. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-run-attempt-verifier-gate-review-20261020.json`.
- At `b0e47312d496417c92efb5716babbd5713dce068`, candidate evidence stores the full plan identity;
  verification and every reconciliation transition require exact equality. Bound missing identity
  and explicit `null` block; absent legacy identity stays compatible. Sol accepted exact source and
  full gate passed 286 tests/42 subtests, Ruff (50 files). Full A04 remains open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-attempt-plan-evidence-review-20261020.json`.
- A11 review packet is in isolated worktree
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a11-review-packet-checkout`, branch
  `org/a11-review-packet`, based on `6974f941aa6c58b64d1fa6dcce26f48faa3428a2`. Silent diff
  truncation is replaced by a content-addressed complete text packet and explicit pre-review blocks
  for binary summaries, path encoding, oversized diff and final packet. Sol accepted exact bounded
  source repair; full gate passed 265 tests/42 subtests and Ruff (47 files). Broad A11 remains open.
- A05 pure deterministic lifecycle planner is merged at
  `91c6cfaf85aaebf62f6bd36290d7fcaf27a1d2b6`; Sol accepted the exact scaffold and the full gate
  passed 274 tests/42 subtests with Ruff (49 files). This initial increment emits only an inert
  owner/epoch request after fail-closed observations. Its persistence/fencing gaps were addressed by
  the later increment below; request consumption, retry policy, scheduler/process recovery and
  operational A05 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a05-passive-lifecycle-scaffold-review-20261020.json`.
- A05 durable owner fencing and inert request persistence merged at
  `55fa10b354317c2bbb6c304cc3602fbf83688497`; exact Sol-reviewed source tip is
  `10e127aae103a07dbb9f89dca35631f72f7d7a73`. Lifecycle owner epochs/leases and requests use the
  existing append-only decision journal; public generic writes cannot alter reserved entries; owner
  histories are contiguous and retries require exactly version 1 with canonical JSON equality.
  Pinned gate passed 298 tests/45 subtests and Ruff (51 files). Request consumption, cross-epoch
  deduplication, retry policy, scheduler/process wiring and operational A05 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a05-lifecycle-fencing-review-20261021.json`.
- A05 passive consumer remains unimplemented: inspection of the current Store contract shows that
  lifecycle owner epoch is only a replaceable lease fence, while `launch_authorized` is caller-supplied
  observation data. No durable controller-owned authorization-generation source exists; do not mint
  or promote it in the consumer. Keep the request inert until a separately owned generation interface
  exists. Consumer may persist passive disposition only; process/service action needs separate review.
- A05 observation-binding source increment `db04f23468744f736622ad5ea33f13d0166c4446` makes Store
  recompute launch plans under the owner fence and persist the exact detached caller-supplied gate
  observations with each plan.
  The plan-only API rejects launch requests. Sol accepted; gate passed 337 tests/73 subtests and Ruff
  on 56 files. This binds claimed inputs to planner output, not sensor truth/freshness/completeness or
  current launch authority. Consumer, revalidation at consumption, cross-epoch deduplication,
  recovery and operational A05 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a05-observation-binding-review-20261021.json`.
- A10 transition increment merged at `ef953d5d95e0fb9bef50477957fcbd8f9fc90ed6` (exact source
  `0879b5efed1cf0942d080e02624da13fd5a9aede`): content-hashed one-version proposals, immutable
  producer/consumer acknowledgements and atomic activation after producer integration. Sol accepted
  bounded source; merged gate passed 312 tests/45 subtests and Ruff. Generic contract migration,
  department plans/leases and broad A10 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-durable-contract-transition-review-20261021.json`.
- A10 dependency-obligation increment merged at `5e4d123ece95d6c87c0eb0ced6891d8473bedece`.
  The blocked-dependencies trigger now refreshes durable duties from a transactionally coherent
  readiness snapshot, preserves unrelated ready work, and cancels stale duties with an immutable,
  content-addressed readiness event. Sol accepted the exact diff; Ruff passed and the gate passed
  341 tests/73 subtests. Automatic trigger freshness, generic contracts, department plans/leases and
  full A10 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-dependency-obligations-review-20261021.json`.
- A10 quiescence-trigger freshness increment is on `org/development` at
  `714bb573fc659ba00c849c9b7e0be7c1f470b472`. Handoffs now fingerprint the coherent task/readiness
  snapshot and are atomically rechecked/deduplicated by Store; eligible work defers quiescence. Sol
  accepted the exact diff; Ruff passed and the engineering gate passed 344 tests/73 subtests.
  Separate quota/resource observations, post-commit state changes and scale with retained tasks
  remain limitations. A separate Sol-accepted test-only increment `01a4c14` now holds a writer lock
  on a second Store connection, waits for the handoff writer, mutates readiness before unlock and
  verifies stale rejection plus fresh acceptance; gate passed 349 tests/83 subtests and Ruff.
  The timeout is practical contention evidence, not exhaustive scheduling proof. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-quiescence-lockwait-review-20261021.json`.
  Generic contracts, plans/leases,
  conflict adjudication and full A10 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a10-trigger-freshness-review-20261021.json`.
- A04 source-only verifier-manifest custody resolver merged at `9487fc70057bc8ff5a467394fe7b70b06391e7b3`;
  Sol accepted the exact source. It verifies canonical manifest/binding digests, release/policy
  identities, exact verifier metadata and trusted harness bytes. The shared plan does not call this
  helper; interpreter/sandbox/result qualification and execution remain fail-closed. Full gate passed
  320 tests/55 subtests and Ruff (54 files). Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-verifier-manifest-review-20261021.json`.
- A04 plan integration merged at `e96dbd2ee4af7b0f2496730c97a1815b1f69a411`: configured manifest
  pins and harness identity enter plan/attempt identity; missing pins keep bound plans unqualified,
  and all bound execution remains blocked pending interpreter, sandbox and result qualification.
  Sol accepted exact source; gate passed 322 tests/55 subtests and Ruff (54 files). Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-plan-manifest-review-20261021.json`.
- A04 result-contract wiring merged at `3c859e28eed86d9d7c6ecdcacdb9128c826fb387`. The first
  result command must pass the pinned harness directly; strict result parsing enforces schema,
  acceptance identity and literal success, with allowlisted environment overrides. Sol accepted the
  bounded source; the engineering gate passed 328 tests/62 subtests and Ruff. Bound plans remain
  `execution_qualified=False`; interpreter/sandbox qualification, executable integration acceptance
  and full A04 remain open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-result-contract-review-20261021.json`.
- A04 manifest v2 interpreter pin merged at `d985cffdcbddee11d25f9972f6a1dafcf73f90fb`. Canonical interpreter path and binary SHA-256 are checked against configuration and included in plan/attempt identity; the first command binds interpreter then harness. Sol accepted bounded source; gate passed 329 tests/65 subtests and Ruff. Bound plans remain unqualified; file replacement between resolution and invocation, invocation/loader semantics, sandbox qualification, executable integration and full A04 remain open. Review receipt and hash correction are under `/Users/root1/Developer/brain-org-state/resumption-20261020/`.
- A04 prelaunch interpreter recheck is now a Sol-accepted bounded source increment on isolated branch `org/a04-exec-revalidation`, based on `1985a262cf39c3623a8e49b82f3bc3baf0c48d69`. Before the first manifest-bound command, `SandboxRunner` opens the canonical interpreter without following a symlink, checks regular/executable mode and hashes at most 512 MB; mismatch fails before `Popen`. Result records the observation as `prelaunch_executable_sha256`. Sol accepted the exact three-file diff. Ruff passed on 56 files; engineering tests passed 332 tests/65 subtests. This checks the opened inode but does not prove `sandbox-exec` later executed it; parent-path/post-check replacement, loader/invocation semantics, sandbox qualification, integration acceptance and full A04 remain open. Plans remain `execution_qualified=False`. Worktree: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-exec-revalidation-checkout`.
- A04 isolated loader command contract merged at `1fd72a363f3ae2dc420223140ac688caa2c7ab24`; owner documentation merged at `7f42047a677c7af1715244bdafb555d6ea531170`. Admission and manifest v3 require exactly `{interpreter} -I -S -B {harness}` at the start of the first verifier command; v2 manifests fail closed. A disposable Seatbelt run with the pinned interpreter and trusted synthetic harness passed strict result validation and checked startup flags, prelaunch hash, dynamic profile hash and no checkout `.pyc`. Ruff passed on 56 files; engineering gate passed 336 tests/73 subtests. Sol accepted the bounded source increment. The sandbox test constructs argv directly and does not join manifest resolution, persisted plan identity and supervisor execution; harness imports and loader dependencies are not pinned, and same-inode invocation is unproven. Deployment qualification, production admission gates, full A04 and operational acceptance remain open; plans stay `execution_qualified=False`. Review receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-isolated-loader-review-20261021.json`.
- A04 same-inode feasibility probe on macOS 26.7.1 / Darwin 25.6.0 found no reviewed public descriptor-to-exec path: `/dev/fd/N` execution of an inherited `/bin/echo` descriptor returned `EACCES` with `O_RDONLY` and `O_EXEC`; current SDK/libSystem probes found no public `fexecve`/`execveat`. No private API is adopted; plans remain unqualified. Loader/import closure remains open. Receipt: `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-darwin-fd-exec-probe-20261021.json`.
- A04 process-code identity is Sol-accepted on isolated branch `org/a04-process-code-identity`
  in worktree `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-identity-checkout`.
  Source `a6f2dfbf7564ef52121f58e0b5534ee79738f1b` pins manifest v4 runtime path/CDHash and
  records one Security API observation of the first verifier process image; docs `0e4ad250478e91a90967997288064e88f772f25c`
  describe the limits. Exact reviewed candidate diff `5169cc17940461374424bb047f60c9ba110342d1e976463796fa8dae2e1f23c9`;
  receipt `/Users/root1/Developer/brain-org-state/resumption-20261020/a04-process-code-identity-review-20261021.json`
  (SHA-256 `dfbed547202fe6ec40cbc09c8612681264319d637e273944e2e6a91cfe783ec5`). Gate passed 348 tests,
  83 subtests and Ruff (58 files). This is not literal inode, prior/later execution or output provenance;
  only first command is sampled, loader closure remains open, and plans stay `execution_qualified=False`.
  Source, docs and this checkpoint are fast-forwarded into `org/development` at
  `afdc32247c8c0a1a89d79f5245ba7fe88c455ca5`. `main` remains unchanged.
- A12 bounded active-task projection merged at `e7e8f1d039cce909d91782c1cf51f4587ccbc016`; Sol
  accepted the exact source and the gate passed 278 tests/42 subtests with Ruff. SQL limits active
  rows to 100, full state counts remain, omitted rows are disclosed, and history is retained. Full
  row counting still scans tasks; full A12 remains open. Receipt:
  `/Users/root1/Developer/brain-org-state/resumption-20261020/a12-active-task-projection-scaffold-review-20261020.json`.
- Original worktree `/Users/root1/Developer/brain-org-build` stays on `org/development`; `main` and
  protected state remain untouched. Pilot, supervisor runtime, services, market collection, training,
  AUTO, trading, protected datasets and STOP remain untouched.
- Same successor Goal ID and prior history are preserved. Native Goal remains active. The durable
  handoff carries the latest API counters and review receipt; predecessor history and the
  single-owner identity remain intact.

Canonical program, acceptance criteria and limits: `docs/engineering-organization.md`. Founder-facing
external-dependency request: `engineering/OWNER-DESK.md`. Prior immutable receipts remain under
`/Users/root1/Developer/brain-org-state/resumption-20261020/`.
