# Current Project State

**As of:** 2026-10-14 13:32Z. Portability/DR remains **NO-GO for factory reset**. Do not erase
the owner Mac. The user has resumed bounded work without Recovery reboot for now and expects to
interrupt again in roughly 1.3–2 hours. No new prospective collector, production, champion,
protected evaluation, paper, or trading state was changed.

- `research/active` is clean and synchronized with `origin/research/active`; read Git for the exact
  current tip at handoff. The last DR implementation commit is
  `04cbf2b3c3d2dc9a83745f8da19fb19f46702b53`. `main` and `origin/main` remain at
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`.
- Data identity remains UUID `430740ca-42e7-4e6c-a07a-e4e7370152cc`, APFS group UUID equal to the
  Data UUID, container UUID `414bc719-2fea-44a6-9952-d12cd31cb0ca`, device `disk3s5`. It is
  FileVault-protected, currently unlocked and writable. At 13:32Z Data had 15,418,336 KiB free
  (97% used); `diskutil apfs listSnapshots disk3s5` reported none. No source capture was attempted.
- Kingston is **not currently present/mounted**. `/Volumes` has no Kingston entry and `diskutil
  list` shows only internal storage and the ChatGPT Installer image. Do not write or mount by
  assumption. Last known accepted binding remains volume UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, BrainData root ID
  `9428695af6d4f46c08e1a444cdc66de8`; storage capacity and artifacts need a fresh check when it is
  attached. This prevents publishing the private locator manifest needed for the current census.
- A live, aggregate-only, metadata/no-follow descendant census examined owner checkout, research
  checkout, verifier worktree, private Brain config, LaunchAgents, and the linked Codex worktree.
  Counts respectively: 331,267 entries (21 symlinks; one `.env.local` excluded); 431,714 (120
  symlinks; one `.env.local` and three opaque `wallets` subtrees excluded); 1,685 (no symlinks); 88
  (no symlinks); 60 (no symlinks); and 12,231 (11 symlinks; `.local` subtree excluded). No
  cross-device descendants or unreadable entries were observed; held root descriptors were stable.
  Payload bytes, link targets, xattr values, and credential values were not read. This was an
  unreceipted aggregate-only live pass: no private locators were retained, child-level changes,
  firmlinks/mount aliases, exact host-tool root coverage, owner-wide closure, and a common source
  point remain unproven. Do not treat it as a recovery boundary. Sol reviewed and classified it as a
  provisional aggregate topology sample; counts are not comparable with earlier work-only scans.
- Read-only owner runtime status at 13:32Z: 18 Brain/D43 labels loaded, nine with PIDs, and 74
  processes with cwd under configured owner/research roots. No process arguments or environments
  were read; this is not inactivity evidence.
- Recovery first-action observer remains read-only and sequential, requires independently pinned
  Data/group/container/device identity, and cannot establish prior mount history or SAFE TO ERASE.
  No Recovery reboot was performed. Recovery boot/history, qualified Data T, complete owner and
  protected custody, real Kingston K, and authenticated compatible-host inert restore remain open.
- Next bounded scope: when Kingston is attached, revalidate its pinned UUID/root and available
  capacity, then rerun the declared-root census with a private locator manifest written through the
  bound storage interface and an aggregate-only receipt. Preserve exclusions for `.env.local`,
  opaque `wallets`, and Codex `.local`; retain NO-GO absent source-point and recovery gates.
  The user-assisted Recovery rehearsal remains a later, separately gated horizon.
