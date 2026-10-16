# Current state

As of 2026-10-16, active scope is **Practical Brain Migration v1**. Factory reset remains **NO-GO**
until reset-unique state is restored and accepted on the second MacBook. Hardened DR is deferred to
[`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md);
its three v3 drafts are durably archived in the published v2 checkpoint.

The bounded v1 adapter is `tools/brain_migration_v1.py`. Ruff, formatting, `py_compile`, CLI help,
and 26 focused tests pass; Sol accepted a bounded non-D43 byte-custody capture. Candidate
`21843d9664ea45438fd79c9745a30fee` was internally readback-verified and independently verified
against manifest SHA `4d5346f052b4fc3943a599b35013f5d0d098d25bf817bb27a29421ba6a7eac55` (783,922
members; 28,820,413,514 bytes). Receipt:
`docs/evidence/portability-migration-v1-capture-2026-10-16T2030Z.json`. Kingston now has
182,458,507,264 free bytes. PersonalBackup and `BrainData` remain untouched.

Sixteen exact Brain writer labels are disabled/unloaded. `ai.memecoin.d43.calendar01` remains loaded
without a PID and is untouched; its exact `owner_work/calendar-execution-01` subtree is skipped
without descent. Any candidate is explicitly incomplete for protected custody; D43 recovery and a
real second-Mac restore remain outstanding. Do not erase or activate Brain.

`research/active` is the DR work branch; `main` remains `770699d3ef8558518ae750b6d5d6cab41c2e82c8`.
