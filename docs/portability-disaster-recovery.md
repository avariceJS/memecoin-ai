# Portability and local state

## Current layout — 2026-11-08

Source truth is `avariceJS/memecoin-ai`, `develop`, checked out at
`~/Developer/memecoin-research`. `main` has separate stable-release authority. The root README
owns new-machine source/dependency setup. No old Git bundle or Desktop checkout is required.

| Component | Required source/state |
|---|---|
| Brain and engineering implementation/tests/config templates | Published Git history and lockfiles |
| Paperclip | Pinned upstream plus `engineering/paperclip/source.fast-export.gz`; exact commit/tree admission |
| Organization installation | Sanitized 52-profile templates in `engineering/paperclip/organization/` |
| Existing company identities, tasks, holds, receipts, subscription bindings | Private `~/Developer/memecoin-runtime/state/instances/lab` database and encryption/JWT secrets |
| Operator, champion and research artifacts | `memecoin-runtime/brain/owner-work` and `brain/research-work`; retain SQLite/WAL/SHM as complete families |
| Host configuration and provider secrets | `memecoin-runtime/brain/config`, `memecoin-runtime/secrets`; owner/research env values remain separate |
| Large research data | Canonical `/Volumes/Kingston/BrainData`, original UUID/root identity; owner/research work links resolve here |
| Worker source isolation | One independent reproducible `memecoin-runtime/worker-source`; no remotes/credentials/alternates to canonical Git |
| Protected custody | Existing opaque state under the private Brain runtime and external data, never ordinary application input |

Local `work`, `.env.local` and `~/.config/brain-research` are compatibility links into this one
private store. Storage callers use the physical config path because the guard rejects symlink ancestors.
Dataset volume identities and sealed object pins remain unchanged. Historical signed
receipts retain their original paths and hashes; use explicit current locators rather than editing them.

## Recovery boundaries

Clone source and rebuild pinned dependencies, then connect the existing private runtime, secrets and
bound research volume separately. Do not create a replacement company/database or reinterpret repo HEAD
as the deployed champion. Restore an inert state: server startup never authorizes company/model work,
collectors, training, AUTO, paper ledger mutation, trading or paid APIs.

Preserve all independent STOP reasons, human/manual/unknown-effect holds, original rejections, fences,
pending intents and scientific role boundaries. Read native state and publication receipts before retry;
a failed process may already have published. A quota reset/reboot/reconnect never clears a STOP.

F01/D43/reserved holdout remains opaque: no application readers, hashing for this cleanup, relabeling,
training or evaluation. Existing protected custody whose duplication could not be established was kept: roughly 31 MiB of
Mac Brain state (including config/custody) and 39.5 MiB allocated in the separate internal
`/Volumes/BrainDevelopment/ResearchData`. These are explicit opaque exceptions, not ordinary data roots.
The original mixed host-recovery records on Kingston and personal Browser/Codex/Cursor archive are
retained. Nineteen original source-custody bundles tied to unresolved historical states remain private;
the current installation does not require them. No new bundle or archive was created.
No new backup, archive or persistent duplicate project was created by consolidation; the worker seed
is required because native worktree sandboxing grants write access to the shared Git metadata root.
Never bind workers directly to canonical `.git`.

Research live writers require verified volume/root identity and descriptor-bound writes; a path or
pre-write probe alone is insufficient. Preserve the existing 50 GiB writer guard. Do not substitute
BrainDevelopment for Kingston or fall back to internal storage. macOS/Linux host semantics, storage
admission, scientific promotion and operational release remain distinct gates.

## Versioned recovery tools

Existing `tools/brain_migration_v1*.py`, `brain_host_recovery.py`, `brain_host_restore_flow_v*.py`,
`brain_host_root_boundary_census_v1.py` and related metadata/SQLite tools remain for their substantive
contracts and tests. Their historical protocol defaults/rosters are not a current deployment recipe.
Use explicit current roots and the receipt-bound source commit for a historical reconstruction;
do not run old campaigns against current pointer text or removed Desktop paths. No host erase,
Recovery boot, protected capture or repeated migration is authorized by source cleanup.

The earlier long migration plans, host snapshots and review narratives remain in Git history at
`adba940` and earlier commits. Exact historical evidence stays immutable in `docs/evidence/` and the
private state. New-machine source reconstruction is tested; a full machine-loss recovery and clean-host
Linux deployment were not performed by consolidation.
