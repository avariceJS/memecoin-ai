# Current Project State

**As of:** 2026-10-14 10:08Z. Portability/DR is **NO-GO for factory reset**. Do not erase the
owner Mac. The accepted Mac-loss scope assumes Kingston remains healthy; its single-copy and plaintext
risks are accepted. F01/D43/reserved material remains protected and was not read.

- A first-host code entry card is on Kingston at `BrainData/host-recovery/ENTRY.txt`. Its source
  revision is `edf2898ba4fb877ed8bee159fc9673754f7e12e4`; SHA-256 is
  `ac8859ee4fc499ffe55dc1824be11a55fd4475fee2dbe0cae749a7f6cbbb5b76`. Verify the hash before
  following the card. Keep these pins in an independent handoff; none has been created yet.
- `tools/brain_host_recovery_preflight_v1.py` is a read-only, sequential Recovery first-action
  observer. It requires separately pinned Data UUID/group/container/device values, fails closed on
  unmapped APFS aliases, never unlocks or mounts, and never proves prior mount history or erase
  readiness. Sol reviewed the implementation; 17 focused tests, Ruff/format, and mypy pass. A live
  current-host probe matches identity but returns `blocked_unlocked`; Data is mounted writable.
  Do not reuse the current-OS result as Recovery evidence. Details: `docs/portability-disaster-recovery.md`.
- Current Data: UUID `430740ca-42e7-4e6c-a07a-e4e7370152cc`, group UUID currently the same,
  container UUID `414bc719-2fea-44a6-9952-d12cd31cb0ca`, device `disk3s5`; FileVault enabled,
  unlocked and mounted writable, zero snapshots observed. At 10:07Z it had 16,511,956 KiB free
  (97% used). No source snapshot/capture was attempted. The boot-to-probe mount history is unknown;
  `no_prior_writable_mount_proven=false`.
- Kingston is mounted read/write at `/Volumes/Kingston`, UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`; known `BrainData` root ID is
  `9428695af6d4f46c08e1a444cdc66de8`. At 10:07Z it had 211,808,096 KiB free (about 202 GiB).
  It remains the only reset-surviving physical copy.
- Read-only owner runtime status: 18 Brain/D43 LaunchAgent labels loaded, eight with PIDs; 64
  owner/research cwd-matched processes. No arguments or environments were read. DR changed no
  services, production behavior, provider state, protected evaluation, paper state, or trading.
- Git: `research/active` and `origin/research/active` are at
  `254ddc79e58eca4811c8d11d6869b6f30825469e`; worktree clean. `main` and cached `origin/main`
  remain at `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; main did not move.
- Remaining real gates: user-assisted Recovery boot/history boundary; qualified Data T; complete
  owner/protected custody and restore witness; real Kingston K frontier; authenticated compatible-host
  inert restore. Research `work/wallets` remains private, opaque, unresolved, and backup-required.
  Single-Kingston and plaintext risks are accepted, not blockers.
- Exact next decision horizon: when the user can enter Recovery, record shutdown/authentication steps;
  run the pinned observer before any Data action; stop on any identity mismatch, non-Locked state,
  mount, or unresolved APFS alias. Inspect earliest boot-session mount events and gaps. If no-prior
  writable mount cannot be evidenced, keep it explicitly as an assumption and do not qualify T.
  Continue only after that gate; no factory reset is authorized.
