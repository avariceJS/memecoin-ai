# Current state

As of 2026-10-18, active scope is **Practical Brain Migration v1**, documentation/planning only:
**Mac1 → Kingston → temporary backup custody on corporate Mac2 → reset Mac1 → inert restore on Mac1
→ separately authorized return to operation**. Mac2 is storage only; no Brain environment, services,
training or development there. The [canonical six-phase plan](../docs/portability-disaster-recovery.md)
replaces the former Mac2 deployment gate. Hardened DR remains deferred.

**Factory reset: NO-GO.** The pinned pair passed full capture/readback verification and 93 ordinary
SQLite structural checks, but the final source inventory/delta, exclusions, complete BrainData
second-copy coverage, corporate policy, Mac2 destination readback, dependency/service recovery kit,
external account/key access and ordinary WAL/frontier comparison remain open. No full restore has
occurred. A storage-only Mac2 needs no application deployment; full functional acceptance belongs
on restored Mac1. The plan distinguishes pre-reset custody acceptance from post-reset functionality.

The unchanged [candidate-pair receipt](../docs/evidence/portability-migration-v1-candidate-pair-2026-10-16T2217Z.json)
pins ordinary `d2fb11d73cd84c14a24ba3ed870b2ccb` and opaque D43
`58ec529e5a134817bf179b06922ddcf7`; exact hashes, counts and historical free space are in the plan.
The pair does not include the whole BrainData corpus. Even ordinary custody contains potentially
protected material; employer permission is needed before any Mac2 transfer, including encrypted
custody. If prohibited, use an approved independent destination. D43's last recorded state is disabled
and PID-free; re-read before operations. Keep all existing candidates and BrainData unchanged.

## Git consolidation — 2026-10-18

`main` is the sole integration baseline on GitHub, incorporating research code/history through
`c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` plus test-only contract alignment (`d8fdff7`) and the consolidation documentation. Start a fresh
clone on `main`; use separate published task branches/worktrees for parallel development.

Retained topic heads: Linux `65846e8`, DR v3 scratch `6554a01`, and unfinished Control Center UI
`de4b42d`. Their implementation checkpoints remain `fb36e7e`, `4a84d5b`, and `ce8505a`.
`research/active` stays frozen at `c157f79` for recovery of the historical Migration v1 operator
package pinned at `6ad944c`; the new plan retires that package, but replacement execution is not yet
qualified. Historical local commits and checkpoint objects are preserved by GitHub tag `archive/pre-reset-20261018` (`b35f8d0`). See the
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
