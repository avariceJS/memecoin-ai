# Brain / Memecoin AI

One source checkout: `~/Developer/memecoin-research`, repository `avariceJS/memecoin-ai`.
`develop` contains Brain and the AI Engineering Organization; `main` is the stable baseline with
separate release authority. Read [AGENTS.md](AGENTS.md) and [current status](memory/now.md) first.

## Brain development

Python 3.11+; the Python runtime uses the standard library. Development dependencies are pinned.
On a new machine:

```sh
git clone --branch develop https://github.com/avariceJS/memecoin-ai.git ~/Developer/memecoin-research
cd ~/Developer/memecoin-research
make setup
make smoke
```

ML is optional: `make ml-setup` installs the separately pinned ML environment. Do not train, promote
models, access protected holdout, start collectors or enable trading without separate authority.
Synthetic smoke validates software behavior, not market quality or trading edge.
Use focused tests for the affected subsystem; `make check` is the broader repository gate.

## Paperclip source and paused runtime

Our exact Paperclip changes are a reproducible delta over pinned upstream, not a local Git bundle.
Install source on a new machine, then install/build its pinned dependencies:

```sh
.venv/bin/python -m engineering.paperclip.install_source
cd ../memecoin-runtime/paperclip
corepack pnpm install --frozen-lockfile
corepack pnpm --filter @paperclipai/plugin-sdk build
cd ../../memecoin-research
corepack pnpm --dir engineering/paperclip/autonomous-plugin install --frozen-lockfile
corepack pnpm --dir engineering/paperclip/autonomous-plugin build
```

Source reconstruction checks exact commit/tree/parent chain. Dependency installation and launch are
separate. Connect the existing private `memecoin-runtime/state/instances/lab` database, encryption key,
JWT secret and delegated subscription bindings independently; never create a replacement organization
while restoring its state. The reusable 52-profile configuration is in
`engineering/paperclip/organization/organization.json`; use existing native APIs and fresh readback.

The existing custody-bound launcher is:

```sh
.venv/bin/python -m engineering.paperclip_lab_server status
.venv/bin/python -m engineering.paperclip_lab_server start
.venv/bin/python -m engineering.paperclip_lab_server stop
```

Starting the server does not authorize company work. All companies remain paused until explicit Founder
launch. See [organization contracts and limits](docs/engineering-organization.md).

## Local state and research data

The sole private store is `~/Developer/memecoin-runtime`: Paperclip instance/dependency, durable receipts,
Brain owner/research state, secrets and one independent sanitized worker seed. `work` and `.env.local`
in the source checkout are local ignored links into that store. Keep owner/research env values separate.
The worker seed has no remote/credentials/alternates to canonical Git: native isolated worktrees need it
because their sandbox grants access to shared Git metadata. It is a reproducible execution cache, not a
second source of truth. Never make canonical `.git` writable to workers.

The canonical data root is `/Volumes/Kingston/BrainData`, bound to Kingston's original UUID/root marker.
Owner/research `work` links resolve to `BrainData/brain/{owner,research}/work`. Data, models, SQLite
families and protected candidates remain outside Git. Configuration defaults use the physical
`~/Developer/memecoin-runtime/brain/config` path: storage admission rejects symlink ancestors.
Keep the existing 50 GiB free-space guard and descriptor-bound writers. BrainDevelopment is a different
internal volume; its verified ordinary replica was removed. Only two opaque D43 custody containers
remain there (39.5 MiB allocated); uncertain protected Mac state remains private. Never hash/read
protected content merely to deduplicate it. Historical unknown-effect custody remains held.

The existing worker interpreter launcher and dependency profile are versioned under
`engineering/paperclip/worker-python*`. The private seed tracks its qualified `.runtime` capsule so
native isolated worktrees receive the interpreter; the capsule is excluded from public source.

[Documentation index](docs/INDEX.md) owns the short canonical reading map. The read-only Observatory
is documented in [apps/control-center/README.md](apps/control-center/README.md).
Apply CLEAN-AS-YOU-GO after accepted publication; no one-off reports or additional project copies.
