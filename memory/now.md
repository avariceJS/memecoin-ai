# Current state

As of 2026-10-16, active scope is **Practical Brain Migration v1**; erase remains **NO-GO** until
real second-MacBook restore acceptance. `research/active` is the DR work branch; `main` remains
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Future hardened v2 handoff is
`docs/portability-disaster-recovery-v2-roadmap.md`.

The exact uncommitted v3 draft is durably archived in
`docs/archives/portability-disaster-recovery-v2/v3-capture-adapter-draft-2026-10-16.tar.gz` and
summarized in the future-v2 roadmap. Sol rejected production acceptance; public capture remains
fail-closed. Do not start a new protocol generation during Migration v1.

PersonalBackup remains untouched and outside inspection; its separate move/verification/deletion
transition must be confirmed before capacity is treated as available. Kingston `/BrainData` is not
to be overwritten. First prepare Migration v1 inventory and a non-overwriting manifest/copy/verify
flow; current owner Data and launchd writers have not been quiesced.
