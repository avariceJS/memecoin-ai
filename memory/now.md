# Current state

As of **2026-10-19**, the post-reset research checkout is usable for local development,
read-only historical inspection, paired model evaluation, synthetic training tests, and local UI.
The current user explicitly authorizes this work and temporary loopback services; the old
pre-reset freeze is historical. External collectors/writers, unattended jobs, AUTO activation,
trading, signing, promotion, and paid upgrades remain disabled pending separate authorization.

- Open `/Users/root1/Developer/memecoin-research`, integration branch `main`. The fetched reset
  baseline was `f9a8118`. Historical owner `/Users/root1/Desktop/memecoin-ai` remains pinned at
  `a594474`; its five documentation-only edits from the earlier audit are preserved unchanged.
- Both research Python environments now install this checkout **editable**. Existing pins remain;
  the ML environment additionally has the two already-locked dev packages hypothesis/sortedcontainers.
  Source `tools/brain_dev_env.sh` in a Cursor terminal: Python3.13.6, OpenSSL3.6.4 with the preserved
  bundled config fallback, Node24.5.0 and zstd1.5.7. Only zstd1.5.7_1/lz4 1.10.0 were newly added;
  existing native formula versions and package locks were not upgraded. Local Cursor interpreter
  settings are ignored machine state.
- The internal case-sensitive APFS development volume is `/Volumes/BrainDevelopment`; its private
  binding is `~/.config/brain-research/development-storage-v1.json`, retaining the 50-GiB safeguard.
  The exact historical BrainData tree is nested at `ResearchData/kingston-snapshot/BrainData`:
  212,474 files /23,692,267,515 bytes and 273,359 members verified against the sealed source index.
  Three research archive aliases now resolve there. Kingston, the historical root marker, canonical
  manifests and old global `storage-v1.json` remain unchanged. New work must use the explicit new
  binding; historical leases/writer identities must not be reused.
- This is a **read-only working replica**, not strict migration-v3 acceptance. macOS transforms
  `com.apple.provenance`; file/link/hardlink contents, modes, timestamps and BSD flags match after
  destination-only symlink timestamp repair. The full exception/identity receipts are local under
  `/Users/root1/Desktop/restore/evidence/development-readiness-20261019/`. No source release or
  protected semantic access occurred. Storage and model evaluation passed with Kingston reads denied;
  normal internal-volume unmount failed closed and identity-checked remount restored readiness.
- Start actual copied-state Operator UI with the command in `docs/operator-runtime.md`.
  It is GET-only, has no operator/Watch tickers,
  and preserves the historical source. All seven tabs worked in a real-API jsdom check; matured
  93,721 matched the API. The separate React Observatory remains its pinned historical projection.
  Watch session state is explicitly omitted and never read from the original/sibling Watch lane.
  Preview binding/link/hash guards and the paired evaluator passed 24 focused tests.
  Native Safari inspection was not approved by Computer Use; CC02 visual acceptance is not claimed.
  Preview/Observatory startup bypasses unnecessary loopback reverse DNS after an observed resolver
  stall; restart/real API/shutdown passed, and all three temporary development listeners were stopped.
- CPU and MPS forwards passed for all five model architectures. The saved B13 GRU and official
  empirical champion reproduced registered paired loss/calibration on 249 test decisions/8 pools,
  with no fits or promotion; champion remains better. B07 historical virtual ledger replay was exact.
  See `docs/operator-runtime.md` for supported launch/shutdown and development commands.
- Broad ML pytest: 4,923 passed,21 failed,5 errors,4 skipped before final environment corrections.
  The five TLS errors were fixed by the OpenSSL config fallback (27 focused tests passed); descriptor
  and host witness cases passed separately (the latter requires a user-group-owned temp root).
  Eighteen remaining failures match documented old-main failures; the operator restart test remains
  reproducibly inconsistent with its expected running/interrupted state. `make check` still stops at
  107 pre-existing Ruff findings. `make ml-check` and its17 tests, source smokes,40 Observatory Python
  tests and44 web tests/build passed. Do not call the entire historical suite green.

## Engineering organization — 2026-10-19

The founder now appoints episodic Astra High as CEO, Sol department leads and Luna workers.
Implementation is isolated on `org/foundation-20261019`; `main` must remain unchanged.
`research/active` may become the verified integration lane only after its historical SHA is archived.
The separate `engineering/` control system is under qualification: no live runtime, acquisition,
training, AUTO or trading activation. See `docs/engineering-organization.md` and the Russian
`engineering/OWNER-DESK.md`. Earlier sole-main and Sol-executive policy below is historical.

