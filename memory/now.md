# Current state

As of 2026-10-18, active scope remains **Practical Brain Migration v1**. Erasure remains
**NO-GO** until the second MacBook restores the full Kingston pair and passes the handoff checks,
then Sol independently reviews the transcript. Hardened DR is deferred to
[`portability-disaster-recovery-v2-roadmap.md`](../docs/portability-disaster-recovery-v2-roadmap.md).

The full current pair is recorded in
[`candidate-pair receipt`](../docs/evidence/portability-migration-v1-candidate-pair-2026-10-16T2217Z.json):
ordinary `d2fb11d73cd84c14a24ba3ed870b2ccb` (784,023 members, 28,821,482,721 bytes, manifest
`0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865`) plus opaque D43
`58ec529e5a134817bf179b06922ddcf7` (1,567 members, 13,227,642 bytes, manifest
`ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6`). Both passed fresh external
verification; 93 ordinary SQLite families passed integrity checks with unchanged family hashes.
Kingston has 138,415,222,784 free bytes. `BrainData` and PersonalBackup remain untouched.

D43 stays disabled and PID-free; its old partial `5d4804faa1f14c47a0e3ed652e758bdd` remains
incomplete and must not be restored. Failed ordinary partial `58e4596a3db5403a9b7901cfdb40566d`
also remains incomplete. Do not delete partial candidates or activate Brain. The published Mac2
operator package is awaiting final Sol review. No second-Mac restore has occurred.

A separate Linux portability branch, `codex/linux-portability-20261018`, is isolated from that
acceptance path. Fresh Ubuntu
24.04 clones completed `make setup`; repeated builds across separate output directories and clean
clones matched wheel and sdist hashes. The exact-head release restored a synthetic ext4 fixture
inert, activated explicitly, stopped/finalized, then passed release and attachment verification
after reboot; its unit remained disabled/inactive. Focused Linux/build tests passed (75 passed, one
optional browser skip); changed-file lint/format and synthetic smoke passed. `make check` stops at
107 tree-wide Ruff findings outside the changed Linux files. A real ext4 lazy-detach writer rehearsal
confirmed held-descriptor behavior for mkdir/create/atomic rename, append rejection, and unchanged
underlay. The Linux UUID probe now cross-checks canonical `findmnt` identity with root-owned udev
links bound to exact major:minor, with fail-closed conflict/ambiguity handling; the link-only path
was exercised on a disposable loop device. Fresh setup, 78 focused tests, changed-file lint/format,
and byte-identical wheel/sdist builds passed at the new head. The disposable detach image, config,
and underlay were removed after readback; the earlier separate synthetic attachment lab remains
mounted and inert for follow-up lifecycle experiments. No provider credentials or protected data
were used. Exact evidence and limits are in
[`Linux portability receipt`](../docs/evidence/brain-linux-portability-2026-10-18.json). This is not
Migration v1 acceptance or qualification of production state, CUDA, ARM64, or Windows/WSL.

At exact source `df0a1a8`, a post-activation synthetic service SIGKILL left its disabled systemd
unit failed without restart. Verification failed closed until explicit `reset-failed`; finalization
then verified idle SQLite and inventory. This does not distinguish crash from clean stop or test a
crash before the idle-probe receipt. The diagnostic remains generic (`attachment_root_action_failed`).
See [`SIGKILL recovery receipt`](../docs/evidence/brain-linux-attachment-sigkill-recovery-2026-10-18.json).

