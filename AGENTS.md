# Control Center retained development branch

As of 2026-10-18, this branch preserves the unfinished CC02 reconstruction. The source checkpoint
before Git consolidation is `ce8505a6da99c5d5e1f118d1e55346f95981c328`; CC02.8 final integrated
visual acceptance remains open. This documentation update does not grant visual acceptance.

Start all general Brain work from GitHub `main`. The current architecture, authorization boundaries,
and branch registry live there. This old topic is for recovering and completing its UI changes;
do not treat its historical core state or Memory Bank as the current Brain baseline.
Read `apps/control-center/memory/STATUS.md`, `apps/control-center/memory/WORK_STATE.json`, and
`docs/control-center/ONBOARDING.md` for the UI checkpoint. Older model assignments, language rules,
and branch-management instructions in UI records are historical; current documentation is English,
and the Git policy below governs. The UI stays read-only against its approved pinned projection.
Do not read private datasets, protected F01/D43/holdouts, model payloads, credentials or owner work/.
No collector control, real transactions, production deployment or service activation is authorized.
Preserve provenance and immutable evidence; do not infer market quality from UI or synthetic tests.

## Git branch and push policy

- GitHub `avariceJS/memecoin-ai` is the source of truth for code and development history.
  `main` is the sole primary integration baseline. Start new tasks from freshly fetched `main`.
- Each active task or parallel agent may use its own short-lived branch and isolated worktree.
  Give each branch a bounded purpose and a clear owner; avoid overlapping writes.
- Commit and push meaningful progress regularly. Before handoff or host loss/reset, publish every
  irreplaceable code change. Incomplete work may be committed as an explicitly labeled checkpoint
  on its topic branch; a checkpoint is not acceptance and must not be promoted merely for custody.
- Integrate finished, compatible, checked work into `main`. Preserve published history; use merges
  or fast-forwards and never force-push `main` or rewrite shared commits.
- Keep experimental branches only while needed, publish them on GitHub, and document their purpose,
  acceptance gaps, and resume point in `docs/operator-runtime.md`. Do not maintain another general
  development baseline. `research/active` is temporarily frozen solely for the pinned Migration v1
  operator package; retire it only after that fetch dependency is safely removed or completed.
- After integration, verify ancestry and remote custody before deleting obsolete branches/worktrees.
  Preserve unique uncommitted files, reflog/checkpoint objects, and private data before cleanup.
  Immutable archival tags can retain historical checkpoints without creating active branches.
- Code must be recoverable after total Mac loss from GitHub. Runtime state, datasets, model artifacts,
  protected data and credentials remain outside Git and follow their separate custody/reissue rules.
- Updating `main` does not deploy or authorize activation. Keep owner runtime checkouts pinned and
  separate; never tie a branch push or development checkout update to collectors, trading or services.

