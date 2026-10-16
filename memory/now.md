# Current state

As of 2026-10-17, active scope is **Practical Brain Migration v1**. Factory reset remains **NO-GO**
until reset-unique state is restored and accepted on the second MacBook. Hardened DR is deferred to
[`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md);
its three v3 drafts are durably archived in the published v2 checkpoint.

The ordinary v1 adapter is `tools/brain_migration_v1.py`. D43 opaque custody
`tools/brain_migration_v1_d43.py` was accepted by Sol for one bounded real capture attempt and
published at `176ef0d`. It has durable pre-disable intent, an exact disable-only pause, descriptor-
based copy/readback, fresh verification, inert restore, and synthetic success/failure E2E coverage.
Focused v1/D43/SQLite checks pass (43 tests), with Ruff, formatting, and `py_compile`. No live D43
pause/capture has occurred yet. Candidate
`21843d9664ea45438fd79c9745a30fee` was internally readback-verified and independently verified
against manifest SHA `4d5346f052b4fc3943a599b35013f5d0d098d25bf817bb27a29421ba6a7eac55` (783,922
members; 28,820,413,514 bytes). Receipt:
`docs/evidence/portability-migration-v1-capture-2026-10-16T2030Z.json`. Kingston now has
182,458,507,264 free bytes. PersonalBackup and `BrainData` remain untouched.

The prior candidate is stale after the new code commit and must not be restored on Mac2. Recheck the
live writer inventory before capture. Ordinary SQLite state is copied without checkpointing; the
final restored copy must pass the read-only database-family verifier. D43 remains opaque-only, and
protected SQLite semantics are excluded. No complete candidate pair or second-Mac restore exists;
erase remains NO-GO. Do not activate Brain.

`research/active` is the DR work branch at `176ef0d`; `main` remains
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`.
