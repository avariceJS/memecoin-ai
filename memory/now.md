# Git recovery entry — 2026-10-18

This published topic is retained for unfinished work: DR v3 descriptor-bound scratch prototype; physical APFS/HFS and owner-scale acceptance remain open.
Its pre-consolidation code checkpoint is `4a84d5bd1496bbd6bb8832391edebd9bf51b845a`; this follow-up changes documentation only.
Start general development from GitHub `main`. The current branch registry and recovery archive are
[on main](https://github.com/avariceJS/memecoin-ai/blob/main/docs/operator-runtime.md#git-integration-and-recovery-registry).
`research/active` is frozen only for the unchanged Migration v1 operator package. Do not develop there.
The dated evidence below retains its original commit scope; advancing this documentation does not
requalify a Linux release, DR capture, Migration v1 acceptance or owner runtime.

# Current state

As of 2026-10-17, active scope is **Practical Brain Migration v1**. Erasure remains
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

`research/active` is at `feb2cccebb023aa1a484909cccc5b47e7d2fd4ea`; `main` remains
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Future-v2 v3 drafts remain untracked and untouched.