The final founder directive ends active Astra after executive handoff; sustained implementation
belongs to an independent Sol6.1 High root with Luna6 High. Start with `engineering/SOL-MANDATE.md`.
Source checkpoint: `/Users/root1/Developer/brain-org-build`; private stopped pilot:
`/Users/root1/Developer/brain-org-state/pilot-20261019`, deployed base `fa9d625`.
132 focused tests and 18 subtests pass. The real pilot exhausted six Sol lead attempts on adapter
defects before any accepted worker code; no Brain task was integrated. Repairs and scoped tool
qualification are preserved in source/evidence, not yet deployed. STOP remains, no organization
LaunchAgent is loaded, and no independent unattended implementation root is claimed. Preserve the
same SQLite, attempt history and usage when applying the approved bounded recovery. The frozen
supervisor still has automatic Astra review; remove it in a reviewed release before overnight Sol
operation. Exact evidence and limitations: `docs/evidence/brain-org-handoff-20261019.json`.

## Organization resource decision — 2026-10-20

Founder amendment `CEO-20261020-02` supersedes68/69% controls and72h/200-WPU/five-renewal limits.
Fresh account read:68%, ordinary use allowed. Productive work may use the available weekly quota,
with default1-point recovery reserve/productive pause99%, reduced concurrency at97%. Sol manages
routine planning renewals and qualified recovery after actual account reset; no automatic Astra.
Existing root `01a15657-3558-79c1-86dc-6ed033c264df` owns an actually active Pursuing Goal for the
full organization mandate, with predecessor identity preserved; no duplicate team. Actual Luna6/high
is repairing three findings from independent review of `0d74d69`; that candidate is not accepted.
Durable goal/worker/next-gate status is at
`/Users/root1/Developer/brain-org-state/founder-quota-20261020/sol-live-status.json`; exact API receipt
is adjacent `continuation-goal-receipt.json`. Pilot config/STOP and six failed attempts are preserved;
reviewed controller upgrade and operational qualification remain required. Prior source `b37eafa` and unreviewed proposal `1546e4c` remain intact. Original full goal
`01a1563e-1a26-75e2-bdab-ca2b38c7b51f` requires supported gated continuation, not an executive reset.
Policy: `docs/evidence/brain-org-founder-quota-amendment-20261020.json`.

Source repair `1d6d44f` has independent exact-pin Sol acceptance: 82 tests/six subtests,
six frozen checks, no-auto-Astra and shared direct-entry native/hash preflight. See
`docs/evidence/brain-org-accounting-source-acceptance-20261020.json`. Full owner Goal
`01a15657-3558-79c1-86dc-6ed033c264df` is active and an automatic continuation performed real
source repair. Pilot STOP/six failures/config remain; dispatch metering wiring, controlled upgrade,
same-DB recovery and the full mandate remain open. Task-spec validator is source-only/pending review.

## Git consolidation — 2026-10-18

`main` is the sole integration baseline on GitHub, incorporating research code/history through
`c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` plus test-only contract alignment (`d8fdff7`) and the consolidation documentation. Start a fresh
clone on `main`; use separate published task branches/worktrees for parallel development.

Retained topic heads: Linux `65846e8`, DR v3 scratch `6554a01`, and unfinished Control Center UI
`de4b42d`. Their implementation checkpoints remain `fb36e7e`, `4a84d5b`, and `ce8505a`.
`research/active` stays frozen at `c157f79` for recovery of the historical Migration v1 operator
package pinned at `6ad944c`; that old Mac2 package is retired. The current Kingston entrypoint uses
its unchanged v1 tools. Historical local commits and checkpoint objects are preserved by GitHub tag
`archive/pre-reset-20261018` (`b35f8d0`). See the
[branch/recovery registry](../docs/operator-runtime.md#git-integration-and-recovery-registry) for
purpose, acceptance gaps, recovery commands and the complete inventory receipt.

Git consolidation changed code custody only and preserved the original Migration v1 pins and
receipts. The subsequent migration-plan correction changes the destination/acceptance sequence;
the original operator package remains in Git history. No owner runtime, LaunchAgents, Kingston data,
protected data or deployment was changed.

Migration/DR, experience, Observatory Python, synthetic smoke and final Gecko contract gates passed.
Full-suite green is not claimed: 43 broad-run failures reproduce on old main and tree-wide Ruff
still reports 107 legacy findings. See the consolidation receipt for exact commands and failure
classification; no runtime implementation changed beyond the already published research checkpoint.

GitHub Actions run `39133861313` at consolidation checkpoint `a6ee5fc` could not start its jobs:
the account annotation reports failed recent payments or a spending limit requiring attention.
This is a CI infrastructure blocker, distinct from the locally measured legacy Ruff/test failures.
No billing/settings changes were made. The immutable `brain-git-consolidated-20261018` tag pins that
code/test checkpoint; subsequent changes here only clarify documentation.