At `94495e34071a6cd5a3a10343f2c2f274885ea9a7`, root actions now return only bounded allowlisted
diagnostics; the release ID is validated before deriving a privileged executable path. A synthetic
helper SIGKILL after systemd start but before idle-probe left the service active and the attachment
unretryable. Verification/finalization rejected it; after exact-unit stop, a fresh attachment passed
inert verify, explicit idle activation, stop/finalize, and read-only SQLite integrity. Evidence:
[`start-intent crash and recovery`](../docs/evidence/brain-linux-attachment-start-intent-recovery-2026-10-18.json).
Release staging now has a finish-only v2 publish intent and an isolated root-owned state parent.
At exact `4b8c9b3`, 36 focused release/service tests passed on macOS and Ubuntu; real Ubuntu/ext4
SIGKILL tests after all four publication renames and after systemd reload recovered, repeated
recovery, retried the same source/wheel, and verified disabled/inactive/PID-0/zero-restart units.
Foreign paths, a tampered pin, and injected active/failed unit statuses were refused before other
artifacts published. One exact-head release was explicitly activated, stopped, verified idle, and
reused safely; it ended inert. This is process-crash evidence, not power-loss or block-device
failure qualification. See
[`release publication v2`](../docs/evidence/brain-linux-release-publish-v2-2026-10-18.json).
At exact `bc50bc3ac47c822070ab217ecd53bb8f4e104806`, a durable v2 publish intent survived an Ubuntu
reboot; SSH recovered via `ssh.socket`, `recover-stage`, same-source retry, and independent verify
passed, with the service still disabled/inactive and PID-free. See
[`v2 reboot receipt`](../docs/evidence/brain-linux-release-v2-host-reboot-2026-10-18.json). Next:
two clean clones at the same exact commit produced byte-identical wheel/sdist hashes after
independent `make setup`; see
[`source build reproducibility receipt`](../docs/evidence/brain-linux-source-build-reproducibility-2026-10-18.json).
This is one-host/one-toolchain evidence. At `9d25fe069cd13d91eaf51c298a0993cb31c9961b`, a real
full-loop-filesystem stage failure returned `stage_builder_no_space`; recovery and same-source retry
passed, and the original release tree was restored and reverified. See
[`ENOSPC receipt`](../docs/evidence/brain-linux-release-stage-enospc-recovery-2026-10-18.json).
Permission drift across a release file, v2 state root, and unit failed closed; exact modes were
restored and independent verify passed. See
[`permission drift receipt`](../docs/evidence/brain-linux-release-permission-drift-2026-10-18.json).
At exact topic commit `2fe5ea1d9c21ba8038b36c24b72c46bf734d8aca`, 15 previously verified inert v2
releases/state/units were removed; legacy v1, three unclassified paths, and eight fail-closed
prepared-intent fixtures were preserved. Fresh private clone setup/build/stage/verify passed,
explicit activation and stop passed, and after reboot SSH recovered while the release remained
disabled/inactive and verified. A second post-reboot clean clone matched wheel/sdist hashes. See
[`clean reinstall`](../docs/evidence/brain-linux-clean-v2-reinstall-2026-10-18.json) and
[`reset inventory`](../docs/evidence/brain-linux-clean-v2-reset-inventory-2026-10-18.json).
At `b0e77f5064fd757d8f606dfd6b0f8d2774767715` (topic branch), Linux release staging now has read-only attempt
audit and explicit exact-ID discard into retained quarantine. On the VPS, 3/37 journaled attempts
were eligible; one was qualified, two remain eligible, and 34 manual-review records remain
untouched. SIGKILL after discard intent and each of four renames replayed safely; normal recovery
refused throughout, and an active unit blocked discard. Quarantine retained 16.8 MB; no pruning
policy exists. The follow-on pre-intent recovery at `918611b3ae9d959a27e713ad376721e606e93008`
records a sealed prepare plan before external temp creation, metadata-only audit, all-artifact
preflight, and replayable retained quarantine. Three real SIGKILL windows (after state, pin, unit
temp writes) recovered and replayed; a later same-release stage/replay passed independent verify
with its unit disabled/inactive. See the
[`stage-discard receipt`](../docs/evidence/brain-linux-stage-discard-qualification-2026-10-18.json)
and [`pre-intent recovery receipt`](../docs/evidence/brain-linux-preintent-crash-qualification-2026-10-18.json).
Power-loss/storage failure and physical quarantine pruning remain unqualified. Next: stale
prepared-state lifecycle and upgrade recovery.

`research/active` is at `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186`; `main` remains
`770699d3ef8558518ae750b6d5d6cab41c2e82c8`. Future-v2 v3 drafts remain untracked and untouched.

