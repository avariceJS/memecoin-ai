# Current state

As of **2026-10-20**, the founder has resumed autonomous organization engineering after Astra’s
independent audit. Successor thread `01a15a6e-0e03-77d2-b3ce-d81b95b5a89f` is active; predecessor
Luna thread `01a15657-3558-79c1-86dc-6ed033c264df` is idle with its Goal paused and history preserved.
Native `state_5.sqlite` metadata confirms the successor as `gpt-6-luna/high`. Sol’s independent plan is recorded in
`docs/engineering-organization.md` and `engineering/SOL-MANDATE.md`.

- Work only on `org/development`; current audit baseline `0054a9e`, source audited at `b2dcd6d`.
  `main` remains `2f8da5f`; Brain remains `brain/development`; `brain/linux` is isolated and
  unqualified; `research/active` is a frozen compatibility alias.
- First resolve the evidence source for terminal usage and bind it to dispatch/thread/turn/model/epoch.
  Unknown usage remains reserved. Then form one finite STOP-preserving release candidate for
  bootstrap, ledger initialization, settlement, active quota stop, controller-fault circuit breaker
  and explicit engineering release checks. Sol independently reviews the exact candidate.
- Sol accepted the pinned schema/source receipt only as static correlation and rejected terminal
  settlement qualification. `thread/tokenUsage/updated` lacks proven finality/order/completeness and
  dispatch/model/epoch binding; account usage is estimated and aggregate. A conditional six-field
  parser gap exists for optional `cacheWriteInputTokens`; see the canonical organization document.
- Corrected the raw-event schema probe: the first default schema export filtered out
  `experimentalRawEvents`; exact pinned v0.163.0 `--experimental` schema includes it, but marks both
  opt-in and `RawResponseCompletedNotification` internal-only. Pinned core source emits per-response
  events after completed upstream responses and tests reject incomplete streams; app-server tags
  thread/turn and publishes before turn completion. However, `response.incomplete` can report nonzero
  spending recorded separately without RawResponseCompleted, while TurnComplete still follows; pinned
  tests assert this. Raw completion events cannot form a complete usage ledger. Runtime delivery,
  reconnect, duplicates, and late corrections remain untested; payload lacks dispatch/model/epoch.
  Receipts: `terminal-usage-raw-response-source-semantics-20261020.json` and
  `terminal-usage-raw-response-incomplete-gap-20261020.json`. Stable `codex exec --json` re-exports
  progress snapshot totals on successful completion and is not an independent terminal source. A
  public null-usage response can also be hidden: `TokenCount.info=None` produces no public usage
  notification; a later positive snapshot can appear complete against a zero baseline. Sol rejected
  settlement on this counterexample. A pre-dispatch provider-state guard plus rejection of any
  zero-delta snapshot could conservatively exclude in-turn null usage, but the baseline provenance,
  model and epoch are not qualified and no supported settlement seeding path exists. Receipt:
  `terminal-usage-public-snapshot-null-gap-20261020.json`; independent follow-up:
  `terminal-usage-public-snapshot-guard-review-20261020.json`. A03 remains fail-closed. Correction
  receipt: `terminal-usage-schema-filter-correction-20261020.json`.
  Internal per-response `TokenUsageRecord` is also not a route: public thread history omits the
  record, resume replays only aggregate TokenCount, null usage creates no record, and record fields
  lack dispatch/model/epoch/completeness. Sol rejected it as public or sufficient settlement evidence.
  Receipt: `terminal-usage-token-record-public-api-review-20261020.json`; private rollout data was not
  read.
- Current-host native profile (21 checks) and OS confinement (27 checks) passed on Python 3.13.6 and
  pinned Codex CLI 0.163.0; adapter digest matches exact candidate `3c16388`. Receipts are
  `native-profile-qualification-20261020.json` and `confinement-qualification-20261020.json`. This
  does not requalify a specific restored deployment or clear its barrier; restore remains `runtime_ready=false`.
- The parser now preserves that optional field in raw evidence and normalizes only the five canonical
  counters. Exact candidate `3c16388` passed the pinned engineering release target using Python
  3.13.6 and the already-installed external venv: Ruff lint/format and pytest (251 tests, 42 subtests).
  The external environment was not modified.
- Sol reviewed `f73e030`: A01, A07 and A02 bootstrap ordering/atomic migration were accepted, while
  A02 schema compatibility, A09 wrong-shaped output validation and complete release qualification
  were rejected. Sol also rejected `47d0770`: it found split-FK acceptance, missing Git object closure
  and organization schema validation, old executable-path dependence, symlink containment bypass,
  unowned destination cleanup and a late restore barrier. Candidate `d4d6590` repairs these
  cases with grouped FK validation, full organization column/constraint signatures, offline Git
  config and promisor rejection, bundled path rebinding, unavailable host tool handling behind the
  barrier, canonical containment and prepublication requalification. The supervisor performs its
  first quota read regardless of uptime. Restore retains STOP and remains `runtime_ready=false`;
  host qualification is required. Sol reviewed `81aa8eb`, found that `allow_requalification=True`
  could be called without a persisted barrier, and the `d4d6590` candidate now requires that barrier.
  Sol accepted exact commit `3c16388` for bounded source repair; independent `gpt-6.1-sol/high`
  review confirmed the permit/thread fixture contract and no settlement from progress plus
  `turn/completed`. The pinned source gate passed. This is not full release qualification: ORG-A03
  remains fail-closed, and operational restore/host qualification remain open.
  A03 remains fail-closed; unknown/late usage keeps reservations.
- Current focused check: 62 unittest cases across ledger/state/bootstrap and all 26 supervisor
  unittest cases passed; 12 snapshot/restore/deployment functions passed via a minimal import-only
  `pytest` shim.
  These earlier shim checks are superseded for source-gate status by the pinned pytest/Ruff run above.
- Exact `d4d6590` source passed a direct complete synthetic checkpoint/move/fresh-path restore
  scenario: online SQLite backups, copied inventory/receipts/integration Git ref, STOP retained,
  persisted host-requalification barrier, `runtime_ready=false`. Owner deployment and pilot were not
  copied/restored.
  Pilot STOP, original SQLite and six failures remain frozen. Reopening original tasks requires
  release acceptance and separate founder authorization. Market collection, training, AUTO, trading,
  protected data, production, `main`, paid APIs and supervisor operation remain prohibited.
- Verified read-only state at succession: the predecessor Goal remains paused; one successor
  Pursuing Goal is active; pilot has three tasks, six failed attempts, no live attempts, zero
  acceptances and zero integrations; STOP remains present and organization LaunchAgent is not loaded.
- Audit evidence and limitations are in
  `docs/evidence/brain-org-independent-audit-2026-10-20.json`. The old consolidation handoff is
  preserved unchanged; the dated resumption handoff records the new owner transfer separately.
