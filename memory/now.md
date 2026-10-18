# Current state

As of 2026-10-18, active scope remains **Practical Brain Migration v1**. Erasure remains
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

A separate Linux portability branch, `codex/linux-portability-20261018` at
`94495e34071a6cd5a3a10343f2c2f274885ea9a7`, is isolated from that acceptance path. Fresh Ubuntu
24.04 clones completed `make setup`; repeated builds across separate output directories and clean
clones matched wheel and sdist hashes. The exact-head release restored a synthetic ext4 fixture
inert, activated explicitly, stopped/finalized, then passed release and attachment verification
after reboot; its unit remained disabled/inactive. Focused Linux/build tests passed (75 passed, one
optional browser skip); changed-file lint/format and synthetic smoke passed. `make check` stops at
107 tree-wide Ruff findings outside the changed Linux files. A real ext4 lazy-detach writer rehearsal
confirmed held-descriptor behavior for mkdir/create/atomic rename, append rejection, and unchanged
underlay. The Linux UUID probe now cross-checks canonical `findmnt` identity with root-owned udev
links bound to exact major:minor, with fail-closed conflict/ambiguity handling; the link-only path
was exercised on a disposable loop device. Fresh setup, 78 focused tests, changed-file lint/format,
and byte-identical wheel/sdist builds passed at the new head. The disposable detach image, config,
and underlay were removed after readback; the earlier separate synthetic attachment lab remains
mounted and inert for follow-up lifecycle experiments. No provider credentials or protected data
were used. Exact evidence and limits are in
[`Linux portability receipt`](../docs/evidence/brain-linux-portability-2026-10-18.json). This is not
Migration v1 acceptance or qualification of production state, CUDA, ARM64, or Windows/WSL.

At exact source `df0a1a8`, a post-activation synthetic service SIGKILL left its disabled systemd
unit failed without restart. Verification failed closed until explicit `reset-failed`; finalization
then verified idle SQLite and inventory. This does not distinguish crash from clean stop or test a
crash before the idle-probe receipt. The diagnostic remains generic (`attachment_root_action_failed`).
See [`SIGKILL recovery receipt`](../docs/evidence/brain-linux-attachment-sigkill-recovery-2026-10-18.json).

At `94495e34071a6cd5a3a10343f2c2f274885ea9a7`, root actions now return only bounded allowlisted
diagnostics; the release ID is validated before deriving a privileged executable path. A synthetic
helper SIGKILL after systemd start but before idle-probe left the service active and the attachment
unretryable. Verification/finalization rejected it; after exact-unit stop, a fresh attachment passed
inert verify, explicit idle activation, stop/finalize, and read-only SQLite integrity. Evidence:
[`start-intent crash and recovery`](../docs/evidence/brain-linux-attachment-start-intent-recovery-2026-10-18.json).
Next horizon: interrupted release and attachment publication cleanup.

`research/active` is at `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186`; `main` remains
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Future-v2 v3 drafts remain untracked and untouched.