At topic commit `c3173a0cda76ae22e902ec211161deade9add480`, Linux handoff recovery now requires an
exact generation-root member set, rejects nested mounts from live mountinfo, and preflights existing
unit bytes/inert systemd state before permission normalization. Ubuntu SIGKILL injections for a
foreign generation sibling and conflicting published unit were rejected while the generation stayed
0700; clean retries verified. Synthetic activate/stop/finalize/verify ended disabled, inactive, and
PID-free. Sol found no must-fix in the recovery boundary. Receipt:
[`handoff recovery boundary`](../docs/evidence/brain-linux-handoff-recovery-boundary-2026-10-18.json).
Next portability horizon: verify source/toolchain provisioning from a fresh minimal Ubuntu install,
including explicit package closure and behavior when optional host capabilities are absent.

At topic commit `fa5950ed7871c88051807bab1f3ece4bfd4a2a66`, macOS arm64 and clean Ubuntu x86_64 builds
produced byte-identical wheel and sdist artifacts after canonicalizing archive modes. A minimal
Ubuntu rootfs completed clean venv setup, first-boot stage/verify, explicit activation with a distinct
network namespace, stop, inert restart, and same-release restaging. The first package closure missed
Ubuntu's unversioned `python3`; the corrected setup passed. Manual package provisioning remains the
next meaningful gap. See
[`Linux clean-root receipt`](../docs/evidence/brain-linux-cleanroot-crosshost-qualification-2026-10-18.json).

At `412aeb985a73683a106c4a6cf0833961c2e91026`, the read-only Linux preflight checks the actual
`make setup` interpreter before venv creation. A minimal rootfs with `python3.12` but no `python3`
now reports the missing default, accepts an explicit interpreter override, and completes setup,
build, and smoke; restoring `python3` makes the default path pass. Adding `udev` closes the service
host capability report. Cross-host wheel/sdist hashes still match, and the new release passed
activation, private-netns, stop, and inert restart verification. Next: package provisioning design.
See
[`Linux bootstrap preflight receipt`](../docs/evidence/brain-linux-bootstrap-preflight-2026-10-18.json).

At `bdad490e079f84a1ebe92c9646bc6e8c6b6f33b0`, preflight imports the selected interpreter's real
`venv` and `ensurepip` modules read-only. The missing-default and explicit-override scenarios passed
on minbase; default and override setup/build/smoke passed from empty venvs. Clean Mac/Ubuntu wheel
and sdist hashes match. The latest release passed verify, explicit private-netns activation, stop,
restart, and read-only preflight with both required roles available. Next: assess opt-in package
provisioning. See
[`setup preflight deployment receipt`](../docs/evidence/brain-linux-setup-preflight-deployment-2026-10-18.json).

The next check found one missing readiness prerequisite: release staging requires an existing
non-root `brain` account. The read-only host report now fails closed when it is missing or UID 0;
the disposable rootfs account is present. Package provisioning remains explicit and will not create
accounts or grant sudo. Ubuntu's official snapshot service supports Noble; a separate explicit
index refresh is required before any plan/apply workflow can bind to immutable signed indexes.

The opt-in lab provisioner passes snapshot-bound `source-build` and `inert-service-host` package
roles on disposable Ubuntu systemd-nspawn roots. The service role explicitly allows `bsdutils` as
an Ubuntu Essential solver companion and replans to an empty transaction after application. A
fresh snapshot-built raw minbase has no Python 3.12 or systemd PID 1, so the tool cannot self-bootstrap
there; Sol set the entry contract to a separately built installer substrate, with a read-only
preflight before any writes. The test import-path collision is fixed; full-suite collection still
depends on optional NumPy and an absent legacy work artifact. Next: build the explicit substrate from
the same signed snapshot, then test package provisioning through first inert boot.

At topic commit `51306aa3629ed11df5991a70caf399feefff025b`, schema-v3 Linux release staging passed on
the VPS after fixing a builder wheelhouse permission defect. The installed venv entrypoints are
relocatable; two fresh pip installs normalized to the same inventory hash, and a same-filesystem
bind mount was rejected. All nine entry points ran, explicit activation and stop returned the static
unit to inert state, and a read-only deploy-key fresh clone reached this branch head. The failed
first attempt is retained in sealed quarantine. This is one Ubuntu/Python toolchain result; clean
host first boot, publish-crash recovery, cross-host build identity, and GPU remain unqualified. See
[`Linux release-v3 receipt`](../docs/evidence/brain-linux-release-v3-host-qualification-2026-10-18.json).
