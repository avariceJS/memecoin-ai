# Current Project State

**As of:** 2026-10-14 05:45Z. Portability/DR remains **NO-GO for factory reset**. Work is on
`research/active` at `4235d87f0b6a0d0db9f371f765665e29f9c5ee7e`; `main` remains the verified
baseline. Do not erase the Mac.

- Accepted case is owner-Mac loss with healthy Kingston. Kingston is the only reset-surviving
  physical copy; single-copy and plaintext risk are accepted. Internal Data has about 14 GiB free,
  no APFS snapshot, and no noninteractive admin path. No owner T exists or was attempted.
- `research/work/wallets` remains private, opaque, unresolved, and backup-required. No wallet payload
  has been read or copied. F01/D43/reserved holdouts remain protected; no semantic reader was used.
- Uncommitted work adds `tools/brain_host_tree_capture_v4.py` plus tests. Its first pass records only
  stat/type/aliases and non-secret symlink text. After cross-root hardlink reconciliation and
  `.env.local` exclusion, it captures metadata and streams bytes only for included objects. Identity
  and exact metadata obligations are rechecked; fresh readback verifies objects, manifest, receipt,
  and exact output membership. Cleanup covers interruption, source drift, and failed post-borrow
  validation. Darwin ACL values use versioned text form and unknown errors fail closed. The
  synthetic-only API refuses non-synthetic handles and cannot produce real T, Kingston custody,
  restore, or erase qualification.
- Sol accepted the bounded synthetic capture contract after the code closed the credential-read,
  metadata-integrity, failure-cleanup, ACL allocation, and complete readback findings. It establishes
  no real T, durable custody, restore, or erase readiness; owner-scale roster use and populated ACL
  /symlink xattr restore remain unqualified.
- Current scope: 71 focused attestor/v4 tests, Ruff, strict mypy for capture, and `git diff --check`
  pass. Work is uncommitted; next horizon is being selected with Sol.
- No provider requests, collectors, production behavior, services, protected evaluation, or trading
  state changed. Canonical DR handoff: `docs/portability-disaster-recovery.md`.

See `docs/INDEX.md` for canonical documentation ownership.
