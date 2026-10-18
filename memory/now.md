# Current state

As of 2026-10-18, practical reset preparation uses **Kingston + GitHub only**. Mac2 is removed
from the plan. The owner explicitly accepts Kingston as the sole physical data copy during reset.
No new development or service activation is authorized.

**Factory reset: GO within that accepted risk, while Brain remains quiescent.** The final
[readiness receipt](../docs/evidence/kingston-restore-ready-2026-10-18.json) records actual checks.
Start restoration at `/Volumes/Kingston/BrainRestore-20261018/START-HERE.txt`; its adjacent final
acceptance, checksums and current all-ref Git bundle complete the existing BrainData/candidate set.
The sealed kit, state supplements and five current private configurations are now on Kingston.
All 212,474 BrainData files were read/hashed; source membership remained stable. Both unchanged v1
candidates passed verification, 93 ordinary SQLite/WAL families passed disposable-copy checks,
and D43 passed an actual complete opaque inert restore. Extracted native Python/SQLite/OpenSSL,
offline ML and synthetic source smoke passed with original Homebrew and network access denied.

A full simultaneous ordinary restore did not fit current Mac1 free space. Clean-OS installation,
account/MFA reauthentication, hardware behavior and actual service activation remain post-restore
checks. Keep backups unchanged and all services inert until separate authorization. The preserved
50 GiB live-writer free-space guard still exceeds available Kingston space (~20 GiB); reclaim
redundant migration storage only after verified Mac1 recovery, before enabling guarded writers.

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
