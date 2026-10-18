# Current state

As of 2026-10-18, active scope is **Practical Brain Migration v1**. Erasure remains
**NO-GO** until the second MacBook restores the full Kingston pair and passes the handoff checks,
then Sol independently reviews the transcript. Hardened DR is deferred to
[`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md).

The full current pair is recorded in
[`candidate-pair receipt`](../docs/evidence/portability-migration-v1-candidate-pair-2026-10-16T2217Z.json):
ordinary `d2fb11d73cd84c14a24ba3ed870b2ccb` (784,023 members, 28,821,482,721 bytes, manifest
`0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865`) plus opaque D43
`58ec529e5a134817bf179b06922ddcf7` (1,567 members, 13,227,642 bytes, manifest
`ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6`). Both passed fresh external
verification; 93 ordinary SQLite families passed integrity checks with unchanged family hashes.
Kingston has 138,415,222,784 free bytes. `BrainData` and PersonalBackup remain untouched.

D43 stays disabled and PID-free; its old partial `5d4804faa1f14c47a0e3ed652e758bdd` remains
incomplete and must not be restored. Failed ordinary partial `58e4596a3db5403a9b7901cfdb40566d`
also remains incomplete. Do not delete partial candidates or activate Brain. The published Mac2
operator package is awaiting final Sol review. No second-Mac restore has occurred.

## Git consolidation — 2026-10-18

`main` is the sole integration baseline on GitHub, incorporating research code/history through
`c157f79471c1cb330bdab01cc4b4cdd7b9a1a186` plus test-only contract alignment (`d8fdff7`) and the consolidation documentation. Start a fresh
clone on `main`; use separate published task branches/worktrees for parallel development.

Retained topic heads: Linux `65846e8`, DR v3 scratch `6554a01`, and unfinished Control Center UI
`de4b42d`. Their implementation checkpoints remain `fb36e7e`, `4a84d5b`, and `ce8505a`. `research/active` stays frozen at `c157f79` because the unchanged Migration v1 operator
package fetches it before checking out pinned `6ad944c`. Historical local commits and checkpoint
objects are preserved by GitHub tag `archive/pre-reset-20261018` (`b35f8d0`). See the
[branch/recovery registry](../docs/operator-runtime.md#git-integration-and-recovery-registry) for
purpose, acceptance gaps, recovery commands and the complete inventory receipt.

This changes code custody only. Migration v1 candidate IDs, manifests, operator package and acceptance
status are unchanged. No Mac2 restore occurred; erase remains **NO-GO** pending that gate. No owner
runtime, LaunchAgents, Kingston data, protected data or deployment was changed.

Migration/DR, experience, Observatory Python, synthetic smoke and final Gecko contract gates passed.
Full-suite green is not claimed: 43 broad-run failures reproduce on old main and tree-wide Ruff
still reports 107 legacy findings. See the consolidation receipt for exact commands and failure
classification; no runtime implementation changed beyond the already published research checkpoint.
