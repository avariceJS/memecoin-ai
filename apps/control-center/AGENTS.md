# Control Center agent rules

Root AGENTS.md and current user instructions apply. This is the UI scope inside the integrated
Brain repository, not a separate old-core checkout. Current documentation is English.

## Entry and ownership

Read root memory/now.md, docs/INDEX.md, this file, README.md here and
../../docs/control-center/ONBOARDING.md. For visual work, additionally read the CC02 audit,
plan, visual acceptance and design reference, and personally inspect relevant PNGs.
UI WORK_STATE.json owns pending UI tasks, not project/runtime truth. Archived reviews and
pre-consolidation model/branch instructions are historical evidence, not current authorization.

## Boundaries

- UI work owns apps/control-center/**, docs/control-center/**, its scoped Cursor rule and six
  named cc agent definitions. Core changes require the root integration task/review, as in this
  consolidation; an ordinary UI task must propose missing interfaces instead of bypassing scope.
- Runtime adapter reads only published/observatory-v1.json and its manifest. Offline export reads
  only the nine explicitly pinned committed engineering documents/configs. Never follow paths
  inside their JSON; do not repin or edit projection values silently.
- No UI access to work/, raw D43/F01/holdout, weights, credentials, wallets, live owner SQLite,
  collector control, shell execution, RPC, training, trading, promotion, STOP or LaunchAgents.
- Do not import core Python in the UI adapter. Missing/live/replay functionality stays unavailable.
  Engineering evidence is neither current runtime health nor permission to train/trade.
- Preserve the loopback, origin, verb, bounded/no-follow IO and protected-field allowlists.

## Development

Use `~/Developer/memecoin-research` on `develop` or an assigned temporary task branch.
`main` is stable and remains unchanged. Old control-center/observatory-handoff is historical.
Scope guard defaults to HEAD for pending changes; --base REF checks a committed review range.
Run UI Python boundary/workflow tests, exact projection reproduction, TypeScript check/tests/build.
Do not convert green tests into a visual PASS: CC02 remains unimplemented and unaccepted.

Use current session model/delegation policy. Grok is disabled; named role files do not force a
model or require delegation. Helpers get bounded file leases; only the integrator edits Git,
shared package locks and task state. Never fabricate an observed model binding.
Keep current operational facts in root canonical docs, UI interface facts in README.md here;
preserve imported audit receipts, before images and failure history without rewriting them.
