# Current state

As of 2026-10-17, active scope is **Practical Brain Migration v1**. Erasure remains **NO-GO** until
the full reset-unique candidate restores and passes acceptance on the second MacBook. Hardened DR
work is deferred to [`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md).

Sol accepted D43's bounded continuation path. The first live capture was interrupted during copy
because its target identity guard called `diskutil` for every member; source data was untouched.
Incomplete candidate `5d4804faa1f14c47a0e3ed652e758bdd` has pause records but no manifest or seal.
The D43 job was left disabled; do not re-enable it. A Sol-reviewed continuation revalidates its
pause metadata and fresh stable source/target identity, leaves the old payload unopened, and writes
a new candidate with interval-based target identity checks. The continuation implementation is
being checkpointed before its one bounded live retry.

The ordinary candidate `21843d9664ea45438fd79c9745a30fee` is stale and excludes D43. Do not restore
it. SQLite verification passed for 93 ordinary databases on that old candidate only; repeat on the
final paired v1 capture. Kingston `BrainData` and PersonalBackup remain untouched. There is no full
candidate pair or second-Mac restore yet.

Branch is `research/active`; `main` remains `770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Commit
`54785829248d401475ee8bbbce20f15232a714e4` is the pre-continuation base. Do not activate Brain.
