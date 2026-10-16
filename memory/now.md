# Current state

As of 2026-10-16, active scope is **Practical Brain Migration v1**. Factory reset remains **NO-GO**
until reset-unique state is restored and accepted on the second MacBook. Hardened DR is deferred to
[`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md);
its three v3 drafts are durably archived in the published v2 checkpoint.

The bounded v1 adapter is `tools/brain_migration_v1.py`. Ruff, formatting, `py_compile`, CLI help,
and 26 focused tests pass; Sol accepts it for one non-D43 byte-custody capture after fresh preflight.
It captures seven roots into a new Kingston candidate, verifies hashes, and restores only into a
new inert home quarantine. Latest metadata-only census: 28.694 GiB; current Kingston baseline:
199.086 GiB free, case-sensitive APFS UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`. No v1 payload
has been written yet. PersonalBackup and `BrainData` remain untouched.

Sixteen exact Brain writer labels are disabled/unloaded. `ai.memecoin.d43.calendar01` remains loaded
without a PID and is untouched; its exact `owner_work/calendar-execution-01` subtree is skipped
without descent. Any candidate is explicitly incomplete for protected custody; D43 recovery and a
real second-Mac restore remain outstanding. Do not erase or activate Brain.

`research/active` is the DR work branch; `main` remains `770699d3ef8558518ae750b6d5d6cab41c2e82c8`.
