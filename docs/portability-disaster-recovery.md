# Portability, Migration, and Disaster Recovery

## Active scope: practical Migration v1

The current user-approved objective is a conservative copy-and-restore migration to the second
MacBook, not formal hardened DR qualification. Preserve reset-unique Brain-owned state on Kingston,
verify the copy, restore and verify it on the second MacBook, and prove the restored Brain is inert.
This remains **NO-GO for erasing the first Mac** until real second-MacBook acceptance passes. Do not
require RecoveryOS qualification, formal `T/K`, or the v3 universal capture adapter for v1. The
deferred hardened capability is owned by
[`portability-disaster-recovery-v2-roadmap.md`](portability-disaster-recovery-v2-roadmap.md).

Git is canonical code/history; Kingston `/Volumes/Kingston/BrainData` is the recovery data tier.
Migration v1 copies, never moves or deletes source state. It must not overwrite existing BrainData
or inspect/change PersonalBackup. The user authorizes a separate v1 copy while PersonalBackup remains
on Kingston; its occupancy is already reflected in fresh free-space measurements, so do not reserve
or estimate it separately. Exclude known secrets and session stores for reauthentication/reissue.
Restore into quarantine, verify integrity and required state, bootstrap and smoke-check, and prove no
service or Brain work auto-started. Restore and Start remain separate.

The bounded implementation is [`tools/brain_migration_v1.py`](../tools/brain_migration_v1.py):
create-only capture, externally pinned verification, and restore into a new inert home quarantine.
It inventories seven roots: owner `work/` and `.git`, research `work/` and `.git`,
`~/.config/brain-research`, the saved Recovery terminal transcript, and Brain LaunchAgents. It copies
hardlink aliases as independently hashed files; topology and aliases outside the selected roots are
unresolved. Private configuration remains quarantine/reissue-only because filename rules cannot
prove it contains no unknown secrets. SQLite/WAL files are opaque bytes; application consistency is
not claimed. Symlink targets and filesystem metadata restoration are not proved.

The initial ordinary candidate excludes `owner_work/calendar-execution-01` without descent and
reports `protected_custody_complete=false`; it cannot be used alone for the second-Mac acceptance
run. Sol accepted `tools/brain_migration_v1_d43.py` for one bounded real D43 opaque-custody capture
attempt. The full v1 candidate is the separately pinned ordinary candidate plus
the D43 overlay, with both manifest hashes and member counts in the operator handoff.

The latest metadata-only seven-root census gave a conservative copy-budget basis of 30,810,427,392
bytes (28.694 GiB), below the 35-GiB cap. On 2026-10-16, a fresh preflight measured 213,921,214,464
free bytes on case-sensitive APFS UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`; this included
existing PersonalBackup occupancy. The writer retains a 50-GiB free floor and an 8-GiB operation
reserve. It created and internally readback-verified candidate
`21843d9664ea45438fd79c9745a30fee` with 28,820,413,514 payload bytes and 783,922 members; a
separate read-only verifier matched every member and the externally pinned manifest SHA-256. Free
space after verification was 182,458,507,264 bytes. That ordinary candidate is stale after the
continuation code checkpoint and excludes D43; do not restore it. A D43 live capture on 2026-10-17
was interrupted during copy after a per-member target identity guard proved prohibitively slow. It
left source data untouched and produced incomplete candidate
`5d4804faa1f14c47a0e3ed652e758bdd`, with durable pause records but no manifest or completion seal.
The exact D43 label remains disabled; no automatic resume occurred. A new Sol-reviewed bounded
continuation path revalidates old pause-record hashes, fresh source stability and target identity,
and never opens the old partial payload. The new D43 overlay and a fresh seven-root ordinary
candidate now form a separately pinned pair; neither has been restored on Mac2. See the earlier
[ordinary capture receipt](evidence/portability-migration-v1-capture-2026-10-16T2030Z.json) for the
stale excluded-D43 candidate and the new [candidate-pair receipt](evidence/portability-migration-v1-candidate-pair-2026-10-16T2217Z.json) for current pins.

### Migration v1 protected custody and database consistency

The D43 component's claim is limited to byte custody, symlink text, source identity/lineage, and an
inert restore. It records and readback-verifies a durable pause intent before disabling exactly
`ai.memecoin.d43.calendar01`; it does not kill or unload the job and never automatically resumes it.
It requires the job PID-free, writer-free, and source metadata stable for the configured interval,
rejects nested filesystems, pins the exact service plist, checks Kingston identity/free-space while
copying, and seals only after readback verification. After any failure, retain the incomplete
candidate and pause records, inspect the exact label, and make no automatic service change. After a
successful capture, run a fresh verify with the externally recorded manifest SHA and confirm the
same label is still disabled and PID-free. This does not inspect D43 semantics, validate protected
SQLite databases, restore macOS metadata, activate the service, establish global migration
completeness, or authorize erasure.

If a D43 capture is interrupted or fails after the disable, retain that incomplete candidate and
leave the exact label disabled. Do not re-enable it to retry. The explicit
`capture --paused-candidate-id <ID>` continuation accepts only the exact incomplete marker set with
durable pause records and no manifest or completion seal; it never enters the prior partial payload.
It rechecks the original pause-record digests, target/plist/source identities, disabled PID-free
state, writer absence, and a fresh stable metadata interval before creating a new candidate. The
new manifest and `PAUSE_CONTINUATION.json` bind the old candidate ID and all three pause-record
digests. Both the old partial and new candidate remain on Kingston; only the new externally pinned
candidate may qualify for restore.

For ordinary SQLite state, stop Brain-owned writers before capture and preserve each database and
existing `-wal`, `-shm`, and `-journal` companion as opaque bytes. Do not checkpoint or otherwise
mutate a source database during capture. On the restored copy, run
[`brain_migration_v1_verify_sqlite.py`](../tools/brain_migration_v1_verify_sqlite.py): it hashes
each ordinary database family against the manifest, opens each database read-only with SQLite's
immutable URI mode for `PRAGMA integrity_check`, and requires all family hashes to remain unchanged.
This is a practical structural check after restore, not a substitute for a stopped-writer capture
boundary. Protected database semantics remain unchecked. The earlier 93-database pass on
`21843d9664ea45438fd79c9745a30fee` was stale and is not relied upon; the fresh ordinary candidate
`d2fb11d73cd84c14a24ba3ed870b2ccb` now passes the same 93-database read-only check. Repeat that
check on the restored copy on the second MacBook.

### Migration v1 second-Mac acceptance package

The full current Kingston pair is ready for one real Mac2 restore attempt from pinned code commit
`6ad944c40938e1e25d2bddb3ea61234c69e9149d`. The ordinary candidate is
`d2fb11d73cd84c14a24ba3ed870b2ccb`, manifest SHA-256
`0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865`, 784,023 manifest members,
and 28,821,482,721 payload bytes. The separate opaque D43 overlay is
`58ec529e5a134817bf179b06922ddcf7`, manifest SHA-256
`ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6`, 1,567 manifest members,
and 13,227,642 payload bytes. Together they contain 785,590 manifest member records, with
overlapping directory records included in that sum. Both passed capture readback and fresh
externally pinned verify. The ordinary payload's 93 database families passed read-only SQLite
integrity checks and retained their manifest hashes. The immutable
[candidate-pair receipt](evidence/portability-migration-v1-candidate-pair-2026-10-16T2217Z.json)
records the source inventory, D43 lineage, incomplete prior attempts, and exact results.

Do the acceptance once, on the second MacBook. Prerequisites: compatible macOS, Python 3.11 or
newer, non-interactive GitHub access for this repository, and network access for the locked package
setup; do not type credentials into the captured terminal. Kingston must be mounted at
`/Volumes/Kingston` with the exact UUID below and case-sensitive APFS, at least 58 GiB free on
Kingston (50-GiB floor plus 8-GiB operation reserve), and at least 45 GiB free in the Mac2 home
volume after checkout/setup. The D43 `protected-candidates` directory and pinned candidate directory
must already be owned by Mac2's effective UID with mode `0700`; the preflight only reads these
attributes and stops on mismatch. The restore commands only read Kingston and create new user-owned
quarantine directories; they do not install or load LaunchAgents. Do not proceed if the preflight
finds an active Brain process or loaded Brain launchd label.

Start a full private terminal transcript at
`$HOME/BrainMigrationV1-acceptance-20261016/mac2-restore-transcript.txt` using macOS `script`.
The unique directory creation intentionally fails if this path already exists, so the transcript
cannot overwrite earlier evidence. Run the following inside that captured shell, and type `exit`
when the package finishes:

```sh
umask 077
mkdir -m 700 "$HOME/BrainMigrationV1-acceptance-20261016"
test ! -e "$HOME/BrainMigrationV1-acceptance-20261016/mac2-restore-transcript.txt"
script -q "$HOME/BrainMigrationV1-acceptance-20261016/mac2-restore-transcript.txt"
```

```sh
bash -s <<'BRAIN_MIGRATION_V1'
set -euo pipefail
ROOT="$HOME/BrainMigrationV1-acceptance-20261016"
REPO="$ROOT/repo"
HEAD="6ad944c40938e1e25d2bddb3ea61234c69e9149d"
ORDINARY="d2fb11d73cd84c14a24ba3ed870b2ccb"
ORDINARY_SHA="0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865"
D43="58ec529e5a134817bf179b06922ddcf7"
D43_SHA="ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6"
KINGSTON="/Volumes/Kingston"
Q="$ROOT/quarantine"
export GIT_TERMINAL_PROMPT=0

expect_json() {
  local expected="$1"
  shift
  local output
  output="$("$@")"
  printf '%s\n' "$output"
  EXPECTED_JSON="$expected" ACTUAL_JSON="$output" python3 - <<'PY'
import json, os
expected = json.loads(os.environ['EXPECTED_JSON'])
actual = json.loads(os.environ['ACTUAL_JSON'])
if any(actual.get(key) != value for key, value in expected.items()):
    raise SystemExit(f"unexpected_result_fields: expected={expected!r}, actual={actual!r}")
print({'expected_fields_verified': sorted(expected)})
PY
}

python3 -c 'import sys; print(sys.version); raise SystemExit(0 if sys.version_info >= (3, 11) else 2)'
git clone --no-checkout https://github.com/avariceJS/memecoin-ai.git "$REPO"
git -C "$REPO" fetch origin research/active
git -C "$REPO" checkout --detach "$HEAD"
test "$(git -C "$REPO" rev-parse HEAD)" = "$HEAD"
test -z "$(git -C "$REPO" status --porcelain)"
cd "$REPO"
make setup
make smoke

diskutil info "$KINGSTON" | grep -E 'Device Identifier|Mount Point|File System Personality|Volume UUID|Volume Read-Only'
diskutil info "$KINGSTON" | grep -q 'Case-sensitive APFS'
python3 - <<'PY'
import os, plistlib, shutil, stat, subprocess
from pathlib import Path
mount = Path('/Volumes/Kingston')
info = plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil', 'info', '-plist', str(mount)]))
assert info.get('VolumeUUID', '').lower() == '8f8f1081-9be4-4914-9111-75f1b90255bf'
assert info.get('MountPoint') == str(mount)
assert 'apfs' in str(info.get('FilesystemType', info.get('FilesystemName', ''))).lower()
free = shutil.disk_usage(mount).free
print({'kingston_uuid': info['VolumeUUID'], 'filesystem': info.get('FilesystemType'), 'free_bytes': free})
assert free >= 58 * 1024**3
for path in (mount / 'BrainMigrationV1' / 'protected-candidates', mount / 'BrainMigrationV1' / 'protected-candidates' / '58ec529e5a134817bf179b06922ddcf7'):
    metadata = path.stat(follow_symlinks=False)
    print({'path': str(path), 'uid': metadata.st_uid, 'mode': oct(stat.S_IMODE(metadata.st_mode))})
    assert stat.S_ISDIR(metadata.st_mode)
    assert metadata.st_uid == os.geteuid() and stat.S_IMODE(metadata.st_mode) == 0o700
home_free = shutil.disk_usage(Path.home()).free
print({'mac2_home_free_bytes': home_free, 'required_bytes': 45 * 1024**3})
assert home_free >= 45 * 1024**3
PY

brain_runtime_check() {
  python3 - <<'PY'
import json, os, re, subprocess
raw = subprocess.check_output(['/bin/launchctl', 'list'], text=True)
jobs = []
for line in raw.splitlines():
    fields = line.split()
    if len(fields) == 3 and fields[2].startswith('ai.memecoin.'):
        jobs.append({'label': fields[2], 'pid': fields[0]})
comm_raw = subprocess.check_output(['/bin/ps', '-axo', 'pid=,comm='], text=True)
args_raw = subprocess.check_output(['/bin/ps', '-axo', 'pid=,args='], text=True)
def by_pid(raw):
    result = {}
    for line in raw.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2:
            result[fields[0]] = fields[1]
    return result
commands = by_pid(comm_raw)
arguments = by_pid(args_raw)
brain_candidates = []
other_python_pids = []
brain_executable_markers = ('brain', 'memecoin')
brain_argument_markers = ('memecoin_ai', 'memecoin_brain', 'memecoin_collector', 'brain_migration_v1', 'calendar-execution-01')
for pid, command in commands.items():
    if pid == str(os.getpid()):
        continue
    executable = os.path.basename(command)
    folded_executable = executable.casefold()
    folded_args = arguments.get(pid, '').casefold()
    if any(marker in folded_executable for marker in brain_executable_markers) or any(marker in folded_args for marker in brain_argument_markers):
        brain_candidates.append({'pid': pid, 'executable': executable})
    elif re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', folded_executable):
        other_python_pids.append(pid)
print(json.dumps({'brain_launchd_jobs': jobs, 'brain_process_candidates': brain_candidates, 'other_python_pids': other_python_pids}, sort_keys=True))
if jobs or brain_candidates or other_python_pids:
    raise SystemExit(2)
PY
}
brain_runtime_check

expect_json '{"manifest_sha256":"0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865","member_count":784023,"payload_bytes":28821482721,"protected_custody_complete":false,"protected_custody_disposition":"protected_D43_source_present_uncaptured"}' python3 tools/brain_migration_v1.py verify --target-root "$KINGSTON" --candidate-id "$ORDINARY" --manifest-sha256 "$ORDINARY_SHA"
expect_json '{"candidate_id":"58ec529e5a134817bf179b06922ddcf7","manifest_sha256":"ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6","member_count":1567,"payload_bytes":13227642,"state":"protected_opaque_candidate_verified","protected_semantics_used":false}' python3 tools/brain_migration_v1_d43.py verify --target-root "$KINGSTON" --candidate-id "$D43" --manifest-sha256 "$D43_SHA"

mkdir -m 700 -p "$Q"
test ! -e "$Q/ordinary"
test ! -e "$Q/d43"
expect_json '{"candidate_id":"d2fb11d73cd84c14a24ba3ed870b2ccb","manifest_sha256":"0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865","member_count":784023,"state":"inert_quarantine_verified","start_or_activation":false,"protected_custody_complete":false}' python3 tools/brain_migration_v1.py restore-inert --target-root "$KINGSTON" --candidate-id "$ORDINARY" --manifest-sha256 "$ORDINARY_SHA" --quarantine-root "$Q/ordinary"
expect_json '{"candidate_id":"58ec529e5a134817bf179b06922ddcf7","manifest_sha256":"ff565b9e24a8ef3b65cd3b9f8e7e3db3670003ed007e38a3fbadc2083632bfe6","member_count":1567,"state":"protected_opaque_inert_quarantine_verified","protected_semantics_used":false,"start_or_activation":false}' python3 tools/brain_migration_v1_d43.py restore-inert --target-root "$KINGSTON" --candidate-id "$D43" --manifest-sha256 "$D43_SHA" --quarantine-root "$Q/d43"
expect_json '{"candidate_id":"d2fb11d73cd84c14a24ba3ed870b2ccb","manifest_sha256":"0fc78fcdf528d4cea338730a987260f6434d474766bf61c0def85ea71aced865","ordinary_database_count":93,"protected_database_semantics_checked":false,"restore_payload_mutated":false,"sqlite_family_hashes_unchanged":true,"state":"ordinary_sqlite_integrity_verified"}' python3 tools/brain_migration_v1_verify_sqlite.py --payload-root "$Q/ordinary/payload" --manifest "$KINGSTON/BrainMigrationV1/candidates/$ORDINARY/MANIFEST.jsonl" --manifest-sha256 "$ORDINARY_SHA"
brain_runtime_check
git -C "$REPO" rev-parse HEAD
git -C "$REPO" status --short
test -z "$(git -C "$REPO" status --porcelain)"
BRAIN_MIGRATION_V1
```

Stop immediately on any failed assertion/command; UUID, APFS/case-sensitivity, capacity, commit,
manifest digest/count/byte mismatch; nonzero runtime check; restore or SQLite error; or any output
claiming activation. Keep Mac1 and all Kingston candidates intact. Do not inspect private
configuration or protected payload semantics. `restore-inert` must report
`inert_quarantine_verified` for ordinary and `protected_opaque_inert_quarantine_verified` for D43,
with `start_or_activation: false`; SQLite must report 93 databases, unchanged hashes, and no
restored-payload mutation. Both runtime checks must report empty `brain_launchd_jobs`,
`brain_process_candidates`, and `other_python_pids`. Any Python process is a stop condition until
it is identified and stopped or the handoff is reviewed again; the checker deliberately does not
print process arguments, which may contain private values.

This completes the Migration v1 real acceptance gate only after the entire transcript is reviewed
by Sol against the two pins and the outputs above. It is not authorized by this capture receipt
alone. Keep Brain inert and do not erase Mac1 until that final review explicitly returns GO. Once
accepted, later activation on Mac2 remains a separate operator decision.

This document owns Brain host portability, recovery inventory, and erase readiness. The project
checkout, owner runtime, large research-data root, and protected evaluations have separate authority
and recovery boundaries. A clean code clone is not a recovery of Brain state.

## Parallel Linux portability qualification

The separate topic branch `codex/linux-portability-20261018` advances portable build and Linux
operator-host support from `research/active` commit `c157f79471c1cb330bdab01cc4b4cdd7b9a1a186`.
It does not change the active Migration v1 candidate, its handoff, the owner Mac runtime, Kingston,
`research/active`, or `main`. The branch pins source archive metadata to the source commit, provides
a read-only Linux systemd host adapter, and stages content-addressed Linux release candidates.
Release identity binds the wheel SHA-256 to the resolved Python path, version, SOABI, and installer
schema. Each candidate has its own version-specific disabled unit; staging never switches a pointer,
starts, or enables it. Wheel installation and import probing run as `nobody`; promoted files are
root-owned, readable by the service group, and not writable by the `brain` runtime account. A
separate verify action checks the source/wheel identity recorded in the manifest, installed-file
inventory, interpreter identity, unit bytes/path, ownership, permissions, and disabled/inactive
systemd state before explicit activation.

On 2026-10-18, clean Ubuntu 24.04 x86_64 VPS clones at topic HEAD
`d12d8eb9d424e50f8b631627f9e2340ebfee5d31` completed unattended `make setup`. Repeated builds in
separate output directories and a build from a separate clean clone produced identical wheel SHA-256
`8da9d0568c427d364c9ac07519221713c8493f33c00243c4f4dbf9f7f9a82191` and source-distribution SHA-256
`57f35dcc0dde0f3a73c1ebe3fa4c1ef8d2fb633745f6cfb8829b38b943bc43e7`, with `SOURCE_DATE_EPOCH`
bound to the source commit. The current wheel was installed into a root-owned content-addressed
release; verification checked its manifest, installed inventory, interpreter, unit, ownership, and
disabled/inactive state.

A fresh synthetic fixture was restored inert to ext4 quarantine and attached to that exact release.
Independent verification passed before explicit activation. The service ran as `brain` with
`PrivateNetwork=yes` and `Restart=no`; its loopback probe returned `operator-status-v1`, idle mode,
idle phase, and zero jobs. During a 24-second soak, the only SQLite change was the periodic
`last_maintain_ns` checkpoint; revision stayed idle, there were zero jobs or incident events, and
`NRestarts=0`. Stop and finalization passed, and after a real VPS reboot the release and attachment
verifiers passed again. The attachment unit remained disabled/inactive with `MainPID=0`; create-only
root-owned start-intent, idle-probe, pin, and activation-result records survived reboot. A Linux
config-drift exercise on the preceding exact source revision returned `linux_unsupported_mode`,
kept the database idle, and did not dispatch the generic adapter. New non-stop Linux commands are
blocked before cached command replay; a focused regression test covers a reused request ID.

At exact HEAD, 75 focused tests passed and one optional Playwright browser acceptance was skipped
because Node/Chromium is unavailable. Changed-file Ruff and format checks passed; `make smoke` passed
with `edge_established: false` and `synthetic_engineering_check_only`. Full `make check` stops at 107
tree-wide Ruff findings outside the changed Linux files, so typecheck and full pytest were not run.
No provider traffic, credentials, protected data, restored Brain root, CUDA, or NVIDIA capability
was used or qualified.

This proves clean Linux setup, repeated package-build identity, inert staging, synthetic idle restore
and explicit activation, bounded maintenance, stop/finalize, and verifier recovery after reboot. It
does not qualify production or arbitrary restored-state modes, source authenticity beyond the
trusted Git checkout and builder, Linux research-data writer semantics, boot-time activation,
ARM64, Windows/WSL, macOS-to-Linux payload portability, CUDA, or NVIDIA. The immutable
[`Linux portability receipt`](evidence/brain-linux-portability-2026-10-18.json) records exact
artifacts, lifecycle hashes, host facts, failures, and limitations. The next architecture horizon is
the Linux storage writer contract: mount identity, descriptor-bound writes, crash consistency, and
recovery must be reviewed and qualified before Linux can host real research data.

## Current readiness

**NO-GO for erasing Mac1 as of 2026-10-17.** The full seven-root ordinary capture and
opaque D43 overlay now exist as a separately pinned, independently verified Kingston pair, and all
93 ordinary SQLite families pass read-only integrity checks. The next action is the single real
Mac2 restore/package above, followed by Sol review of the full transcript. Until that succeeds,
factory reset remains prohibited. The accepted recovery scope is loss or erasure of the owner Mac
while Kingston remains healthy. The user explicitly accepts Kingston as
the only reset-surviving physical copy and accepts unencrypted local custody for this iteration. Kingston
failure, theft, or corruption remains an accepted uncovered risk, not a blocker for the Mac-loss
claim. A future 1-TB+ SSD, NAS, or other independent destination must add a separately verified
replica and a Kingston-loss rehearsal. The conditional Recovery/capture plan and nine-role policy
remain a Sol-reviewed, non-executable design; they do not authorize a Recovery boot. The user has
changed the PersonalBackup operating plan: it is temporary Kingston occupancy, later moved to a
second MacBook, verified there, and deleted from Kingston separately by the user. Do not inspect,
move, or delete PersonalBackup. The newly authorized Migration v1 candidate may be written while it
remains there, provided fresh free space covers the measured candidate and policy reserves. This v1
exception does not change the post-transition Kingston assumptions for the deferred hardened-v2
worksheet.

On 2026-10-16, the read-only `HeldDataT` identity/locator library and operator CLI passed a real
Darwin/APFS probe against a disposable read-only synthetic APFS image using the pinned Kingston
execution kit under Homebrew-read denial. The fixed roster held eight roots while the absent
verifier role remained unresolved. This validates only the mounted-view identity and locator
mechanism: it does not qualify owner Data T, source cutoff, capture, Kingston K, restore, root
closure, or erase readiness. Sol accepted this bounded scope. See the
[`HeldDataT APFS CLI receipt`](evidence/portability-dr-held-data-t-apfs-cli-2026-10-16T0004Z.json).
The exact published commit was then fetched into a new clean checkout; isolated Python environment,
dependency check, project smoke, and CLI help passed. This was still the current Mac and authentication
context, with no Brain state restored. See the
[`clean-clone bootstrap receipt`](evidence/portability-dr-clean-clone-held-data-cli-bootstrap-2026-10-16T0007Z.json).
The subsequent restricted byte-custody scaffold passed a nonempty synthetic Darwin/APFS-to-Kingston
capture and fresh verification using external plan and manifest pins. Sol accepted this narrow
internal integrity claim only; the strict walker cannot traverse the real owner-root roster, so this
does not qualify owner capture, T/K, Recovery-kit use, restore, or erase readiness. See the
[`synthetic byte-capture E2E receipt`](evidence/portability-dr-held-data-byte-capture-synthetic-e2e-2026-10-16T0204Z.json).
A mixed v2 root-set coordinator now binds directory candidates and exact private-file quarantine
candidates to the same held source view. A fresh-process read-only synthetic APFS-to-Kingston
rehearsal verified two directory units and three opaque file units; Sol accepted this as a
synthetic candidate-index milestone only. The operator `capture` CLI wrote the same five-unit
candidate set to Kingston. Its initial `verify` implementation incorrectly used `writer_guard`, so
it could create the coordination lock and required writer headroom; that run is candidate integrity
evidence only, not read-only verifier qualification. A separate read-only storage binding and
reader-capability now verify the pinned set without taking that lock or imposing a free-space floor.
A fresh CLI process verified the five-unit set on Kingston; the set index and existing lock file
metadata were unchanged. Sol accepted this bounded verification interface and its descriptor,
marker, UUID, and mutator-rejection checks. Verification is still lockless point-in-time artifact
integrity, not concurrent-writer exclusion, source cutoff, T, Kingston K, or restore. Four declared
roles remain unresolved, file metadata is omitted, and every T/K/closure/restore/activation/erase
authority flag remains false. Focused negative tests cover preflight failure, partial capture,
payload drift, empty-set rejection, readonly/low-space storage binding, mount/root replacement, and
mutator denial. A pre-index capture failure leaves no set index; a failure after index creation or
readback may leave an unaccepted candidate index. Receipt:
[`mixed held-Data root-set rehearsal`](evidence/portability-dr-mixed-held-data-root-set-apfs-kingston-2026-10-16T0300Z.json).
The read-only verifier's separate CLI receipt records the fresh run and unchanged storage metadata:
[`read-only root-set verification`](evidence/portability-dr-root-set-readonly-verification-2026-10-16T0313Z.json).
The exact pushed commit `6db6a5a` was then bootstrapped into a new scratch clone using this Mac's
existing Git authentication, the locked Python environment, dependency checks, and smoke. Its own
focused storage/external-writer/root-set CLI suite passed (75 tests), and its verify-only CLI
reverified the same five-unit synthetic Kingston candidate with four roles unresolved and the
inspected lock/index metadata unchanged. A separate real macOS read-only case-sensitive APFS image
with owners enabled passed `read_only_guard` and the reader factory; a pinned fixture file was read,
`create()` was rejected, and the inspected lock metadata and root membership stayed unchanged. Sol
accepted these bounded same-host claims. Neither rehearsal proves whole-volume immutability, clean-host
credentials, owner Data T, Kingston K, owner closure, Brain restore, inert service reconstruction, or
erase readiness. See the
[`published-bootstrap and read-only APFS fixture receipt`](evidence/portability-dr-published-bootstrap-readonly-apfs-2026-10-16T0318Z.json).
A fresh clone of the exact pushed `ce210910` installed the locked development environment and passed
`make smoke`, the 38 focused DR tests, and byte-capture CLI help. Repository-wide `make check` stops
at its first Ruff stage with 107 diagnostics in untouched files, so its later formatting/type/test
stages did not run. This remains same-Mac code-bootstrap evidence, not Brain restore. See the
[`current-code clean-clone receipt`](evidence/portability-dr-current-code-clean-clone-2026-10-16T0205Z.json).
The post-bootstrap restore flow then passed a same-host blocked-path rehearsal: the exact commit,
entry card and Kingston identity matched, and the operation stopped at the absent accepted set before
quarantine or witness creation. This still is not a clean-host restore or erase qualification.
Published v1, v2, and v3 bootstrap tags are preserved as immutable history but retired for operator use.
The v3 annotated locator was Sol-qualified for bootstrap-only use: tag object
`aaafd81a4c396cec9d5ffdb50066911cf84e35e0`, peeled H
`27bca2509af2f5eb3756c99803e7c12acfd713c6`, handoff JSON SHA
`f373bff90ac73f4759a83837bfb4124a7f673512a6f851b0158687f1189b08c3`. The exact remote zsh
sequence fetched and validated in an empty repo with this Mac's authentication; positive and negative
object/schema checks passed under `PYTHONOPTIMIZE=1`. A clean replacement host still must reauthenticate.
The locator remains bootstrap-only: no accepted set, Data T, Kingston K, Brain restore, activation,
final readiness, or erase authority is established. Sol noted that the displayed handoff digest uses
a `shasum | cut` pipeline without `pipefail`; this is informational rather than an acceptance gate.
If it is empty, rerun `shasum` directly before a later restore-flow check. See the v3 qualification
receipt in the evidence index. The v4 handoff below supersedes it for operator entry.

After publishing `c2b42cc51be1ff4306140ede5e3b512d370dca04`, the exact commit was cloned twice into
new scratch checkouts. One fresh environment installed `requirements-dev.lock` plus the editable
project, ran `make smoke`, launched the metadata CLI from a neutral cwd, and passed 136 focused
custody/recovery tests. The published `brain_host_recovery.py bootstrap` helper separately cloned
the exact commit, installed the lock, and returned `state=complete`, matching `head`, and
`smoke=passed`; it changed no storage or services. This used the current Mac, public GitHub access,
and scratch targets. It proves exact-code bootstrap/rebuild only, not recovery data, a new host, or
inert Brain restore. See the [`published bootstrap rehearsal`](evidence/portability-dr-published-bootstrap-c2b42cc-2026-10-16T0356Z.json).

The first metadata-class census traversed eight declared-present roots and recorded stable per-object
descriptor metadata, but its `acl_absent` label did not distinguish a no-ACL result from ambiguous
ACL API errors. Sol review required a narrower statement. The final scan reopens each declared root
endpoint at completion, including the parent directory and leaf of file roots. Its Darwin symlink
xattr fixture resolves to a distinct external target with a different xattr, and the scan observes
only the link's xattr name. The scan fails closed on ACL results outside the API's accepted no-result
errors (`ENOENT` or `ENOATTR`). Across 819,547 entries it records only the aggregate no-result class,
not each entry's errno; disposable file, directory, and symlink fixtures observed `ENOENT`. This is
probe evidence, not a universal claim about ACL semantics. The path-free aggregate includes
`com.apple.provenance` (273,672), `com.apple.macl` (995),
`com.apple.FinderInfo` (67), and one backup exclusion marker; `UF_HIDDEN` was present on 67 entries.
The research checkout has 6,053 regular-file hardlink groups whose aliases were not all observed
within that root. The live Data volume remained read/write and changed by one observed entry between
scans; neither scan establishes a common source point or T. See the initial
[`metadata-class census`](evidence/portability-dr-root-metadata-classes-2026-10-16T0340Z.json),
the superseded intermediate [`metadata-probe correction`](evidence/portability-dr-root-metadata-classes-2026-10-16T0350Z.json)
(its file-root parent check and link-target fixture were incomplete),
and final [`root-endpoint and metadata-probe census`](evidence/portability-dr-root-metadata-classes-2026-10-16T0352Z.json).
Sol independently accepted the final receipt as a live read/write metadata-class interval inventory
only; endpoint observations do not establish continuous custody, ownership authorization, T, K, or
erase readiness. The exact scanner and test hashes must be published with this receipt.

The Darwin symlink xattr-name probe was corrected from an unavailable `llistxattr` symbol to
`listxattr` with `XATTR_NOFOLLOW`, and a real macOS symlink probe now passes. Published
`brain_host_restore_flow_v1` remains unchanged: its eight directory roles are not the current
nine-role HeldDataT roster, which includes three file roles. Sol requires a versioned v2 handoff and
negative-only assessor with an explicit type-aware crosswalk; `owner_work` and `research_work` must
not be mapped automatically, and the MCP files stay quarantine-only/reissue. Current candidate sets
cannot authorize restore. The next implementation focus is a real descriptor-bound capture and inert
extract path that handles unique ignored state, symlink and hardlink disposition, xattr/ACL/flag
policy, and database consistency. Restore readiness and SAFE TO ERASE remain NO-GO.

Sol's review narrows the hardlink question without closing it: all 6,053 unobserved research
hardlink groups fall under `.git` in the pinned metadata census, while the exact research checkout
HEAD is reachable from a live published branch. That proves the checked-out commit can be fetched again
after Git reauthentication; it does not prove local refs, reflogs/stashes, unreachable objects,
config/hooks, alternates, index state, or linked-worktree administration are disposable. Keep the
research `.git` directory opaque, separate, and backup-required until its required local state is
inventory-pinned or explicitly declared rebuildable. A future held-view capture may copy observed
`.git` files and preserve aliases wholly within the captured unit while recording unobserved aliases;
it must not claim topology or root closure. Xattr values must be captured privately and bound to
the byte manifest, but remain unapplied during inert extraction until each metadata class has a
qualified policy.

A 03:57Z path-free Git metadata check adds two concrete preservation items. The research checkout is
at the published `research/active` head, has no stash and no dirty/untracked worktree paths, but its
reflog reaches four commits outside current live branch/tag histories; those commits remain
unidentified and uncaptured. The owner Codex linked worktree's HEAD is not reachable from current
branch heads (correcting the earlier branch-only claim), though it is an ancestor of the three live
`brain-dr-handoff-v1/v2/v3` tag targets. Its worktree also contains one untracked, unignored 249,641
byte regular file. The 03:57Z census inspected only Git/ref metadata and file lstat fields; it read no
Git blobs or untracked bytes. The user has since identified this file as the Recovery Terminal
transcript, SHA-256
`37ea3c44e4ca3483cdfc16c7551a8e8695d80283f5f972120346a4c3e091a0ab`. It is now classified as
private recovery evidence, but remains untracked/local and backup-required until a verified source
capture preserves it. Keep the four reflog-only commits and linked-worktree admin state
backup-required as well. Receipt:
[`Git local-state census`](evidence/portability-dr-git-local-state-census-2026-10-16T0357Z.json).

Live checks on the current Mac:

- On 2026-10-14 at 14:51Z, a descriptor-bound, metadata-only census published and reread-verified
  its private JSONL manifest and completion marker on the pinned Kingston root (299,700,736 bytes).
  It covered nine declared roots and four Git bindings; all roots remained on the pinned Data UUID
  and no separate Git metadata roots were found. The census is **incomplete**: one directory in
  `owner_checkout` changed metadata during traversal, so `scan_complete=false`. The exact locator
  is retained only in the private Kingston manifest. The other eight roots passed their included
  traversal checks; three research `wallets` roots and owner Codex `.local` remain explicitly
  opaque, backup-required exclusions. Two `.env.local` files were excluded for credential reissue.
  No application, credential, symlink-target, xattr, or protected payload bytes were read; no
  provider request or service change occurred. This sequential census is neither a common source
  point nor root-closure, custody, or erase evidence. Receipt:
  [`declared-root boundary census`](evidence/portability-dr-root-boundary-census-2026-10-14T1451Z.json).
- Read-only owner runtime refresh (2026-10-15 21:32:34 UTC) found 35 Brain/D43 labels loaded, 12
  with PIDs, and 12 cwd-matched workspace processes (nine owner, three research, including the
  status probe). Both Gecko canary watchdogs had PIDs; storage was not inspected. No arguments or
  environments were read and no services changed. A preceding same-session point check returned 15
  labels with PIDs and showed `gecko-capture-only-v6` running; this label was absent from the saved
  21:32Z check. These short-window changes do not establish a write frontier or source quiescence,
  so the owner census/source cutoff remains blocked. Receipt:
  [`live runtime refresh`](evidence/portability-dr-live-runtime-refresh-2026-10-15T2132Z.json).
- A bounded allowlisted projection at 22:27Z revalidated Kingston UUID/root and pinned the v6
  Gecko capture activation, its exact repository plist, source manifest, epoch, and opaque journal
  counters. The existing activation still says `capture_authorized=true`; its 60-second scheduled
  label was loaded. The journal had 21 events: five intents, four dispatches, five outcomes, five
  confirmations, one missed slot, and zero unresolved intents. Both 15-second canary watchdogs had
  terminal receipts (`passed` and `failed_closed`). A pinned source-manifest self-hash was valid,
  but one of its 15 source files no longer matched the activation source commit:
  `src/memecoin_research_storage/__init__.py`. The current tick implementation verifies this set
  before work and therefore fails closed; no tick or request was invoked by the DR probe. The
  projection is recorded in the
  [`Senses pre-quiescence receipt`](evidence/portability-dr-senses-prequiescence-2026-10-15T2227Z.json).
- After confirming terminal journal/watchdog state and no active callback PIDs, the exact v6 label
  and its two terminal canary watchdog labels were disabled and unloaded. No callback was killed;
  the Kingston journal hash stayed unchanged. All three remained absent and disabled across 25
  checks over 75 seconds, longer than the v6 60-second schedule; legacy v3/v4 capture labels
  remained unloaded. Their prior enable/load state and exact plist hashes are in the pre-quiescence
  receipt; re-enabling is a separate owner-authorized action. No other label was changed. This
  closes only this Senses timer/write loop, not the owner-wide source frontier: paper/UI processes
  and the loaded D43 scheduler remain outside the quiescence claim. See the
  [`Senses quiescence receipt`](evidence/portability-dr-senses-quiescence-2026-10-15T2230Z.json).
- At 22:35Z, the legacy `ai.memecoin.brain.b09r2.source` launcher was bound to its exact deployed
  runtime: the 154-file deployment bundle and manifest verified, and the imported runtime tree
  matched that bundle. The deployed checkout was `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31`, an
  ancestor of current `research/active`. The source activation was expired and its STOP marker
  recorded `source_worker_failure`; no callback PID was present. Its hash-valid journal had 3,605
  events, including ten reservations without outcomes. Following Sol review, only this exact
  five-second launchd label was disabled and unloaded; no process was killed, no reservation was
  replayed, and the source root was retained. It remained unloaded for 15 seconds with no journal
  change. This prevents automatic re-entry into that fail-closed source worker while preserving
  unresolved attempts as possibly spent history. The root remains host-local and its complete
  Kingston custody is not established. This closes one launch surface only; operator/UI and D43
  remain untouched, and there is no owner-wide cutoff or erase qualification. See the
  [`B09 source quiescence receipt`](evidence/portability-dr-b09-source-quiescence-2026-10-15T2235Z.json).
- A subsequent real v2 root-boundary census completed endpoint-stable metadata traversal for all
  eight declared-present roots (819,063 entries) and published a 317,360,545-byte private manifest
  plus completion marker to the pinned Kingston root; descriptor-bound readback matched both
  receipt hashes. The ninth required role, the previously declared temporary verifier worktree,
  was absent and explicitly retained as unresolved; it was not recreated. Three research `wallets`
  subtrees and owner Codex `.local` remain opaque and backup-required, and two credential-named
  entries remain excluded for reissue. This v2 run supersedes the prior incomplete v1 only as the
  latest traversal evidence for its corresponding present-root scope. It is sequential live
  metadata, not an atomic source point, root closure, complete custody, or erase evidence. Receipt:
  [`v2 root-boundary census`](evidence/portability-dr-root-boundary-census-v2-2026-10-15T2239Z.json).
- The absent verifier checkout's recorded detached commit and tree remain present in Git; the commit
  is reachable from the live published `origin/research/active`, so tracked source can be recreated
  from Git. Its one historical ignored path-class record is unavailable; the later correction says
  the `__pycache__` name alone does not prove harmlessness or reproducibility. Keep the ninth census
  role absent and unresolved; no directory recreation, worktree pruning, or accepted-loss scope
  decision was made. See the
  [`verifier worktree classification receipt`](evidence/portability-dr-verifier-worktree-classification-2026-10-15T2305Z.json).
- The B09 source root was then rechecked against its pinned device/inode and the v2 census. Its
  metadata-only subtree scan found 20,177 entries with no protected path component, symlink,
  multiply linked regular file, or v3 automatic/policy exclusion. After confirming the exact B09
  writer remained disabled/unloaded with no open descriptors, v3 captured all 751,986,935
  regular-file bytes to the pinned Kingston root. Fresh descriptor-bound archive verification
  matched the manifest and completion marker; all 20,177 entries were included with no unresolved
  exclusions or external custody requirements. This is complete opaque file-content custody only
  for the declared B09 source root, not a Data snapshot or application-consistent SQLite backup.
  See the [`B09 root custody and inert restore receipt`](evidence/portability-dr-b09-root-custody-inert-restore-2026-10-15T2251Z.json).
- The v3 manifest records source flag `UF_HIDDEN` and omits values for macOS `FinderInfo`, `macl`,
  and `provenance` attributes (1, 125, and 138 entries respectively). Sol accepted a bounded
  Darwin-only v3 restore gate for the already validated supported flags; file and directory flags
  are applied through the existing descriptor-bound path and checked during restore readback.
  Synthetic hidden-file/directory round-trip, non-Darwin refusal, and injected flag-application
  failure checks pass. The pinned capture was extracted to a fresh owner-private 0700 quarantine on
  the current Data volume; its restore witness matches the capture and policy, represented bytes,
  modes, mtimes, flags, and topology passed readback, and the B09 label remained unloaded and
  disabled. This is a same-host inert extract of one root. It does not prove clean-host recovery,
  exact filesystem metadata fidelity, SQLite application consistency, owner-wide cutoff, Kingston
  frontier, or erase readiness. Raw STOP and ten unresolved reservations were preserved without
  replay or interpretation.
- The exact published DR commit was then fetched into a fresh clean checkout. Exact-commit bootstrap
  completed and smoke passed. From that clone, the pinned Kingston B09 capture and policy were
  verified and extracted into a separate owner-private 0700 quarantine on Data. The restore witness
  matched its capture and policy; the B09 label remained disabled/unloaded, and no service,
  provider, runtime, or SQLite activation occurred. Sol accepted this as a **same-host inert
  restore rehearsal** for code bootstrap plus one declared Kingston-backed data root. The host,
  user, Python toolchain, and attached volumes are still those of the owner Mac; this does not
  exercise replacement-host authentication/dependencies, owner-runtime reconstruction, SQLite
  application consistency, Data T/K frontiers, owner/protected closure, or erase readiness. See the
  [`clean-clone B09 restore rehearsal receipt`](evidence/portability-dr-clean-clone-b09-restore-e2e-2026-10-15T2255Z.json).
- A distinct `ai.memecoin.brain.b09r1.source` LaunchAgent was found loaded but idle. Its durable
  STOP marker is checked by the bound runtime before child/tick execution, the journal chain
  verifies through 6,065 terminal events (3,032 reservations and 3,032 outcomes, zero unresolved),
  and no latest-two recovery candidate exists. Following Sol review, only this exact label was
  disabled and unloaded; it remained absent with no PID for 11 seconds before and after bootout,
  with STOP and the journal tail unchanged. The original plist remains on the host. Separate
  descriptor-bound v3 captures preserve the complete-under-policy state root (31,797 entries,
  629,332,887 bytes) and deployed runtime bundle (162 entries, 1,476,176 bytes), with zero
  exclusions or unresolved custody. Each was extracted into a separate mode-0700 quarantine on the
  same Data volume; restore witnesses matched capture manifests and represented bytes/metadata. No
  SQLite API was opened, runtime code executed, or service installed. This proves only two
  interval-stable root captures and same-host inert extracts, not an atomic pair, T/K frontier, or
  application consistency. The deployed bundle pin and manifest are internally consistent, but
  its 140 Python files do not match any of 933 reachable Git commits; nine deployed blob IDs are
  unreachable from Git refs. Preserve the bundle as opaque unique local executable state without
  trusted-source or restart claims. V3 omits xattr values (`FinderInfo` 1, `macl` 412,
  `provenance` 143 on state; `provenance` 162 on bundle). Exact paths and source identities are in a
  private Kingston relation record. This does not qualify clean-host recovery or erase readiness.
  See the [`B09r1 state and bundle inert restore receipt`](evidence/portability-dr-b09r1-state-bundle-inert-restore-2026-10-15T2329Z.json).
- At 23:10:13Z, the private-config prior archive and manifest pins were reverified on Kingston. A
  live metadata pass found the same 75-file roster, with 74 files matching prior size/mode/mtime and
  one unique metadata-delta candidate; only that candidate was opened no-follow and hashed. Its
  stable 0600, single-link file differed from the prior manifest and was copied as one 304-byte
  create-only private delta with a separate manifest and COMPLETE marker. Fresh descriptor-bound
  readback verified both marker bindings on the pinned Kingston root. An earlier live content-hash
  comparison had found 74 same-name/same-byte files and one difference, but its candidate identity
  was not linked to this capture by persisted per-file signatures. Treat this as provisional opaque
  custody for one observed file; the possible private value remains unclassified and quarantined.
  No config-generation or restore overlay was formed, and SAFE TO ERASE remains **NO-GO**. Receipt:
  [`private config single-file delta`](evidence/portability-dr-private-config-single-file-delta-2026-10-15T2310Z.json).
- The census CLI supports relocated hosts through an explicit mode-0600
  `brain-host-root-roster-v2` JSON input. Required logical roles must be declared as present or
  absent with an explicit disposition and reason; present roots carry absolute local paths, kinds,
  optional metadata-only exclusions, and optional checkout-role keys for bounded Git admin/common
  path derivation. It does not discover Desktop/tmp paths or Codex worktrees. Source volume UUID
  and mount point are roster pins; Kingston UUID and root ID are independently roster-pinned and
  checked against storage configuration. The v2 input uses `schema`, `version`,
  `source_volume_uuid`, `source_mount_point`, `storage_volume_uuid`, `storage_root_id`,
  `required_roles`, and `roles`; each role has `key` and `state`. Present roles use `path`,
  `expected_kind`, optional `metadata_only_exclusions`, `opaque_expectations`, and
  `git_checkout_key`, while absent roles require `disposition` and `reason`. The historical v1
  profile remains available only through
  `--legacy-profile`. The exact mode-0600 roster bytes and SHA-256 are held in the private manifest
  and the completion marker binds the digest. Required-role validation checks only the roster's
  internal consistency; it does not prove that an independently approved owner-host inventory is
  complete. A complete scan requires at least one present root and means traversal completed for
  declared present roots on the pinned source volume; it never proves root closure, a source cutoff,
  restore readiness, or erase safety. Git metadata locators inside an explicit exclusion fail
  closed instead of scanning around it. This interface change has only synthetic focused coverage
  so far; no owner census was rerun and no live recovery qualification changed.
- The exact-commit bootstrap helper was hardened and rehearsed at
  `8544c4d82ebc7dbf277cab5e68715e279f2b267b` from a disposable local bare Git fixture. It created a
  clean final checkout, verified matching HEAD and an unchanged dependency-lock SHA, ran `pip
  check` and smoke, and isolated Python venv/install/check/smoke stages under a temporary HOME with
  pip config/cache and Python user-site disabled plus explicit `https://pypi.org/simple`.
  Deliberately invalid host pip index/config/cache/Python-path values did not contaminate those
  stages; unknown commit and preexisting destination cases failed closed. The helper deliberately
  leaves Git clone/fetch and the initial Python-version probe in the caller environment so normal
  user-controlled Git authentication can work. This was a same-Mac rehearsal with Python 3.13.6,
  not a replacement-host test; private GitHub auth, artifact-hash reproducibility, and offline
  dependency availability remain unproven. Package downloads from PyPI occurred; no Brain provider
  requests, storage changes, or service changes occurred. This verifies code bootstrap only, not
  Brain state recovery or erase readiness. Receipt: [`isolated bootstrap rehearsal`](evidence/portability-dr-isolated-bootstrap-rehearsal-2026-10-14T1836Z.json).
- The earlier exact-code clean-clone/bootstrap rehearsal used
  `7d8dc82f94e5c9e2631bc38ae79f1229cccdeea1`, an ancestor of the published DR changes on
  `research/active`. The later Kingston operator-flow rehearsal used current commit
  `89ff00f1f32d5aa2043f6729d78190a03ff4c5b6` with only synthetic artifacts; its scope and limits
  are recorded below. The immutable inventory receipt
  records the prior source cutoff at `eaedeae61cd101882835095c2f54158ca07a2945`. `main` and
  `origin/main` remain at `770699d3ef8558518ae750b6d5d6cab41c2e82c8`; do not advance `main` until
  the capability is qualified.
- A clean-`HOME`, empty-environment bootstrap probe at the current published DR commit stopped at
  `git clone`: the private remote requires user-controlled GitHub authentication. The isolated
  attempt created no checkout or environment and changed no persistent Kingston or production state;
  its temporary home was removed. Replacement-host login is therefore an explicit restore prerequisite; no auth database or
  credential was copied. A full authenticated isolated bootstrap remains unproven. See the
  [`clean-home Git authentication receipt`](evidence/portability-dr-clean-home-git-auth-boundary-2026-10-14T0208Z.json).
- The owner checkout at `/Users/root1/Desktop/memecoin-ai` was clean at
  `a5944742b6b8ed0466c50bdeb72370ed3e3f2a31` on branch `main`. Its cached `origin/main` is
  `6feba83525c0d21392432c53becd79e8901650ea`, 15 commits ahead; a read-only live query now returns
  `770699d3ef8558518ae750b6d5d6cab41c2e82c8`, matching research `origin/main`. That object is not
  present in the owner clone. Existing objects in the research checkout establish owner `HEAD` as
  507 commits behind live `main`, and cached `origin/main` as 492 commits behind it. This resolves
  stale tracking-ref ancestry but not which bundle, activation pointer, or state owner services use.
  See the [`owner Git remote check`](evidence/portability-dr-owner-git-remote-check-2026-10-14T0122Z.json)
  and [`ancestry receipt`](evidence/portability-dr-owner-git-ancestry-2026-10-14T0123Z.json).
  Read-only B01–B04 status reports all four scopes STOPped and expired. They retain 48, 48, 19, and
  28 pending predictions respectively; these
  remain pending and are not to be resumed. This status does not audit owner operator/paper state.
  The owner operator and several owner paper/UI processes remain running.
  A 02:43Z read-only probe found 18 combined Brain/D43 labels loaded, nine with PIDs, and 72
  cwd-matched owner/research processes, including three owner Python paper/operator processes. It
  read no arguments or environments and made no changes; this point-in-time observation does not
  establish a write frontier or consistent source view.
- An earlier metadata-only census counted 330,273 regular files and 108 symlinks in research `work/`, and
  285,577 regular files and 18 symlinks in owner `work/` (about 28.6 GB combined). It read no file
  bytes and found no unreadable entries. These are large mixed state trees, not classified recovery
  sets. Across consecutive full scans, owner `work/` kept the same entry counts but its apparent
  regular-file bytes fell by 594,048 and its membership/metadata digest changed. A short repeat
  observed metadata changes to three owner entries, including one growing log (+56,423 bytes) and
  one SQLite file; no entries were added or removed. Research `work/` metadata remained stable over
  that short repeat. This confirms active owner-state churn and does not bound byte writes or qualify
  a snapshot. A point-in-time `lsof` metadata pass over 12 owner-cwd processes found 26 open-file
  records within owner `work/`; it found no named SQLite-family descriptors within either `work/`
  root. This does not establish inactivity or a write frontier. Receipt:
  [`host metadata census`](evidence/portability-dr-host-metadata-census-2026-10-14T0153Z.json).
  Both trees still need complete recovery classification.
  `~/.config/brain-research` occupies about 348 KiB and currently has 14 entries. The owner
  `.env.local` is mode 0600 (470 bytes); its contents were not read or hashed. The
  owner and research checkout `.env.local` were excluded from the three-member MCP custody capture;
  every future recovery archive must exclude `.env.local` credential files for user reissue.
  `~/Library/LaunchAgents` has 60 plist files: 55 Brain, one D43, and four host-software definitions
  for Steam cleanup, Zoom, and Homebrew PostgreSQL. The four host-software labels were already
  loaded without PIDs; DR did not load or change them. The PostgreSQL label has no PID and its last
  status is nonzero; a read-only search of the owner checkout found no PostgreSQL references in its
  recorded scope. A negative search does not prove absence of dependency; keep this service outside
  the inert Brain baseline pending explicit dependency proof.
  At 01:12Z, 18 labels were loaded and nine had PIDs, including `ai.memecoin.brain.b09r2.source`; by
  01:17Z, 18 remained
  loaded and eight had PIDs, with that source label loaded but without a PID. At the later probe,
  72 processes had cwd metadata under owner/research roots. No process arguments or environments
  were read. These time-bounded observations do not establish whether the source label performed
  earlier work or whether other services wrote state. DR work did not change or activate services.
  A value-filtered plist projection confirms all 56 Brain/D43 definitions contain `RunAtLoad` (54
  true, two false) and the four host-software definitions contain three true and one false. The plist
  byte streams were opened to project selected fields, but environment values were not inspected or
  interpreted, retained, or emitted. Five environment-key names occur across four Brain/D43 plists;
  one additional environment key occurs in one host-software plist. Keep credentials private and
  reissue them. The existing
  [`LaunchAgent metadata receipt`](evidence/portability-dr-launchagent-metadata-2026-10-14T0120Z.json)
  remains the original metadata-only record; the later
  [`root/dependency map`](evidence/portability-dr-root-dependency-map-2026-10-14T0221Z.json)
  owns this bounded classification.
- Kingston `BrainData` occupies about 19 GiB and remains on case-sensitive APFS with UUID
  `8f8f1081-9be4-4914-9111-75f1b90255bf`, root ID
  `9428695af6d4f46c08e1a444cdc66de8`. The 06:10Z read-only check found about 203 GiB (217 GB)
  free; the
  50-GiB storage floor passes.
  Kingston is the only reset-surviving physical copy.
- A fresh read-only preflight at 04:56Z confirmed the same Kingston UUID/root with
  217,174,470,656 bytes free. Internal Data had 14,453,396 KiB free (13.78 GiB) at 97% used; no
  APFS snapshots or Time Machine destination were present. The session has no noninteractive admin
  path and no safe snapshot retention/churn budget is qualified, so no snapshot was attempted.
- The 01:18Z storage refresh reports 13,234,572 KiB free on internal Data and 212,382,340 KiB on
  Kingston. It found no Data APFS snapshots or configured Time Machine destinations. These are
  point-in-time capacity/facility checks, not a qualified recovery boundary; see the
  [`storage/owner refresh receipt`](evidence/portability-dr-storage-owner-refresh-2026-10-14T0118Z.json).
- The read-only refresh receipt measured 12,960,665,600 bytes free before removal of two agent-created
  rehearsal clones. Removing those exact, verified disposable checkouts reclaimed 596,779,008 bytes;
  current `df` reports 13,239,692 KiB free (about 12.6 GiB). `diskutil apfs listSnapshots` finds no
  Data snapshots and `tmutil` reports no configured destination. A fresh
  noninteractive privilege check confirms the session cannot create or mount a privileged snapshot
  without user authentication. The available unprivileged `iostat` view combines reads and writes,
  so it did not establish a safe write-churn bound. A read-only owner-wide snapshot has not been
  created or qualified.
  Copying live SQLite/WAL files individually is not an acceptable substitute. Any snapshot needs a
  short, monitored retention window and a conservative free-space abort floor.
- Prior exact-current clone and uncached dependency bootstrap took about 82 seconds on this
  macOS/Python 3.13 host. On the current commit, `make smoke` passes; the recovery-set,
  restore-witness, tree-custody-v3, bootstrap, SQLite-backup, and static-custody focused suite passes
  90 tests; scoped Ruff/format and `git diff --check` pass. Repository-wide `make check` stops at
  Ruff with 107 findings outside the DR scope. These are synthetic engineering checks.
- A synthetic frontier scaffold wraps the v1 set/witness with exact storage member pins, a
  caller-supplied post-capture source fingerprint assertion, and a fixture corpus object plus
  STOP/pending marker. Its archive and identity callback use pytest temporary storage. Sol accepts
  this bounded scaffold only: it does not prove an immutable T cutoff, reconcile capture members
  to that cutoff, establish a real K journal prefix, or exercise Kingston. Ten frontier tests and
  46 combined recovery-set/tree-v3/SQLite/frontier tests pass. See the
  [`frontier scaffold receipt`](evidence/portability-dr-frontier-scaffold-2026-10-14T0314Z.json).
  The factory-reset verdict remains NO-GO.
- A K object/STOP fixture of the same format has now been written under the configured Kingston
  root with the held descriptor-bound create-only writer, reread through a fresh storage writer,
  and removed through the held descriptors after an exact-membership check. This tests the storage
  binding for a synthetic 57-byte object only; no recovery set, real journal prefix, source cutoff, or restore
  witness was exercised on Kingston. See the
  [`Kingston synthetic K receipt`](evidence/portability-dr-kingston-synthetic-k-rehearsal-2026-10-14T0317Z.json).
- An opt-in synthetic-only Kingston rehearsal then copied six fixture artifacts, built a v1
  recovery set, restored all six into isolated same-Mac temporary targets, bound and verified the
  restore witness, and published/read back the synthetic object/STOP/end-marker frontier envelope.
  Exact member type/path/size/hash inventories were compared before nonce cleanup; only the normal
  shared zero-byte `.index.lock` and `.publish.lock` coordination files remain (whether they
  pre-existed was not established). Sol independently accepted this narrow pass. It proves neither
  an immutable owner T cutoff, a real K journal prefix, xattr/ACL recovery, nor clean-host restore;
  factory reset remains NO-GO. See the
  [`synthetic Kingston restore receipt`](evidence/portability-dr-kingston-synthetic-restore-2026-10-14T0333Z.json).
- A synthetic held-directory reader now re-verifies all six recovery-set artifact pins and compares
  four v3 fixture roots through open directory/file descriptors against verified pages: exact
  membership, file bytes, topology, and recorded stat/xattr-name fields. It skips excluded D43
  descendants. A separate metadata-only pass compares the synthetic SQLite DB/WAL/SHM/journal
  family through held descriptors to its backup receipt; fixture setup separately runs online backup
  against its disposable source DB. Artifact inventory commitments/counts are derived, and member
  capture intervals must fit the declared capture window; the fixture's unresolved descendant
  remains explicit. The focused suite passes 53 tests, including source-drift and parent-replacement
  rejection plus a Hypothesis event-order property. Synthetic root device/inode bindings are
  checked, while snapshot identity, policy/tool hashes, and event times remain fixture declarations.
  This is not APFS
  snapshot attestation; protected-root binding, full owner closure, and a real snapshot-family
  capture remain open. Sol accepts only this synthetic scope. The v1 recovery-set verifier hash is
  unchanged. See the
  [`held-view synthetic receipt`](evidence/portability-dr-source-view-held-view-2026-10-14T035534Z.json).
- A negative-only restore assessor now combines the existing set/witness integrity checks with
  independent pins, the exact clean research commit, fresh filtered launchd/cwd observations, and
  a held Kingston mount/root binding. Missing evidence is `unknown`; mismatches fail. It always
  returns `blocked`, with complete inertness, owner/protected custody, T, K, and application restore
  still unknown. The read-only storage opener does not take a writer lock; verification may create
  and remove short-lived system temporary SQLite scratch. Synthetic tests cover missing and
  mismatched pins, absent restore roots, active labels, unavailable process observation, witness
  drift, low capacity, and simulated final root-identity drift. Sol accepts this narrow assessor;
  the simulated root drift is not a real rename-race rehearsal. It did not assess live Kingston
  artifacts or restore targets and does not change the NO-GO verdict. See the
  [`restore assessor receipt`](evidence/portability-dr-restore-assessment-v1-2026-10-14T0411Z.json).
- A consume-existing-view adapter contract is now exercised on synthetic fixtures. It opens the
  declared view and roots through held no-follow descriptors, checks same-device topology and
  path/root identity before and after reconciliation, and binds only the four v3 tree roots to
  prior capture pages. Protected-v2 and SQLite locators remain source-unbound; the SQLite family
  has a separate metadata-only check through the owner root. Exact declared credential files must
  be excluded with `reauth_reissue`; this does not establish exhaustive credential closure. The
  existing capture reader reopens paths, so the result says post-capture reconciliation and
  descriptor-sourced capture is false. No APFS snapshot is created or mounted; snapshot identity,
  read-only status, and cutoff remain unqualified. T, host closure, and erase readiness stay false.
  Sol accepts only this synthetic contract. See the
  [`source-view adapter receipt`](evidence/portability-dr-source-view-adapter-v1-2026-10-14T0421Z.json).
- A single inert restore conductor now preflights the six pinned recovery roles, audits every v3
  exclusion page, rejects protected-scope/unresolved custody, caller-forbidden target roots,
  absolute v3 symlinks, and any protected-v2 symlink, then extracts into a fresh 0700 quarantine,
  verifies the restored members, binds a witness, and verifies it again. Failures retain partial
  quarantine without resume semantics. The output claims only byte-verified inert artifacts and
  keeps T/K, source closure, host closure, activation, and erase readiness false. Sol accepted this
  implementation scope after an independent code review. Its exclusions for owner checkout,
  private config, LaunchAgents, and any additional source roots depend on correct caller-supplied
  paths; this does not prove owner-wide closure. The conductor result can now be passed directly
  to the negative-only restore assessor with `--handoff` after the assessor-side integrity and
  field checks.
  Synthetic tests exercise actual captured absolute-v3 and protected-v2 symlinks and reject them
  before quarantine creation. See the
  [`inert restore conductor receipt`](evidence/portability-dr-inert-restore-v1-2026-10-14T0438Z.json).
  This remains a fixture-only implementation result, not a live Kingston or clean-host rehearsal.
- The committed conductor-to-assessor flow was then rehearsed against the configured Kingston
  volume using six synthetic role captures, an isolated temporary `HOME`, and a separate-device
  temporary restore quarantine. The set and witness pins passed, restored bytes were verified, the
  assessor returned `blocked` with T, K, and complete owner/protected custody `unknown`. Set and
  witness pin passes establish internal matching/readback only: this run generated the expected
  hashes itself and had no external trusted recovery receipt. The known run nonce paths were
  removed and no run set/witness ID remained in the filtered 32-hex ID lists;
  shared parent-directory effects were not classified. No provider request or service change
  occurred; no persistent set or witness ID remained. A failed fixture attempt had created six random synthetic artifact
  roots before refusing a stale UUID/root marker; those exact roots were byte-compared with their
  local synthetic source and descriptor-unlinked before the successful attempt. The pre-existing
  shared index lock remains and its ownership/mutation is not classified. This proves the bounded
  operator path on the current host, not a clean compatible-host restore or erase readiness. The
  local pytest-managed HOME and restore target were not checked for post-run removal. See the
  [`Kingston inert restore operator-flow receipt`](evidence/portability-dr-kingston-inert-restore-flow-2026-10-14T0450Z.json).
- A fresh clone of `7d8dc82` bootstrapped Python 3.13 and passed `make smoke`; both new recovery CLIs
  also passed direct `--help` invocation from that clone. The bootstrap reported no storage or
  service changes. This still ran on the existing Mac, did not isolate host caches, and restored no
  owner/private/protected/Kingston state. See the
  [`bootstrap rehearsal receipt`](evidence/portability-dr-bootstrap-rehearsal-2026-10-14T0107Z.json).
  Since that rehearsal, the private config root and all 60 current LaunchAgent definitions were
  captured to Kingston and byte-verified after isolated quarantine restore. Neither group was
  activated. Owner runtime/state, protected custody, and Kingston corpus dependencies still lack a
  qualified complete recovery set and full restore witness. No full clean-room restore has been
  rehearsed.
- `tools/brain_host_recovery.py` provides read-only host status and inert research-checkout bootstrap.
  `tools/brain_host_tree_custody_v3.py` and `tools/brain_host_recovery_set.py` verify declared tree
  members and a restored quarantine witness. Their `application_recovery_qualified` result remains
  false; the current set schema is an integrity index, not owner-root closure or erase approval.
- These live inventory and qualification observations are recorded in the immutable
  [`read-only DR inventory receipt`](evidence/portability-dr-readonly-inventory-2026-10-14T0101Z.json).
- The fresh [`read-only DR refresh receipt`](evidence/portability-dr-readonly-refresh-2026-10-14T0112Z.json)
  records 18 loaded labels / nine running, 12.1 GiB free on Data, and a recursive metadata-only name
  scan across owner/research work, private config, and Kingston. The scan located
  `owner/work/calendar-execution-01`; no exact F01, reserved, or holdout component name matched.
  This name-based scan does not prove those roots are absent or that protected custody is complete.

- A 01:17Z metadata-only follow-up found basename-prefix candidates under owner work (37 D43 and
  30 F01), research work (15 D43, 81 F01, and three reserved), and Kingston (four D43 and eight
  reserved). Private config had no matching prefix. Paths and contents were not recorded or opened;
  names alone do not establish protected role. These candidates expand the unclassified custody
  inventory and must be resolved as opaque custody members or explicitly ruled out by an authorized
  classification before erase. The fresh service/process status and limits are in the
  [`metadata follow-up receipt`](evidence/portability-dr-metadata-followup-2026-10-14T0117Z.json).
  Type-only counts show 59 regular files/eight directories in owner work, 68 regular files/31
  directories in research work, and eight regular files/four directories on Kingston. Their
  suffixes include JSON, logs, Markdown, Python, and Rust source. Nested entries can be related or
  duplicated; extensions do not establish role. See the
  [`candidate entry-type receipt`](evidence/portability-dr-candidate-entry-types-2026-10-14T0119Z.json).
  A follow-up kept a private path inventory of all 178 matching entries in those three roots on
  Kingston at `host-recovery/private-path-inventory-20261014-a1/`. The 62,395-byte manifest records
  relative paths, lstat metadata, and symlink link text; no payload was opened and no symlink was followed.
  The manifest and completion marker were reread through a fresh descriptor-bound verifier and
  hash-checked. All candidate entry metadata was stable at the end of the scan, which does not make
  this a common source point or establish role/byte custody. See the
  [`private candidate-path inventory receipt`](evidence/portability-dr-protected-candidate-path-inventory-2026-10-14T0155Z.json).

- Recursive metadata for `~/.config/brain-research` finds 13 directories and 75 files (about
  184 KiB), all files mode 0600. Its top-level entries include five versioned external configuration
  sets, three timing-rehearsal sets, one retired-LaunchAgent set, one storage-health set, four
  handoff/binding files, and no symlinks. These host-local records may include old activations or
  private configuration; names and modes do not prove contents safe or disposable. Preserve or
  explicitly reissue them after private classification. Metadata-only follow-up counted 15 mode-0600
  plist copies in the retired set; they remain inert custody candidates and have not been compared
  to live definitions. The metadata inventory opened no contents; a later opaque backup copied and
  hashed bytes without parsing them. See the
  [`private-config metadata receipt`](evidence/portability-dr-private-config-metadata-2026-10-14T0127Z.json).
  The focused [`retired plist set receipt`](evidence/portability-dr-retired-launchagent-set-metadata-2026-10-14T0136Z.json)
  records the follow-up count without file names or contents. The entire private root is now in a
  provisional Kingston quarantine; all member hashes were verified after reread. Its metadata was
  stable during this live pass, but no atomic cross-file boundary is proved. The archive is not
  restored or activated automatically; see the
  [`private-config custody receipt`](evidence/portability-dr-private-config-custody-2026-10-14T0140Z.json).
  The opaque restore witness counts 14 archive directory entries because it includes the archived
  `brain-research/` source root plus its 13 descendant directories.

- A later metadata-only filesystem count found 60 regular plist files in `~/Library/LaunchAgents`;
  the earlier Brain/D43 metadata receipt covers 56. All 60 files were privately captured and
  restored into temporary inert quarantine; member hashes matched. A later value-filtered projection
  classified the four extra files as Steam cleanup, two Zoom services, and Homebrew PostgreSQL 16.
  Their selected path fields were reduced to root categories; environment values were not inspected
  or interpreted, retained, or emitted. All
  four labels were already loaded without a PID, with three nonzero last statuses. No definition was
  installed or loaded by DR. These are host application services, not part of the replacement Brain
  baseline. All 56 Brain/D43 definitions remain historical inert custody and need per-role
  authorization before any later install. The copy is still a live provisional capture without an
  atomic owner-wide boundary; see the
  [`LaunchAgent custody receipt`](evidence/portability-dr-launchagent-custody-2026-10-14T0143Z.json)
  and [`root/dependency map`](evidence/portability-dr-root-dependency-map-2026-10-14T0221Z.json).

- At 23:01Z, a receipt-pinned no-follow hash reconciliation compared the existing 60-member Kingston
  archive with 61 current regular LaunchAgent files. All 55 Brain definitions, the one protected
  D43 definition, and four previously captured host definitions match by name and bytes; there are
  no changed or missing members. One new entry is outside the Brain/D43 namespace; its label matches
  its private basename. Its environment-variable key and count were inspected, but no values were
  read. Keep that non-Brain/unclassified entry private and unresolved, outside Brain custody. The
  private per-file roster is on the pinned
  Kingston root; its digest-only public record is the
  [`LaunchAgent reconciliation receipt`](evidence/portability-dr-launchagent-reconciliation-2026-10-15T2301Z.json).
  This closes only the observed Brain/D43 definition byte-custody gap at per-file observation time.
  It does not preserve xattrs/ACLs, prove a cross-file source point, bind loaded services to disk or
  running code, or change NO-GO restore/erase readiness.

- The private root/dependency map stores metadata for the immediate owner/research `work/` children,
  all 88 entries in private Brain config, and all 60 LaunchAgents; raw paths stay on Kingston. The
  contemporaneous census counted 285,577 owner and 330,273 research files. Among 657 recorded entries,
  604 have extended attributes (mostly `com.apple.provenance`); ACL markers were absent. Values were
  not read, and current custody archives do not preserve xattrs/ACLs. This root-only map is not a
  recursive owner-state closure; the subsequent metadata inventory below adds recursive paths but
  not semantic ownership. Broad activation/deployment basenames also match generated files, so exact
  nonprotected pointer closure remains open.

- A later recursive metadata-only inventory records 320,989 owner and 411,217 research entries
  encountered under the two `work/` roots, including root directories, under a sensitive-subtree
  pruning rule. It records lstat fields, xattr names,
  symlink target text without following links, and ACL presence where determinable. It did not read or
  hash regular-file payload bytes. Seven owner and nine research sensitive-name entries are lstat-only
  exclusions; their directories were not descended. All 67 owner and 99 research protected-name
  candidates from the separate private inventory are present with matching recorded lstat fields;
  the 12 Kingston candidates remain in that separate inventory. The earlier research census counted
  21 more regular files and three more directories. This scan pruned three sensitive-name directories
  and does not count their descendants; the scopes differ and the deltas do not prove deletion. The
  root directory signatures
  stayed unchanged, but recursive metadata was not checked again after scanning. The private map is
  therefore useful for path topology and candidate reconciliation, not a source snapshot or custody
  set. See the [`recursive work metadata receipt`](evidence/portability-dr-recursive-work-metadata-2026-10-14T0230Z.json).

- The three research directories pruned by the sensitive-name rule are named `wallets`. A separate
  one-second lstat/scandir-only pass counted 21 files and three subdirectories beneath them (69,375
  apparent file bytes); child names were transiently enumerated to traverse entries but not retained
  or emitted, and no payloads were read or copied. Those counts numerically account for the earlier
  census deltas of 21 files and three directories. The scans were at different times, so this does
  not prove historical membership continuity. Their contents and role remain unknown. The user
  authorizes opaque custody only; v4 plans their metadata classification as private, unresolved,
  backup-required state without opening payloads. No payload has been read or copied. Capture still
  requires a qualified T and Kingston publication path. See the
  [`pruned-subtree metadata receipt`](evidence/portability-dr-pruned-subtree-metadata-2026-10-14T0241Z.json).

Scope caveat after that receipt: a later broad `git grep` covered tracked `docs/`, `src/`, `tools/`,
and `tests/`, which includes `docs/evidence/`. Its output was truncated before matching files could
be classified. No result was used for protected-data analysis or copied into project artifacts, and
no files were changed by the search. Whether a protected receipt line was printed is unresolved;
the refresh receipt's `protected_payloads_read=false` applies only to its 01:12Z capture. Do not
rely on that later search output or treat it as protected custody evidence.

- A separate Codex control-center worktree was found under host-local worktree storage. Its branch
  tip was absent from the queried remote branch heads and from the research checkout's object DB;
  the exact queried remote result does not prove no other copy exists. A 19,421,684-byte Git bundle
  containing its 131-commit reachable history and a 3,010,560-byte tar of its ignored `.local`
  tree were written create-only to Kingston through the descriptor-bound storage writer at
  `host-recovery/provisional-opaque/codex-worktree-20261014-a1/`. The tar
  contains 32 files (2,941,740 source bytes) and six directories; all 32 per-file hashes are in
  the private backup manifest. A fresh empty bare repository recovered the branch tip and all nine
  commits absent from cached origin refs; all tar member hashes matched after rereading the backup
  from Kingston. The immutable [`provisional Codex worktree custody receipt`](evidence/portability-dr-codex-worktree-custody-2026-10-14T0133Z.json)
  pins the bundle, tar, private manifest, and completion marker hashes. This is opaque, provisional
  custody: the three log files may have changed outside the per-file read windows, xattrs/ACLs are
  not preserved, and no application
  state was interpreted. All 10,661 other ignored-status records (10,650 regular files and 11
  symlinks) were also copied into a separate opaque tar and verified after Kingston reread. Their
  metadata remained stable during capture, and a post-capture status comparison matched all archive
  member paths. The 10,693 status records reconcile as 10,631 under `node_modules`, ten under
  `dist`, 11 under `.ruff_cache`, nine other ignored records, and 32 separately captured `.local`
  files. Their role and rebuildability/disposability remain unclassified; empty directories,
  xattrs, and ACLs are not preserved. A follow-up metadata check found all 11 symlinks resolve to
  regular files inside the captured tree; those targets were present and hash-matched in the
  isolated restore. This closes these path-level dependencies only, not package/runtime readiness
  or external dependencies. See the
  [`ignored-state custody receipt`](evidence/portability-dr-codex-ignored-custody-2026-10-14T0140Z.json).
  See also the [`symlink closure receipt`](evidence/portability-dr-codex-symlink-closure-2026-10-14T0145Z.json).
  These archives are provisional live copies and do not establish a consistent source point or close
  the owner-wide recovery boundary. All three archives (`.local`, private config, ignored state)
  have since been restored into temporary quarantine roots, with every file hash and symlink link
  text checked; the temporary roots were removed after verification. No contents were parsed or
  executed. This is only a local-group restore witness, not a Brain-wide clean-room restore; see the
  [`opaque restore witness receipt`](evidence/portability-dr-opaque-restore-witness-2026-10-14T0141Z.json).

- Host-tool follow-up found an unversioned owner-only Helius MCP wrapper and small Codex/Cursor MCP
  configuration files. The wrapper reads a private `.env.local`, pins `helius-mcp@2.2.0`, and embeds
  an absolute path to the research checkout's `.env.local`. A targeted value-free credential scan of
  the wrapper and parsed config fields found no recognized credential patterns. Codex high-entropy
  values were heuristically path/URL-like; embedded secrets are not ruled out. All three files were copied into a private Kingston static-custody capture
  and hash-verified by a fresh verifier, then restored into an isolated quarantine and byte-checked.
  They remain inert: none was installed or executed. The capture manifest contains no `.env`-named
  member; owner and research `.env.local`, Codex/Cursor auth/session stores, and MCP-specific app
  caches were excluded from this capture. Future recovery archives must exclude `.env.local`
  credential files and auth/session stores; classify other cache/build roots separately before
  treating them as disposable.
  Configuration values remain private and potentially sensitive pending full review. On a replacement host, reauthenticate and rebind the wrapper/config paths
  to verified roots before any separate authorization to use MCP. This aid does not qualify Brain
  restore. See the [`private MCP tool custody receipt`](evidence/portability-dr-private-mcp-tool-custody-2026-10-14T0200Z.json).

No providers, prospective collectors, owner services, champion, protected evaluation, or trading
state were changed by the DR work. No provider request or collector was initiated by the DR work.
No protected application or dataset reader was used. No qualified owner-wide recovery set, classified
F01/D43/reserved custody set, or complete Brain restore witness exists. The provisional Codex bundle may
contain protected-like objects whose role remains unclassified. Recovery-set code currently binds
only caller-declared roles and one opaque D43 root; it does not prove full owner-root closure,
F01/reserved custody, service-definition closure, or semantic replay. Recovery-secret custody is not required for the accepted plaintext
custody scope; provider credentials require user reauthentication after restore. Source consistency
and completeness remain unresolved and block `SAFE TO ERASE`.

Independent Sol review of the published `beac49989afb416695c46258c2e0c747e93e2df9` handoff
confirms **NO-GO** under the accepted healthy-Kingston/plaintext scope. The review found no material
claim correction: current receipts prove provisional local-group custody and narrow isolated
restores, not an owner-wide consistent cutoff, complete owner/protected custody, or a full inert
Brain restore. The next material horizon requires interactive admin authentication for a qualified
source view, a monitored snapshot headroom budget, then complete custody and isolated restore.
Research checkout authentication remains a clean-host user step. Sol review deferred an optional
exact-commit local source bundle: remote user reauthentication is accepted, while the bundle adds
little recovery value and would duplicate reachable history into plaintext Kingston custody.

Independent Sol review of the current operator-flow increment confirms **NO-GO** under the same
accepted scope. The receipt supports only the same-Mac synthetic six-role conductor/witness/assessor
path; expected set and witness pins were generated by that run and are not external recovery
authority. No further non-invasive inventory or synthetic rehearsal materially closes the remaining
gates. Next progress requires an admin-assisted owner-wide source view, complete root/dependency and
protected-custody classification, capture from that view, a real Kingston K frontier, and user Git
authentication plus an isolated clean-compatible-host restore.

Sol separately reviewed the 02:50Z operator deployment-pointer pass and the 02:55Z active service-root
pass. Both scopes and limitations are accepted: the deployment pin matches its manifest, and five
declared nonprotected working-directory entries match the private inventory while three sensitive
or protected-candidate entries remain excluded. Neither pass proves loaded process/code identity or
changes the owner-wide custody and source-consistency NO-GO.

The v5 global held-inventory engine first passed a bounded synthetic-fixture review. It inventories
all declared fixture roots before streaming, reconciles cross-root hardlinks, verifies
member/payload relationships, and extracts into a mode-0700 quarantine on a separate disposable
APFS device. Its source and candidate initially shared a temporary device; no Kingston or owner
data was used. A one-million-row SQLite probe measured 249.42 bytes per row, 2.282 seconds, and
39.88 MiB peak RSS. The older 819,547-entry/30-GB capacity figure was arithmetic modeling only.
Sol accepted that synthetic scope, not production capture. Symlinks and xattr/ACL/flag values remain
evidence only, not restored; ACL grants are not qualified. The historical 6,053-group `.git` waiver
remains unresolved custody. See the
[`v5 synthetic global-inventory receipt`](evidence/portability-dr-global-held-inventory-v5-synthetic-2026-10-16T0500Z.json).

The new production-gated v5 factory now binds an externally approved real Darwin `HeldDataT` plan,
a nine-role policy-roster digest, exact target UUID/root identity, a descriptor-bound writer, and
source/target device separation. It revalidates source and target identity/capacity at full and
per-object/chunk cadence and requires separately pinned private APFS/HFS scratch for independent
verification. A disposable read-only APFS source to target capture produced a 10-entry, 1,144-byte
candidate with fresh readback; the target was detached and reattached read-only, then the independent
verifier passed using a third APFS scratch volume and external candidate/plan/target/source pins. A
separate real production inventory run traversed 819,557 entries from a synthetic read-only APFS
view in 667 seconds, using a 345,612,288-byte SQLite spool, 46,153,728-byte peak RSS, 36 sampled
peak file descriptors, 98 full boundary checks, and 819,551 fast identity checks. That scale run
stopped before candidate/payload capture. Two smaller scratch-reserve trials failed closed before
publication. Sol accepted the bounded factory layer; see the
[`v5 production APFS and scale receipt`](evidence/portability-dr-v5-production-apfs-scale-2026-10-16T0615Z.json).

This is not a full 30-GB stream/readback. A thin operator `capture` CLI now consumes a mode-0600
externally hash-pinned operation spec plus separately supplied approved-plan and nine-role policy
roster hashes. It accepts only an already mounted read-only Data view and never mounts, unlocks,
creates snapshots, changes services, or semantically reads protected payloads. Its path-free result includes
candidate and input pins while every source-history, closure, T, K, completion, restore, activation,
and erase flag remains false. A failed capture may leave an unaccepted partial candidate. The CLI is
not part of the immutable Recovery kit and must not be run on owner Data or Kingston until the
combined source-view procedure and external role/policy decision are qualified. RecoveryOS execution
and tmpfs scratch remain unqualified. The factory does not qualify a real owner source T, Kingston K,
owner/protected closure, restore, activation, or erase readiness. These gates remain NO-GO.
The exact pushed factory commit `128a25143770ec6e53f2aa7ce4c7f17e1f9ae0b7` was bootstrapped into a
new checkout with a temporary isolated environment, checked lock, and smoke. Its focused 33-test
suite passed against a writable disposable quarantine volume. The production CLI help and independent
verifier against the pinned candidate passed from that clone after reattaching the target read-only
with ownership enabled. This demonstrates published-code reproducibility and current-host verifier
execution only, not clean-compatible-host Brain restore. See the
[`v5 production clean-clone receipt`](evidence/portability-dr-v5-production-clean-clone-2026-10-16T0640Z.json).

The next exact pushed commit `326949cfea307611eaae9053a26e41fcbdb1c5d4` passed fresh isolated
bootstrap/smoke, 41 focused tests, and both CLI help paths. From that clone, the new operator capture
CLI consumed an externally pinned private spec, plan, and role-policy roster; captured a 3-entry,
31-byte nonprotected synthetic file from a separate read-only APFS source to a pinned target; and
passed fresh readback. The target was then reattached read-only with owners enabled, and the clone's
independent verifier passed with a third APFS scratch volume. All authority fields remain false.
This qualifies current-host published CLI integration only. No real owner root, Kingston, Recovery
kit, T/K, restore, or erase evidence is created. See the
[`v5 production CLI clean-clone receipt`](evidence/portability-dr-v5-production-cli-clean-clone-2026-10-16T0630Z.json).
The exact pushed commit `435b2241b9a09265b3eb8023afbc0d9e1d0d6ca1` was then cloned into a fresh
checkout-local virtual environment, installed from `requirements-dev.lock`, and passed the same 21
focused tests with separate-device synthetic quarantine. The capture and storage modules loaded
from the clone. This remains same-Mac synthetic-code reproduction; it adds no real-host restore
evidence. See the
[`published v5 clean-clone receipt`](evidence/portability-dr-v5-published-clean-clone-synthetic-2026-10-16T0503Z.json).

The v5 `extract-inert` path now verifies external index/plan/source identity pins against a
read-only candidate and extracts payload bytes into a new mode-0700 quarantine on a separately
bound writable APFS volume, using separately pinned scratch. It preserves hardlink topology, omits
symlinks, applies no source metadata, never opens extracted databases or code, and performs no
restore or start. Sol approved the bounded implementation after final review; 46 focused tests pass.
The production-boundary `extract-inert` CLI E2E passed against a tiny synthetic candidate. An
owner-enabled disposable APFS image was attached with `hdiutil attach -owners on`, keeping the
storage guard intact and avoiding admin credentials. The initial 07:00 receipt contained a
source/scratch UUID labeling contradiction and is explicitly superseded; use only the
[corrected same-host APFS extraction E2E receipt](evidence/portability-dr-v5-inert-extraction-apfs-e2e-correction-2026-10-16T0703Z.json).
The correction records a fresh exact-commit CLI run: the source plan pin is the synthetic source
volume UUID `15105a35-…`, while scratch is on the host Data volume `430740ca-…`; target and
destination UUIDs are distinct from both. The CLI checked all pins and reported readback success.
A separate fresh-process check matched exact included membership and payload bytes against the
candidate member page: one 31-byte file, exact SHA-256, no symlinks, excluded subtree absent, and
quarantine/file modes 0700/0600. Source and candidate target remained mounted read-only; the
disposable destination was owner-enabled APFS with its pinned volume UUID and root marker.

This is deliberately limited evidence: same Mac and same filesystem image rehearsal, three
candidate entries with only 31 payload bytes, and no clean-compatible-host reconstruction. It
does not qualify owner Data T, Kingston K, full host restore, protected-state closure, activation,
or erase readiness. The exact pushed `e5455d7ceb7e4340854b6d5447987f56a71886b4` had already passed
fresh shallow clone, locked environment rebuild, smoke, 46 focused v5 tests, and CLI help; the
repository-wide `make check` and `make test` remain blocked by baseline lint and collection debt
outside this DR slice, as recorded in the
[`v5 extraction clean-clone receipt`](evidence/portability-dr-v5-inert-extraction-clean-clone-2026-10-16T0654Z.json).
See the [`v5 extraction clean-clone receipt`](evidence/portability-dr-v5-inert-extraction-clean-clone-2026-10-16T0654Z.json)
for the exact-code bootstrap and focused test evidence; it is independent of, and weaker than,
the disposable-volume extraction E2E above.
The repository-wide `make check` and `make test` remain red outside this DR slice: global Ruff
reports 107 findings on `research/active` versus 110 on current `main`; full test collection reports
204 import/archived-reference errors and 17 skips on `research/active` versus 8 errors and 13 skips
on current `main`. All changed v5 files pass Ruff and the focused suite; the collection failures
prevent claiming a whole-repository test run.
This work qualifies no owner capture, Data T, Kingston K, full-host restore, activation, or erase.

## State ownership and restore rules

| State | Canonical source of truth | Recovery rule |
|---|---|---|
| Research code, history, canonical docs, committed receipts | Git remotes and immutable commits | Clone the exact accepted `research/active` commit; verify clean identity; rebuild dependencies from checked-in pins. |
| Owner code and deployed runtime identity | Owner Git remote plus exact runtime bundles, deployment pointers, and state under owner `work/` | Restore an inert copy; never infer the deployed bundle from repository `HEAD` or recreate active services automatically. |
| Large research captures, datasets, journals, immutable evidence, and recovery records | Kingston `BrainData` plus each object's manifest, lineage, STOP, and recovery records | Verify UUID, root marker, artifact hashes, and semantic replay before admission. This is canonical storage, not a second backup. |
| Unique local owner/research state | Local owner/research `work/`, `~/.config/brain-research`, and Brain LaunchAgent definitions until a verified Kingston custody set is published | Capture from one read-only APFS source view where available. Use SQLite online backups for declared non-protected databases. Preserve unknown and unfinished state; never copy a live database/WAL family file-by-file. |
| Private provider configuration | User reauthentication/reissuance; current `.env.local` files are not needed for inert restore | Exclude every `.env.local` credential file from recovery archives. Record metadata only where needed; do not open or hash secret bytes. Keep local values mode 0600 and never place them in Git, public receipts, logs, plists, or process arguments. |
| Protected F01, D43, and reserved holdout | Existing protected source roots and their frozen declarations/manifests/lineage | Preserve exact bytes, classification, and lineage as opaque custody. Verify byte/custody metadata only; no protected application readers, replay, training, evaluation, admission, or relabeling. |
| Host bindings | Per-host private config, including Kingston UUID/root binding | Recreate from the attached volume and verify explicitly. Paths are locators, not artifact identity. |
| Owner developer-tool integration | Private Kingston quarantine of the Helius wrapper and Codex/Cursor MCP config | Restore only to quarantine first; treat configuration values as potentially sensitive, rebind host paths, and reauthenticate before separate MCP use authorization. This capture excludes `.env.local`, Codex/Cursor auth/session stores, and MCP-specific app caches; classify other cache/build roots separately. This state is not required for inert Brain restore. |
| Runtime and recovery authority | Versioned source, immutable journals/STOPs, activation records, and service definitions | Restore inert. Expired anchors, STOPs, spent reservations, ambiguity, gaps, and failures remain facts; none grants restart permission. |
| Environments and caches | Checked-in pins for dependencies; caches are disposable only after unique evidence is ruled out | Rebuild environments; do not treat old virtualenvs or Homebrew state as the source of truth. |
| Credentials for developer tools and host login | Account/password-manager recovery and user-controlled reauthentication | Reauthorize after reset. Do not copy Keychain wholesale as a substitute for documented secret custody. |

Preserve source bytes and original STOP/recovery histories. Keep event, availability, receipt, and
decision times distinct. Preserve `observed`, `unknown`, `censored`, and `failed` dispositions.
Sealed declarations can contain historical paths; retain original bytes and use explicit transport
locators for new restores. A recovery point is the last independently verifiable durable boundary,
not the newest timestamp. For unfinished work, preserve the uncertainty around it; never turn a
missing prefix into a clean history.

## Backup-only custody procedure

The user authorizes read-only inventory and backup-only copying. Plaintext custody on the bound,
owner-controlled Kingston volume is an explicit accepted scope for this iteration; do not describe
these artifacts as encrypted or as a second physical copy. The protected procedure is:

1. Define the custody set from path/type/size/metadata only: original protected bytes and their
   declarations, manifests, membership/split records, STOPs, and recovery dependencies. Keep the
   private path inventory under the verified Kingston root. Reject unresolved symlinks and
   out-of-scope filesystem dependencies. Do not inspect, parse, train on, evaluate, admit, or
   relabel protected content.
2. Use a qualified immutable source view. Prefer one read-only APFS snapshot covering the internal
   owner checkout, runtime/state roots, and protected roots. Record volume and snapshot identity
   plus cutoff. If dependencies live on other filesystems, give each its own explicit recovery
   point. A crash-consistent snapshot does not claim that an application operation completed.
3. Stream metadata-preserving custody objects beneath the verified Kingston root through a held
   descriptor and create-only names. Do not capture provider secret values; the user reissues them
   after restore. Nonsecret private host configuration remains private on Kingston and must not be
   copied into public receipts.
4. Reopen every object with a fresh verifier; validate byte hashes, member completeness, and
   metadata without a protected application reader. Fsync content and manifests, then publish a
   create-only group with a commit marker last. Failed or interrupted output is unpublished and
   cannot count as custody.
5. Qualify metadata and failure behavior using synthetic files before any protected stream. Retain
   the source snapshot through custody artifact verification and publication acceptance, then
   release it promptly before a longer restore rehearsal. If the measured churn/headroom budget
   cannot safely cover that short capture window, abort before capture. Remove or quarantine only
   generated scratch, never original protected bytes or STOP/history.

The reproducible 2026-10-14 synthetic rehearsal is
[`tools/brain_dr_synthetic_archive_rehearsal.py`](../tools/brain_dr_synthetic_archive_rehearsal.py),
bound by its source hash in the receipt. It used `bsdtar 3.5.3` and GnuPG 2.4.8 in an isolated GPG
home and verified metadata and encryption failure handling on synthetic files only. It does not
qualify a Kingston writer or publication protocol. Protected data was not read or copied. State the
eventual custody receipt as “opaque backup bytes processed;
protected semantic access/evaluation/training false,” rather than claiming that raw bytes were never
read.

## First-host code entry

`recovery/ENTRY.txt` is the retained legacy card; the current versioned first-host card is
`recovery/ENTRY-v2.txt`, stored at `BrainData/host-recovery/ENTRY-v2.txt` for use before a repository
checkout exists. A trusted handoff
outside Kingston supplies the exact recovery source commit and the expected card SHA-256; do not
treat the card or a hash stored beside it as its own trust anchor. The source commit is also the
final bootstrap commit, so the target is explicit and does not follow a moving branch. The card
checks the mounted Kingston UUID and existing root marker read-only, then uses interactive Git
authentication to fetch that exact commit into a new temporary entry checkout. The checkout's card
bytes and hash must match Kingston before the existing `brain_host_recovery.py bootstrap` helper is
run. The independent handoff must instruct the operator to calculate and compare the Kingston card
hash before executing any command from the card; the handoff-pinned card digest must be checked before following that card’s commands.

The card bytes were last changed at commit `edf2898ba4fb877ed8bee159fc9673754f7e12e4`; their
SHA-256 remains `ac8859ee4fc499ffe55dc1824be11a55fd4475fee2dbe0cae749a7f6cbbb5b76`. The card hash
pins the entry instructions; it does not select the source commit. For each recovery, the external
handoff must pin the intended accepted source commit and confirm that this card hash is expected at
that commit. The restore-flow implementation commit `75f580b2741ed34dff47c7ec567a53388979c503`
contains the same card bytes. A fresh shallow clone of that published branch tip rebuilt the locked
development environment under a new isolated HOME, passed project smoke and the focused flow tests,
and launched the flow CLI from a neutral working directory. The clone and Git authentication still
used this Mac; this tests published-code retrieval and bootstrap, not clean-host credentials or Brain
state restoration. The earlier exact-commit bootstrap at `68253b84c5aefd8676b85b9415d2d242ada58d73`
also passed dependency check and smoke. The prior handoff pin to `edf2898...` authorizes only that older code commit and legacy card; it is
historical, not the current entry target. Published v1-v4 bootstrap locators remain immutable history
and are retired for operator entry. The current locator is
[`BOOTSTRAP-HANDOFF-v5.json`](../recovery/BOOTSTRAP-HANDOFF-v5.json), with its operator sequence in
[`BOOTSTRAP-HANDOFF-v5.txt`](../recovery/BOOTSTRAP-HANDOFF-v5.txt). Use only annotated
`refs/tags/brain-dr-handoff-v5` from the accepted `origin`, never a latest-tag lookup or moving
branch. Published pins are tag object `0f90e09307075364d50b6fd68a7f139faa13ff3f`, peeled H
`7fe921db1ca45af7418f28c6388b97f7415ab11a`, and handoff JSON SHA-256
`2cb400b2e3e13d7dac91b3d1da3eceb301945316cd8329313b9acd7a2db37597`. A fresh empty Git client
retrieved the exact tag, checked annotated-tag and peeled-commit types, and passed strict schema,
pin, duplicate-key, and hostile numeric-boolean checks. The pinned C
`0e7076d08cbf75fe157083a2e3c96f927d7b6034` clean-clone receipt records `make setup`, 51 focused
tests, and smoke passing. Its repository card `recovery/ENTRY-v2.txt` has the pinned SHA-256
`f40e8e160b38fffa6e0ba8e4aa81dc87523850e5dd4cca04b0a8add5e8677c37`; the physical Kingston copy
was not read during v5 qualification and must be compared before following its commands. The
replacement host must use normal user-controlled Git authentication. v5 grants code bootstrap only:
no accepted recovery set, Data T, Kingston K, Brain-state restore, activation, final readiness, or
erase authority is established. See the
[`v5 origin retrieval receipt`](evidence/portability-dr-bootstrap-handoff-v5-origin-retrieval-2026-10-16T1013Z.json)
and [`v5 exact-C clean-clone receipt`](evidence/portability-dr-v5-bootstrap-code-clean-clone-2026-10-16T1010Z.json).
The prior v4 origin-retrieval correction and exact-C two-clone rehearsal remain historical evidence
for v4 only; see their immutable receipts in the evidence index.

The helper makes a second exact-commit clone into a separate new destination, installs the locked
development requirements, and runs smoke. Both checkouts must report the pinned `HEAD`, and the
final helper result must say `complete` with `smoke=passed`. The temporary entry checkout remains
until this result is recorded; only that exact run-owned directory may later be removed. This
two-clone path avoids a pre-checkout executable and preserves the final checkout's normal Git
remote. It costs an extra source clone but no additional dependency installation.

This path produces **code bootstrap verified** only. It does not restore private or durable Brain
state, qualify Kingston custody, activate services, send provider requests, prove a clean-host
inert restore, or change erase readiness. Wrong/missing Kingston identity, auth failure, a changed
card, a wrong commit, a preexisting destination, or an incomplete smoke result blocks the flow.
At commit `8d6dd13bc0c8f0f94610c9dd65ebd4b1dd06c26d`, the helper applies
`-c http.version=HTTP/1.1` to the Git clone and exact-commit fetch commands only, reports that
transport in its result, and leaves global/repository Git configuration and interactive
authentication unchanged. The helper completed on a fresh same-Mac checkout at that exact commit;
locked environment setup, dependency check, and smoke passed, followed by 17 focused recovery tests.
This validates the helper's current code-bootstrap path using the existing host authentication
context, not clean-host authentication or state restoration. See the
[`HTTP/1.1 bootstrap rehearsal receipt`](evidence/portability-dr-bootstrap-helper-http11-e2e-2026-10-16T0110Z.json)
and [Git's configuration documentation](https://git-scm.com/docs/git-config).
The previously recorded clean-`HOME` auth failure remains a real prerequisite; this card does not
copy old-host credentials. The Mac-loss recovery procedure below still governs data and runtime.

A same-Mac rehearsal from that fresh checkout recreated the host-only Kingston binding in an
isolated temporary profile. `bind-existing` and `check --require-root` verified the already-mounted
volume UUID, existing root marker, and a 50 GiB free-space floor; a wrong UUID failed without
creating the config. This writes only the private host config, not Kingston. It does not test a new
host, volume attachment, member integrity, a recovery set, or the durable K frontier; the UUID and
root marker still require an independent trusted handoff. See the
[`host-only Kingston binding rehearsal receipt`](evidence/portability-dr-clean-profile-kingston-binding-2026-10-16T0111Z.json).

The first-host card now has two fixed source names: legacy `recovery/ENTRY.txt` and versioned
`recovery/ENTRY-v2.txt`. The v2 card applies command-local HTTP/1.1 to its pre-checkout clone and
fetch, closing the transport gap before the bootstrap helper is available. It leaves Git's
persistent configuration and interactive authentication unchanged. The restore-flow inspector
selects exactly one of those two names by the independently supplied card digest, verifies the same
name and bytes on the pinned Kingston root, and reports both relative paths. It rejects no match,
duplicate matching bytes, or a source/Kingston name mismatch; arbitrary handoff-supplied card paths
are not accepted. The legacy Kingston card remains unchanged; the versioned card was published
create-only and rehearsed as described below.

At commit `75f3e0405c1fca473eb95fae81fc21c20e289651`, a fresh same-Mac checkout executed the v2
pre-checkout clone and exact-commit fetch with command-local HTTP/1.1; the card bytes and SHA-256
matched the source checkout. The helper then built a second exact-commit checkout, installed the
locked environment, and passed smoke. Forty-eight focused restore-flow, handoff-assessor,
recovery-set, and bootstrap tests passed. `ENTRY-v2.txt` was then published create-only beneath
Kingston through the storage writer guard, with descriptor-bound readback; the original `ENTRY.txt`
hash remained unchanged. The guard created the standard root-level `.research-storage.lock`. A
read-only inspect using a synthetic blocked-path handoff matched both v2 card paths and hashes, then
stopped at `set_missing` with the target and run parents empty and all authority flags false. See
the [`v2 entry/bootstrap and Kingston publication receipt`](evidence/portability-dr-entry-v2-exact-bootstrap-and-kingston-publication-2026-10-16T0120Z.json).

The authenticated `origin` repository is accepted as the external trust domain for the accidental
Mac-loss model; this does not protect against loss of Git account/remote access or a compromised
remote. Handoffs v1-v3 are retired for operator use and must remain unchanged. The annotated v4 tag
is the create-once locator for handoff commit H, separate from pinned bootstrap commit C. It is not a
signature, and a replacement host cannot detect a same-remote tag retarget; record the tag-object and
peeled commit IDs. H pins only code/card and Kingston identity: recovery-set, T, K, activation, and
erase authority remain explicitly unavailable/false. Sol reviewed v4 and approved its bootstrap-only
publication. Exact retrieval and failure-case evidence are in the
[`v4 origin retrieval receipt`](evidence/portability-dr-bootstrap-handoff-v4-origin-retrieval-qualification-2026-10-16T0715Z.json).

The local-fixture rehearsal passed both exact-HEAD checks, locked environment creation, smoke, and
the wrong-commit, wrong-volume-pin, missing-authentication, and preexisting-target failure cases.
Its success path used an ephemeral local bare remote on the current Mac, so authenticated GitHub
bootstrap and physical clean-host deployment remain unproven. The final rehearsal also recreated
the host-only Kingston binding in temporary `HOME` and checked the existing root. It supersedes
the earlier 06:06Z rehearsal iteration, before the trust-order correction. See the
[`pin-first first-host entry rehearsal receipt`](evidence/portability-dr-first-host-entry-rehearsal-2026-10-14T0610Z.json).

## Restore procedure

1. On a clean compatible macOS host, install Git and a supported Python. Sign in to the Git account
   that can read the private remote through its normal user-controlled authentication flow; never
   copy the old host's Keychain, tokens, or session stores. Clone the exact accepted
   research commit from `origin/research/active`; confirm the commit and a clean worktree.
2. Rebuild the base development environment from `requirements-dev.lock` and run `make smoke`.
   Optional ML dependencies remain separate and task-specific.
3. Attach Kingston; recreate a mode-0600 host-only binding using the actual mount path. Verify
   volume UUID, case-sensitive APFS, ownership, mount identity, 50-GiB free-space floor, and
   `BrainData` root marker. On a fresh host with no private binding, create the mode-0600 binding
   with the existing root identity pins:

   ```sh
   .venv/bin/python tools/research_storage.py bind-existing \
     --config "$HOME/.config/brain-research/storage-v1.json" \
     --mount /Volumes/Kingston --data-root BrainData \
     --expected-volume-uuid 8f8f1081-9be4-4914-9111-75f1b90255bf \
     --expected-root-id 9428695af6d4f46c08e1a444cdc66de8 \
     --min-free-bytes 53687091200
   .venv/bin/python tools/research_storage.py check \
     --config "$HOME/.config/brain-research/storage-v1.json" \
     --min-free-bytes 53687091200 --require-root
   ```

   Do not run `create-root` against an existing corpus.
4. Restore the accepted local-state group into a new isolated incoming location. Verify artifact
   and private manifest hashes, archive completeness, and metadata for every member. Apply source
   identity, SQLite integrity, durable-prefix, lineage, STOP, gap, and unknown/censored/failed-state
   checks only to non-protected owner/research state and Kingston corpus dependencies. For
   F01/D43/reserved members, verify opaque byte and custody metadata only; do not use protected
   application readers or perform replay, admission, training, or evaluation.
5. Restore owner runtime and paper state inert. Keep all LaunchAgents unloaded; do not initialize
   databases, checkpoint the original state, restart jobs, or issue provider requests during a
   rehearsal. Recreate only the documented service set after separate role authorization and
   non-protected source/identity/STOP checks. Protected material remains excluded from application
   readers and all semantic validation.
6. Compare the restored evidence frontier and runtime identities to the accepted recovery receipt.
   Any missing prefix, failed replay, unclassified state, unresolved dependency, or identity mismatch
   blocks readiness. Provider reauthentication is a later user action and is not part of inert
   recovery.

### Inert artifact restore conductor

For a recovery set that already has an independently pinned set ID and `INDEX.json` SHA-256, use
`tools/brain_host_inert_restore_v1.py` with the verified host-only storage binding, an empty target
parent on a device separate from Kingston, the exact research repository root, and the owner
checkout plus any other source roots that must be excluded, each supplied as a repeatable
`--forbidden-root`. Include private config and LaunchAgents roots explicitly when they are source
roots on the host. The command checks all six roles before creating `brain-restore-<nonce>`, writes
only inside that new quarantine, and prints one JSON result.
Save that output as the local restore record; the command does not publish a public receipt or
persist the result elsewhere. Pass the saved full result directly to the read-only assessor:
`.venv/bin/python tools/brain_host_restore_assessment.py --storage-config <private-binding> --handoff <restore-result.json> --format json`.
The assessor checks the conductor result digest, exact handoff fields, false activation/erase
authority, and absolute target paths before mapping pins and targets into the existing assessment.
That digest detects accidental modification; it does not authenticate the result or independently
accept its pins. Treat every handoff-supplied pin as untrusted until it is compared with a separate
trusted recovery receipt. The assessor remains negative-only and reports blocked while source
closure and durable T/K boundaries are unresolved. The conductor's successful result is not
`Brain restored` and is never erase approval. The assessor also accepts the standalone
`assessor_handoff` object when one is provided separately; that form has strict schema and authority
checks but no enclosing conductor digest.

### Pinned post-bootstrap restore flow

`tools/brain_host_restore_flow_v1.py` composes the existing set verifier, inert conductor, and
negative-only assessor after the exact code checkout is available. Its `inspect` command is
read-only. `restore-inert` validates a canonical, mode-0600 handoff whose SHA-256 is supplied
separately, the exact clean execution commit, the entry-card hash in Git and on Kingston, Kingston
UUID/root identity, accepted six-role set pins, a required source-root roster, and isolated local
target/run parents. The `research_checkout` declaration must identify the exact execution checkout;
v1 requires the set's `research_commit` to equal `execution_commit`. The handoff schema does not
accept a clean-host claim from its caller. Same-host rehearsal requires `--same-host-isolated` and
remains labeled same-host.

Before creating local run records, the flow reopens the run parent without following symlinks,
compares its device/inode with inspection, checks that it remains outside Kingston and every
declared source root, and revalidates the host-only Kingston binding. An unsafe run parent produces
no records. Missing entry cards and Kingston binding failures return named blockers rather than raw
tracebacks. A safe run parent may retain a blocked record (for example, `set_missing`); this does
not create a quarantine or write a Kingston restore witness. Before extraction the flow rechecks
the empty target parent's device/inode and isolation. It persists the conductor result create-only,
reloads that saved result through the assessor's strict handoff loader, and requires set, identity,
witness, and stable-root gates to pass before writing `RUN_COMPLETE.JSON`. A partial run is retained
for manual review and is never resumed or overwritten. There is no start/activation command. Every
result fixes `activation_authorized`, `final_readiness`, and `safe_to_erase` to false.

This is an operator-flow implementation, not complete restore qualification. It does not attest
that the target is a new Mac, prove host-wide service inertness, close owner or protected custody,
qualify Recovery Data T or Kingston K, or authorize activation. The handoff's expected SHA must be
obtained independently of both its file and Kingston; self-generated set pins are not trusted
acceptance evidence. The current Kingston inventory has no accepted six-role set, so only the
blocked path is eligible for live rehearsal; it must stop at `set_missing` before quarantine or
witness creation. The direct script entrypoint now works from a neutral working directory. A
same-host synthetic-handoff rehearsal reached exactly `set_missing`, retained only the private
local request/inspection/blocked records, and confirmed the target remained empty with no set or
witness at the pinned IDs. The handoff was self-generated and is not a trust anchor; this verifies
operator-facing rejection behavior only. Sol accepted the implementation for this blocked path, not
positive extraction. See the
[`post-bootstrap restore-flow receipt`](evidence/portability-dr-post-bootstrap-restore-flow-v1-2026-10-16T0056Z.json).

A fresh checkout of current published commit `6b234796` on this Mac installed the locked dev
environment, passed smoke and restore-flow CLI help, and passed 39 focused restore/set/assessor
tests. A live read-only `inspect` on that commit, without `--same-host-isolated`, observed 30
filtered Brain/D43 launchd labels and 10 selected workspace cwd matches, then returned blocked with
`brain_runtime_not_observably_inert`, `execution_commit_pin_mismatch`, and `set_missing`; all
activation/readiness/erase flags stayed false. Sol accepts only the narrow runtime-guard claim:
these are point-in-time filtered observations, and the stale synthetic pins and missing set are
independent blockers. This is not writer quiescence, a clean-host inertness check, or extraction.
The first direct HTTPS clone attempt produced no output for over 79 seconds and was interrupted with
cause unknown; a retry using command-local `git -c http.version=HTTP/1.1` completed in about 14
seconds. No global Git configuration was changed. See the
[`current-code clean-clone and host-inert guard receipt`](evidence/portability-dr-current-code-clean-clone-inert-host-guard-2026-10-16T0105Z.json).

Operator order for a later accepted handoff:

1. Obtain the canonical handoff file and its expected SHA-256 through a trusted channel outside
   Kingston. Confirm that its execution commit, entry-card digest, six-role set ID/index digest,
   owner/research commits, and eight source-root declarations match the separately accepted
   recovery receipt. The rehearsal receipt's synthetic pins are never reusable.
2. Create a new, private, persistent local run root with separate empty `target-parent` and
   `runs` directories. Keep the root outside Kingston and every declared source root; the target
   parent must be on a device separate from Kingston. Do not reuse a path containing old state.
3. Run `tools/brain_host_restore_flow_v1.py inspect` with the handoff, external digest, exact
   checkout, storage binding, and both local parent paths. It is read-only and returns exit 2 with
   named blockers. Resolve each blocker; `set_missing`, a pin mismatch, or an unresolved source root
   stops the procedure before extraction.
4. Only with `inspect.status=ready_for_inert_restore` and no blockers, run the same arguments with
   `restore-inert`. Preserve its create-only run directory and `ASSESSMENT.JSON`. A blocked or
   partial result remains for review; never resume it or start services from it. Any later service
   activation is a separate, explicit operation outside this flow.

### Operator command selection

| Operator task | Entry point and required inputs | Expected result and boundary |
|---|---|---|
| Inspect current host/storage state | `tools/brain_host_recovery.py status --repo <checkout> --storage-config <private-binding> --format json` | Read-only point observations; unknown or missing fields stay unresolved. This is not source cutoff, T/K, or host-wide inertness proof. |
| Bootstrap code on a replacement host | Exact trusted `BOOTSTRAP-HANDOFF-v5` and its pinned Kingston `host-recovery/ENTRY-v2.txt`; follow the card's two-clone exact-commit flow | Accept only exact HEAD, clean checkout, locked setup, and smoke. Result means code bootstrap only. |
| Inspect a nine-role mixed handoff | `tools/brain_host_restore_flow_v2.py inspect` with private `--handoff`, its external `--handoff-sha256`, exact `--repository-root`, `--storage-config`, `--plan` plus `--plan-sha256`, and `--role-policy` plus `--role-policy-sha256` | Exit 2 and `status=blocked` are expected today. Phase blockers are path-free. Even `metadata_pins_match=true` checks only metadata joins; payload custody and all recovery/activation/erase authority remain false. |
| Inspect or restore a previously accepted six-role set | `tools/brain_host_restore_flow_v1.py inspect` or `restore-inert` with its exact externally trusted v1 handoff and required host/source/target inputs | Legacy six-role only. Do not map v2 roles to v1 or infer a v1 handoff. Run `restore-inert` only after its inspect is ready with no blockers; it remains inert and is not erase approval. |
| Start services or collectors | No command in these restore entry points | Start remains a later, separate, role-specific operation after an independently accepted restore and explicit operator decision. |

The v2 CLI-path test uses disposable handoff/plan/policy/card/index files and mocks the repository
and storage boundaries. It exercises parsing and blocked result rendering without a separate
process, real APFS binding, or Kingston access. Missing and malformed handoffs return static
phase guidance without echoing their paths. This is interface coverage only, not a storage or
restore rehearsal.

## Service restore posture

The 01:12Z read-only `launchctl list` inventory captured 18 loaded labels. Nine had a running PID:
the owner `operator`, seven owner UI processes, and `ai.memecoin.brain.b09r2.source`. Nine were listed
without a PID, including the D43 calendar, paper/audit/train labels, and the research storage-health
monitor. By 01:17Z, eight of 18 had PIDs and `b09r2.source` was loaded without one. This is observed
source state, not a proposed startup set. No Senses capture/watchdog label was listed. Neither probe
proves the source workload or write frontier.

There are 55 Brain-named and one D43-named plist files in `~/Library/LaunchAgents`; the other four
are host-software services. At least 38 Brain/D43 definitions were not represented among the 18
labels loaded at the 01:12Z probe. All 60 definitions remain host-specific custody; the 56 Brain/D43
definitions need per-role authorization before any later install. Their plist byte streams were
parsed to select declared fields; environment values were not enumerated into the projection or
emitted. Reissue credentials through user authentication. Never install the full directory wholesale.
Their declared arguments refer to owner, research, and private-config roots; 56 argument references
pass through only three distinct symlink mappings. Output/error paths point either inside owner work
or to `/dev`; six owner log path references per stream currently point to missing files. These are
path-field references, not a count of unique dependencies. On a replacement host, rebuild any authorized
service definitions against verified roots instead of recreating old symlink accidents. See the
[`LaunchAgent path receipt`](evidence/portability-dr-launchagent-paths-2026-10-14T0124Z.json).

A later metadata-only reconciliation of the 01:43Z quarantined 60-definition plist capture found 321
absolute path references in the selected launchd fields. Four private-config references all match the
private-config inventory. Of 215 owner/work references, 203 match the recursive inventory (105
directory, 65 file, 33 symlink entries); the remaining 12 are the six missing stdout and six missing
stderr log destinations already recorded in the earlier path receipt. None falls beneath the pruned
sensitive directories. Six owner-checkout references and 51 research-checkout references are outside
their respective `work/` roots; no lexical declared path references enter `research/work/`. This closes
declared path topology for the inventoried work/config portions only; it does not identify the
deployed code revision or prove runtime behavior.
See the [`LaunchAgent work-path reconciliation receipt`](evidence/portability-dr-launchagent-work-path-reconciliation-2026-10-14T0238Z.json).

A 02:50Z allowlisted pointer pass found the current `ai.memecoin.brain.operator` plist declares the
owner `brain-b13/operator04` root. The owner code at its clean but 507-commits-behind `a594474`
checkout defines `deployment.json` as exactly `{bundle, pin}`. The 153-byte pointer is a regular
file whose lstat metadata matches the private recursive inventory; its `pin` matches the SHA-256 of
the selected bundle's `manifest.json` (16,876 bytes), also matching that inventory. Neither path
intersects the 178-entry protected-candidate inventory, and no path component was a symlink. Only
the pointer and manifest were read; bundle payloads were not. This pins one declared operator
bundle manifest, but does not bind it to an owner Git revision or prove the running process loaded
it. At the same point, 17 Brain labels were loaded and eight had PIDs; the operator was one, leaving
seven other running Brain code identities unresolved. One D43 label was loaded without a PID. This
does not establish an owner-wide source frontier. Private locators and hashes are in the
[`owner deployment-pointer receipt`](evidence/portability-dr-owner-deployment-pointer-2026-10-14T0250Z.json)
and its Kingston-only manifest.

A 02:55Z metadata-only pass covered the eight Brain LaunchAgent labels then running. It read only
their current plists' `WorkingDirectory` field and lstat metadata. Five declared nonprotected
owner/work working-directory entries matched the recursive inventory; three entries intersected
the protected-candidate inventory or sensitive-name rule and were excluded without retaining their
paths. No environment values, program arguments, process arguments, or protected payloads were
inspected. This reconciles five declared path entries but does not prove that the loaded definitions
match those files, that running processes use those directories, or that their code identities are
known. See the [`active service-root receipt`](evidence/portability-dr-active-service-roots-2026-10-14T0255Z.json)
and its private Kingston manifest.

The four additional host-software definitions are `com.valvesoftware.steamclean`, two Zoom services,
and `homebrew.mxcl.postgresql@16`. They were already loaded without PIDs at the latest probe; three
had nonzero last status. A scoped owner-checkout search found no PostgreSQL source references, which
does not prove that no dependency exists. Keep all four outside the restored Brain service set;
PostgreSQL remains outside pending explicit dependency proof, and other applications may need their
own reinstall/state review.

| Observed role and labels | Clean restore posture |
|---|---|
| Owner control: `ai.memecoin.brain.operator` (running) | Preserve its exact deployment definition and state. Keep unloaded; any later start requires separate owner release/recovery authorization and identity/STOP checks. |
| Storage monitor: `ai.memecoin.brain.research-storage-health-v1` (listed, no PID) | Preserve the definition. Keep unloaded during restore; later activation is a candidate only after the new host binding, source, and plist are verified. It records storage health and has no acquisition authority. |
| Protected evaluation: `ai.memecoin.d43.calendar01` (listed, no PID) | Preserve its definition and protected custody only. Do not install, load, or run evaluation as part of recovery. |
| Legacy jobs/source/audit/train/paper: `b04`, `b09r2.audit`, `b09r2.source`, `b10r2.train`, `b12.paper`, `expansion.paper`, `b10r2.paper`, `b12r2.paper` (`b09r2.source` had a PID at 01:12Z and none at 01:17Z; others had none at the earlier probe) | Preserve definitions and unique state in custody. Exclude from automatic install/start; each needs separate current justification and authorization. |
| Owner UI services: `b12.ui`, `b10.ui`, `expansion.ui`, `b12r2.ui`, `b10r2.ui`, `b12r1.ui`, `b10r1.ui` (all running) | Preserve definitions and exact runtime identity. Leave absent/unloaded on the restored host; a UI label does not prove read-only behavior or deployment authority. |

The initial restored-host baseline is zero loaded Brain/Senses labels. Keep historical plist files in
the private custody set rather than installing the old LaunchAgents directory wholesale. Operator
and storage-health are future owner-role candidates only; this classification does not authorize
enabling either. Watch Market and the integrated Observatory have no accepted owner deployment.
Do not recreate retired Senses services, stale activations, or STOP-bound releases. An inactive
worker receives no owner services, credentials, or protected evaluation scheduler. These restore
rules do not instruct stopping or changing services on the source Mac.

## APFS snapshot T attestor contract

A new `tools/brain_host_apfs_snapshot_attestor_v1.py` consumes an already-mounted snapshot; it never
creates, mounts, or unmounts one. Its synthetic contract suite exercises a versioned host plan bound
by a separately supplied SHA-256 pin, a separately selected snapshot UUID/XID, the pinned Data
volume UUID/group/device, exact APFS snapshot mount source (`SnapshotName@/dev/<Data device>`),
read-only `fstatfs`, `diskutil` plist identity, and three disjoint held no-follow directory roots.
The Darwin adapter invokes `/usr/sbin/diskutil` with a controlled environment. It holds the mount
parent, requires a root-owned parent without group/other write, reopens the mount leaf around OS path
queries, and revalidates the snapshot and roots before borrowed descriptor use and after each borrow.
An injected OS backend always reports synthetic; 17 focused tests pass. The actual Darwin API shape
was checked read-only against the current System snapshot and live Data volume: System snapshot UUID/XID
matched and was read-only; Data had zero snapshots. The System snapshot was used only as API-shape
evidence and is not owner T. The 18 focused attestor tests pass.

A digest comparison proves that the supplied plan matches the separately supplied pin; it does not
prove who approved that pin. Human approval and trusted pin delivery remain external. Snapshot UUID
and XID must be selected and reviewed after snapshot creation before the digest is pinned. Path-based
`diskutil` observations cannot be atomic with held descriptors; the rechecks mitigate replacement
races but do not protect against a local root adversary, which remains outside the accepted home-host
threat model. The result proves mounted snapshot identity and three root handles only. Root
completeness, application consistency, owner/protected closure, capture, Kingston K, restore, and
erase readiness remain false or unproven. No owner Data snapshot exists, and there is no real T.
Sol independently reviewed the implementation contract and required the pin, exact UUID/XID
selection, exact source-name binding, absolute `diskutil` path, and post-query descriptor checks.
The second independent review found no remaining blocker for this bounded identity contract; it did
not qualify plan approval or an actual Data T. Optional plist creation time is normalized as UTC.

## Offline read-only Data view feasibility

A bounded metadata-only feasibility pass found a possible source-view route that does not need APFS
snapshot headroom: shut the owner Mac down cleanly, start it in macOS Recovery (or another qualified
offline environment), unlock Data without mounting it if the volume is encrypted/locked, then explicitly mount the exact Data volume
read-only. The current host's `diskutil(8)` documents `apfs unlockVolume ... -nomount` and
`diskutil mount readOnly`; the read-only mount option denies writes even to superuser. Apple documents
Recovery Terminal and Share Disk on Apple silicon, but Share Disk is a transfer facility and does not
establish a read-only source export ([Apple Recovery guide](https://support.apple.com/guide/mac-help/macos-recovery-a-mac-apple-silicon-mchl82829c17/mac)).
No Recovery boot, unlock, mount, or capture was attempted. The current
`brain_host_tree_capture_v4.py` and `brain_host_source_view_adapter_v1.py` are explicitly
synthetic-only, and the former refuses a non-synthetic held snapshot handle. A pinned execution-kit
candidate now exists on Kingston and passed a bounded synthetic launch probe from its read-only image
on the current online host. It is not a qualified Recovery runtime or real Data-view adapter. Treat
the Recovery route as a conditional candidate, not a currently executable recovery procedure,
qualified T, or approval to interrupt the running host.

A fresh read-only `diskutil info` observation confirms that the current Data volume has FileVault
enabled and is mounted/unlocked under the live OS. A first Recovery Terminal check of the pinned Data
UUID, FileVault state, lock state, and mountpoint is a necessary gate: require `FileVault: Yes`,
`Locked: Yes`, and no mountpoint; stop if any check fails. Record the clean shutdown-to-Recovery boot
chain and every earlier authentication choice. Apple documents Recovery Assistant and notes that a
personal recovery key can unlock the volume while entering recoveryOS, so abort if any pre-Terminal
step unlocked Data ([Apple FileVault deployment guide](https://support.apple.com/guide/deployment/manage-filevault-with-device-management-dep0a2cb7686/web)).
Even `Locked: Yes` is only a point-in-time check; by itself it cannot exclude an earlier
unlock/mount/relock or another Data alias. A qualified history boundary also needs the earliest
available boot-session disk arbitration/mount logs, explicit treatment of missing log coverage, and
enumeration of all Data aliases and snapshots. If those logs cannot establish prior events, retain
the residual assumption and do not claim independently proven no-prior-writable mount or qualified
T. Then, if the history boundary is accepted, consider interactive unlock with `-nomount` followed by
an explicit read-only mount. The current online `diskutil(8)` manual says read-only mount denies writes
even to superuser, but Disk Arbitration may run `fsck_apfs -q`; the local `fsck_apfs(8)` manual says
quick check attempts no repairs. Recovery's bundled versions and actual mount behavior remain
unobserved. See the
[`Recovery locked-preflight feasibility receipt`](evidence/portability-dr-recovery-locked-preflight-2026-10-14T0653Z.json).
Sol independently reviewed this gate and rejected the Locked/no-mount observation as sufficient
history proof. Acceptance also requires shutdown-to-first-probe and authentication records, earliest
available mount events with gaps accounted for, all Data UUID/group/device/mount aliases checked
before unlock, and a second unmounted check after `unlockVolume -nomount`. If the log history cannot
establish prior events, state the remaining assumption; do not claim independent proof or qualify T.

### Recovery boot evidence reviewed 2026-10-15

The final Recovery boot was completed, and the user has returned to normal macOS. Its command set is
hash-bound in the
[`Recovery boot analysis receipt`](evidence/portability-dr-recovery-boot-analysis-2026-10-15T1942Z.json).
The first and last `diskutil info` checks identify the pinned Data UUID as `disk3s5`, with FileVault
enabled, locked, and not mounted. The saved `/usr/bin/log show` output starts 3.658 seconds after
the recorded kernel boot time. It shows a successful APFS probe for `disk3s5`, one automatic mount
attempt, and an explicit failure 19 milliseconds later (`0x0000004D`). Treat that value as an
uninterpreted failure: it does not state why the attempt failed or whether a hypothetical earlier
mount was read-only or writable. No successful Data-volume mount appears in the saved log or mount
listings. The separate `/System/Volumes/Data?owner=0` discovery appears alongside the Recovery
mount-table's `tmpfs` at `/System/Volumes/Data`; it is not evidence that `/dev/disk3s5` mounted.

The final text transcript contains no `listSnapshots` command or output, so the Recovery snapshot
inventory remains unknown. The user clarified that the four photographs document one Recovery
command session interrupted after a command-entry error; the Mac was rebooted and the full command
set was entered again. `docs/snapshot/Вывод Терминала.txt` contains the output from that rerun and is
the transcript bound by this receipt. Its exact 249,641 bytes are now preserved in the canonical
research checkout with the same SHA-256 as the user's working copy; this custody update does not
change the boot interpretation. The photographs are not four independent boots or an independent
copy of that transcript. The final text shows `/usr/bin/log show` working. See the
[`transcript custody receipt`](evidence/portability-dr-recovery-transcript-custody-2026-10-16T0637Z.json).

This boot establishes the point-in-time locked/unmounted state and the logged failed automatic mount
attempt. It does not establish that no earlier mount occurred: pre-Terminal authentication and Data
access choices were not recorded, and the first saved log event follows kernel boot by 3.658 seconds.
The current live macOS read-only follow-up at 20:31Z found the same Data UUID mounted as expected,
one Data role member, and zero Data snapshots; this does not repair the Recovery history gap or
qualify a historical source view. Sol's independent decision is to avoid another Recovery boot
solely to repeat this preflight. Keep prior writable-mount history as an explicit human-attested
residual assumption; no Data T is qualified and erase remains NO-GO. A new Recovery boot is useful
only as part of a separately planned source-view rehearsal; repeating point checks cannot retroactively
close this boot's log gap.

Sol's review confirms that the automatic `disk3s5` mount event is a recorded failed attempt with
uninterpreted status `0x0000004D`; it is not evidence of a successful or writable mount. The
3.658-second early-log gap and unrecorded pre-Terminal authentication leave prior mount history
unknown. Keep `prior_writable_mount_history_verified`, `T_qualified`, and erase readiness false.
Another Recovery boot is not justified to repeat the preflight; perform one only when the complete,
pinned, read-only source-view rehearsal is ready. The live-snapshot alternative is separately
blocked by the documented entitlement requirement and lacks an authorized creation route.

`tools/brain_host_recovery_preflight_v1.py` now implements the bounded first-action observation.
Invocation requires independently pinned Data UUID, volume-group UUID, container UUID, and device
identifier; all four must match before the point-in-time check can pass. It checks APFS
container/group membership, FileVault/lock/mount state, Data snapshots, and matching kernel mount
entries. It resolves APFS sources by container role or `diskutil info` identity; incomplete mount
lines, unresolved aliases in the pinned container, and group-only ambiguity return `unknown`. It
never unlocks or mounts, omits mountpoint and snapshot-name strings from output, and always leaves
prior-mount history and erase readiness false. CLI exit status is nonzero for every blocked/unknown
state. The commands are sequential rather than atomic; recheck immediately before any dependent
Recovery action, and treat the reported host timestamp as untrusted. Seventeen focused synthetic
tests, scoped Ruff/format, and mypy pass. A live read-only run at 09:15Z
matched the pinned Data/container/group/device, found FileVault enabled, Data unlocked and mounted
writable, no Data snapshots, and returned `blocked_unlocked` with `safe_to_erase=false`. This
demonstrates the observer's current-host blocked path only; it is not Recovery behavior, mount-history
evidence, a qualified source view, or an erase gate. The first Recovery Terminal invocation still
needs the user-assisted sequence and history review described above. On Recovery, stop if any pinned
identity differs or any alias cannot be mapped; do not change the expected pins merely to continue.

Read-only `lstat` at 06:15Z found that the named owner checkout/work, research checkout/work, private
Brain config, and `~/Library/LaunchAgents` root entries reported the same local `st_dev` as internal
Data; Kingston `BrainData` reported a different `st_dev` and has its own pinned volume UUID/root.
This groups the root entries only. It does not prove their types, exclude symlinks/firmlinks or other
aliases, place descendants on the same volume, or close root coverage. `st_dev` is a local boot
identifier, not an APFS identity pin. A fresh read-only check found about 12.8 GiB free on Data at
97%, no Data snapshots, and 18 Brain/D43 launchd labels loaded, eight with PIDs. Prior metadata
scans observed active owner-tree churn. There is no credible live non-snapshot cutoff while those
source services remain active.

For a future offline rehearsal, acceptance requires a clean shutdown before the capture environment
starts, no earlier automatic writable Data mount in Recovery before the selected T, exact Data APFS
UUID/group/container/device checks, all source roots bound to the same held Data view (or individually
marked unresolved), an interactive unlock with no credential in argv or logs, explicit read-only
mount and filesystem-flag checks before and after capture, and held descriptor identity checks
throughout. T, if qualified, is scoped to Data; Kingston K remains a separate frontier. A pinned
capture tool/runtime must be runnable offline without installing onto the source Data volume. Stop if
the view is writable or ambiguous, any source writer remains, APFS requires repair/replay, a root
escapes the view, or protected/SQLite/metadata restore semantics are unresolved. The current
machine-readable feasibility observation is the
[`offline-view feasibility receipt`](evidence/portability-dr-offline-view-feasibility-2026-10-14T0616Z.json).

This route does not close protected-root classification, owner-root closure, xattr/ACL restore
semantics, capture-tool availability in Recovery, SQLite recovery behavior, Kingston K lineage, or
clean-host restore. All remain open, and `SAFE TO ERASE` remains false.

Sol independently reviewed this feasibility boundary. The offline route remains a conditional
Data-volume source view, but no real T is qualified and the NO-GO verdict is unchanged. A later
read-only `fstatfs` result cannot prove that Recovery did not previously auto-mount Data writable.
That boot/mount history needs independent evidence from a user-assisted rehearsal or another trusted
observable mechanism. The `HeldDataT` library and `brain_host_held_data_t_cli_v1.py` now provide a
read-only identity/locator check for an already-mounted view. The CLI requires an explicit plan,
external plan SHA-256, and mountpoint; it does not authenticate approval, assert boot history, unlock,
mount, capture, or qualify T. Its synthetic APFS rehearsal is receipt-bound above. Kingston K
remains a separate frontier.

### Mixed held-Data candidate operator CLI

`tools/brain_host_held_data_root_set_v2_cli.py` has two explicit operations: `capture` writes
quarantine-only candidate units and a set index to the canonical Kingston `BrainData`; `verify`
reads a pinned set index and all declared units without accessing a source mount. Both require a
private mode-0600 canonical operation spec and its separately supplied SHA-256. The spec pins the
approved Data plan, role policy, each directory/file policy, source mountpoint, and host-only
storage binding. Both operations reject a different Kingston UUID/root marker; `capture` requires at
least 50 GiB free and takes the writer lock, while `verify` uses a no-lock read-only binding without
a free-space floor. Capture never unlocks or mounts Data; the source must already be a verified
read-only view.
Output is bounded JSON with stable error codes and all qualification flags false. The CLI has no
restore, start, activation, or erase operation. Its synthetic E2E passed; it has not been used with
owner roots. The accepted synthetic result and its limits are in the
[`mixed held-Data root-set rehearsal`](evidence/portability-dr-mixed-held-data-root-set-apfs-kingston-2026-10-16T0300Z.json).

A read-only live feasibility check at 00:10Z on 2026-10-16 found the pinned Data volume mounted
read/write under normal macOS, 46 GiB available (90% used), and no Data snapshots. The bound
Kingston storage check passed its UUID/root pins with 214,413,123,584 bytes available. Local
`diskutil apfs` help exposes no snapshot-creation verb; `mount_apfs` documents a snapshot selector,
but no Data-snapshot mount was attempted. Local `tmutil` help says `localsnapshot` covers volumes
included in a Time Machine backup; it is not an approved arbitrary Data-volume creation procedure.
Earlier preflight found no configured Time Machine destination and no noninteractive admin path.
Follow-up inspection of the local `fs_snapshot_create(2)` manual and Apple's published XNU manual
confirms that every snapshot operation requires superuser privileges **and an additional
entitlement**; the manual does not identify an entitlement available to this operator. `tmutil
destinationinfo` still reports no configured destination, and the local APFS command interface
exposes no snapshot creation operation. Therefore ordinary administrator access does not qualify a
live snapshot path. No entitlement request, Time Machine configuration, snapshot API call, or volume
change was attempted. The live-snapshot path remains unavailable for this rehearsal unless an
Apple-supported, authorized creation and read-only mount route is established independently. See
the [`snapshot API privilege receipt`](evidence/portability-dr-snapshot-api-privilege-2026-10-16T0022Z.json)
and [Apple's XNU `fs_snapshot_create(2)` manual](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/man/man2/fs_snapshot_create.2).
Sol reviewed a live APFS snapshot as a valid alternative source-view architecture, but creation is
held: 46 GiB is not a qualified churn/retention budget, no supported creation/admin procedure is
qualified, and the active writer ownership and quiescence boundary remain unresolved. A successfully
attested snapshot can establish filesystem cutoff T; it does not prove application operations
completed, bind roots outside the snapshot, or reconcile Kingston K. Before any creation, qualify
the privileged mechanism and read-only mount, a conservative free-space floor and bounded retention,
the writer inventory/stop/resume procedure, three same-view roots, and separate K reconciliation.
Do not improvise snapshot creation, thinning, deletion, rollback, or service stops. See the
[`live snapshot feasibility receipt`](evidence/portability-dr-live-snapshot-feasibility-2026-10-16T0010Z.json).

A read-only recheck at 01:01Z confirmed `tmutil localsnapshot` is present, but its local manual
defines snapshots over APFS volumes included in a Time Machine backup; it has no target-volume
argument. Time Machine still has no configured destination, no backup is running, and neither `/`
nor Data has local snapshots. Live Data is mounted/unlocked with about 44 GiB free. Kingston remains
mounted at its pinned UUID, and the recovery-set index still has no accepted set. Sol reviewed the
finding and rejected running `tmutil localsnapshot`: command presence does not establish Data
inclusion or a supported Data capture route, and the free-space and snapshot-retention budgets are
unqualified. No Time Machine setting, snapshot, Kingston artifact, or service was changed. Keep T
false; no further Recovery boot is justified until a complete source-view rehearsal is ready. See
the [`tmutil localsnapshot review receipt`](evidence/portability-dr-tmutil-localsnapshot-review-2026-10-16T0101Z.json),
[Apple's Time Machine snapshot guidance](https://support.apple.com/en-ie/guide/mac-help/mh35933/27/mac/27),
and [Apple's local snapshot retention guidance](https://support.apple.com/en-ie/102154).

### Pinned execution-kit candidate

The candidate at `BrainData/host-recovery/offline-execution-kit-v1/candidate-20261014T0624Z-a1.dmg`
is pinned by an immutable image manifest and was published create-only to Kingston with exact fresh
descriptor readback. A same-host probe mounted the image read-only, used an isolated HOME/PATH and
Kingston scratch, launched the bundled recovery CLI, imported SQLite/hash support, and exercised
only the synthetic capture path and interruption cleanup. It made no provider requests, activated
no service, and read no Data or protected payload. The candidate image remained hash-stable. See the
[`execution-kit rehearsal receipt`](evidence/portability-dr-offline-execution-kit-2026-10-14T0630Z.json).

This is an online current-host probe, not an offline Recovery or clean-host rehearsal. A metadata-only
`otool` survey covered 82 Mach-O members and 12 symlinks. It found Homebrew absolute install names
and RPATHs. A sandbox probe denying reads beneath `/opt/homebrew` stopped dyld at the absolute
Python.framework dependency before Python startup. The earlier invalid-shadow-library probe remains
inconclusive and does not prove fallback behavior. The candidate is therefore not independent of
this host's Homebrew prefix; its dependency closure and Recovery compatibility are unqualified. Do
not alter the owner's Homebrew installation to simulate absence.

A second immutable candidate, `candidate-20261014T0635Z-a2.dmg`, bundles five required dylibs,
rewrites the Homebrew references found in 85 surveyed Mach-O members to bundled-relative load names,
removes Homebrew RPATHs, and regenerates local ad-hoc signatures; explicit macOS system references
remain. Its manifest pins 3,689 runtime members and 373 source files. It was
published create-only to Kingston with exact fresh descriptor readback, then the Kingston copy was
mounted read-only and tested while sandbox rules denied all reads beneath `/opt/homebrew`. Selected
SQLite, hashing, TLS, decimal, and lzma imports and the recovery CLI help path passed with dynamic
loader override variables unset; no absolute Homebrew install names or RPATHs remained in the Mach-O
survey. See the [`dependency-closure rehearsal receipt`](evidence/portability-dr-offline-execution-kit-closure-2026-10-14T0637Z.json).

This closes only the dependency path used by those current-host probes. It is not a clean compatible
Mac or Recovery test; system library/version compatibility, unexercised Python modules, real source
capture, and restore remain open. Ad-hoc signing supplies no publisher identity or notarization. Sol
independently accepted this bounded current-host closure claim; it did not qualify Recovery,
clean-host compatibility, exhaustive dynamic closure, or Data T.

A third immutable candidate, `candidate-20261014T0640Z-a3.dmg`, removes one dangling Homebrew
`site-packages` symlink. Its static audit verified all 3,688 runtime members against the manifest,
resolved all 11 remaining symlinks and 15 `@loader_path` dependencies within the image, checked the
10 explicit macOS system dependencies against the allowlist, and found no unresolved entries. The
exact Kingston-published image then passed synthetic descriptor capture, inert topology restore, and
interruption cleanup under the same Homebrew-denial sandbox; its image hash matched before and after.
The test used only disposable fixtures and did not access Data, protected payloads, services, or
provider APIs. See the
[`capture/restore rehearsal receipt`](evidence/portability-dr-offline-execution-kit-capture-restore-2026-10-14T0640Z.json).
Sol independently accepted this narrow current-host result from the receipt and recorded procedure;
Sol did not open or re-hash the Kingston image.

Static review of the third image's manifest found a packaging-validation gap: it claimed macOS 15.0,
while four bundled dylibs (`libcrypto`, `liblzma`, `libsqlite3`, and `libssl`) declare
`LC_BUILD_VERSION minos 26.0`. Immutable a4 corrects the declared minimum to 26.0 and its runtime
and archive hashes were read back from a read-only Kingston mount. A later neutral-cwd replay found
that the earlier a4 CLI launches imported from the research checkout, not `source.zip`; the archive
has no explicit `tools/__init__.py` and omits the HeldDataT CLI/model. Those launch claims are
invalidated. The archive-only attempts fail with `ModuleNotFoundError: No module named 'tools'`.
See the [`a4 entrypoint correction`](evidence/portability-dr-offline-kit-entrypoint-correction-2026-10-16T0042Z.json).

Immutable a5 packages an explicit `tools` package, the recovery status and preflight CLIs, the
HeldDataT CLI/model, and the complete storage verifier package beneath `src/`. The source loader now
supports zipimport's `/src` subpath. The exact Kingston image was freshly mounted read-only; the
completion marker, image and manifest pins, all 3,688 runtime members, and all nine source entries
matched. From `/private/tmp` with an empty HOME, isolated environment, bundled CPython, and
`/opt/homebrew` reads denied, all three CLI help paths and configured storage status passed. Status
resolved the configured Kingston UUID/root and reported zero provider requests. This qualifies only
these archive-only entrypoints and current-host status path; the source archive is not a complete
Brain restore bundle and no clean-host or Recovery compatibility, real Data T, K, owner restore,
protected custody, or erase readiness is established. See the
[`a5 execution-kit receipt`](evidence/portability-dr-offline-execution-kit-a5-2026-10-16T0034Z.json).

This still proves only bounded tool and dependency behavior on the current online host. Real Recovery
availability, system-library compatibility, clean compatible-host restore, no-prior-writable-Data
mount evidence, real Data T, owner/protected custody, Kingston K, and full restore remain open. The
Recovery compatibility and mount-history rehearsal is user-dependent. Metadata-only root closure can
proceed separately without interrupting the live owner host. Do not erase or reboot the Mac as part
of this work.

### Ignored roots and unpushed owner-worktree history

A read-only Git/filesystem metadata census covered the current research checkout, owner checkout, the
research verifier worktree, and the linked owner Codex worktree. It found path-class candidates with
names associated with Python caches, build outputs, virtual environments, and dependency directories,
plus the already excluded `.env.local` credential files. Names and ignore rules do not prove that
every ignored artifact is disposable, reproducible, or free of unique evidence; no deletion or
recovery-exclusion decision follows from this census. Git reports each `work/` root as one ignored
entry; previous metadata-only counts are 330,273 regular files plus 108 symlinks under research
`work/`, and 285,577
regular files plus 18 symlinks under owner `work/`. Those roots are still opaque and backup-required.

The owner Codex worktree also has a clean `control-center/observatory-handoff` branch with nine local
commits beyond its cached local upstream ref. A verified Git bundle containing opaque reachable Git
objects/history was written create-only to Kingston at
`BrainData/host-recovery/git-bundles/control-center-observatory-handoff-20261014.bundle`; its exact
head, upstream, and bundle hash are in the
[`ignored-root custody receipt`](evidence/portability-dr-ignored-root-and-branch-custody-2026-10-14T0645Z.json).
The commit payloads were not semantically inspected and could contain protected-like material; the
bundle is not merged into `research/active`. An earlier read-only remote query found the tip absent
from the queried remote ref set only; it does not prove there is no other remote copy.

The same owner worktree contains ignored `apps/control-center/.local` state: six directories, 32
regular files (three `.log`, 21 `.png`, eight `.json`), and 2,941,740 bytes. No contents were read.
Classify the current live state as private, opaque, unresolved, and backup-required until it is
covered by an accepted source view or separately screened. A provisional Kingston tar with
per-file hashes was previously verified and restored to quarantine, but it is an uncoordinated live
copy: three logs may have changed outside individual read windows, and xattrs/ACLs were omitted. A
06:50Z current-path hash check confirms the same archive remains present. `lsof` reported no open
files under the source path at that instant, which does not prove source inactivity or a cutoff. The
fresh Git bundle does not contain `.local`. See the
[`opaque archive recheck receipt`](evidence/portability-dr-codex-local-archive-recheck-2026-10-14T0650Z.json)
and [`claim correction receipt`](evidence/portability-dr-ignored-root-claim-correction-2026-10-14T0651Z.json).
This review also does not close owner/protected roots or establish a durable source boundary.

A follow-up `lstat`/device lookup verified that the declared owner/research checkouts and `work/`
roots, private Brain config, LaunchAgents, owner Codex worktree, and its `.local` state are directory
entries on the current Data APFS volume (UUID
`430740ca-42e7-4e6c-a07a-e4e7370152cc`). Kingston `BrainData` is a separate directory entry on its
pinned volume UUID/root. The named entries had no symlink path components and resolve to their
declared paths. This is an entry-level identity check only; descendants, internal firmlinks, full
root closure, and a held offline Data view remain unproven. See the
[`root entry identity receipt`](evidence/portability-dr-root-entry-identity-2026-10-14T0647Z.json).

The v4 private custody planner and synthetic-only artifact exerciser classify one exact three-root
group as private, opaque, unresolved, and backup-required. The separate
`tools/brain_host_tree_capture_v4.py` now exercises the source capture path against a synthetic held
snapshot handle: it traverses all three held roots without following links, builds the complete
cross-root hardlink roster using stat/type observations before excluding exact `.env.local` files
and every alias, then probes xattr/ACL/flag metadata only for included objects. It streams only
included regular-file groups, records symlink text without materializing targets, and fails closed
when metadata cannot be observed. Darwin symlink metadata uses `O_SYMLINK` descriptors; the Linux
fixture adapter uses `/proc/self/fd` with no-follow xattr calls at the leaf. Darwin ACL values use
the versioned `acl_to_text` representation
(whose returned text allocation is released with `acl_free`, as documented by [Apple's acl_to_text
manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/acl_to_text.3.html));
the ACL and allocated text buffers are both freed. A disposable Darwin fd probe observed ENOENT
for no extended ACL; other ACL errors remain unresolved. It captures only disposable synthetic
filesystem fixtures and does not access project state.

Metadata reads check planned object identity before and after access and validate exact xattr, ACL,
and flag obligations. After source streaming, the implementation re-walks the held roots and
metadata, compares the canonical roster, and rereads the exact output member set (objects, manifest,
and receipt) through fresh no-follow descriptors. Interruption, source drift, or post-borrow
revalidation failure removes only its nonce-scoped temporary tree. Tests assert that excluded
credential inode bytes and xattr values are never read, a path swapped to a credential inode fails
before metadata reads, unresolved outside hardlink aliases remain explicit, excluded `.env.local`
symlink text is not queried, symlink targets are not followed, unsupported xattr inventory fails
closed, xattrs are captured, drift and interruption do not leave artifacts, and readback hashes
match. The 71 focused attestor/v4 tests,
scoped Ruff/format, strict mypy for the capture module, and `git diff --check` pass.

This path requires a synthetic `HeldSnapshotT`; its receipt sets `complete_marker_published`, real
T, real capture, Kingston custody, restore verification, and erase readiness to false. The
synthetic flag is an in-process label, and the output parent is caller-supplied: invoke this API only
with disposable fixture roots and private scratch. It is not wired to owner-data capture or
persistent publication and cannot mint a production `COMPLETE` marker. Darwin execution is tested
on this Mac's temporary fixture filesystem, not a real read-only Data snapshot. Populated ACL
handling, symlink xattr restoration, and owner-scale in-memory roster capacity are not qualified.
This is an implementation contract test only; source completeness, Kingston durability, a full
restore, and factory-reset readiness remain unproven.

The next synthetic boundary, `tools/brain_host_tree_restore_v4_synthetic.py`, consumes only that
capture bundle and recreates included directories, bytes, and within-capture hardlink topology under
a fresh 0700 disposable quarantine. It validates the canonical plan, receipt-bound manifest hash,
exact source bundle membership, every captured object hash, metadata evidence roster, and a second
source-bundle read against the original pins before returning. The witness remains under a pending
name until fresh quarantine and capture readback pass, then is renamed descriptor-relatively and
read back again. It never materializes symlinks, including absolute or dangling
links; exact `.env.local` entries and aliases remain absent. Source xattr/ACL/flags/owner metadata is
preserved as a mode-0600 evidence member but is not applied. External hardlink aliases remain
explicitly unresolved by count, without a guessed location. The witness also fixes `real_T`,
Kingston custody, full restore, and erase readiness to false.

The focused capture/restore suite passes 21 tests (85 combined APFS attestor/v4 planner/synthetic
planner/capture/restore tests). It includes cross-root hardlinks, unresolved
outside aliases, excluded credentials and aliases, absolute/dangling symlinks, corrupt objects,
missing/unexpected members, wrong object/group mapping, preexisting-target refusal, FIFO rejection,
simulated cross-device link failure, interrupted open/write cleanup, bundle replacement, and
post-write quarantine mutation. Readback checks exact tree membership, object bytes, hardlink inode
identity, private metadata evidence, witness bytes, and
source capture integrity against the initially verified manifest, receipt, and object map. Object
preflight streams hashes under a 128-MiB per-object and 512-MiB aggregate synthetic fixture limit.
Restore also checks distinct hardlink groups have distinct inodes and every file's link count equals
its in-quarantine alias count. After acquiring the quarantine descriptor, recursive failure cleanup
is descriptor-relative; if it cannot acquire that descriptor, it removes only the still-empty
nonce directory and reports incomplete cleanup if that fails. Ruff and strict mypy pass for the
restore implementation. This is still
same-host disposable synthetic evidence: it does not exercise owner state, a real T, Kingston,
compatible-host restore, metadata application, launchd, or user authentication. Caller-selected
scratch and the process-local synthetic label are not authority controls. It does not change the
NO-GO factory-reset verdict.

Sol independently accepted this bounded implementation after reviewing its input/output identity,
cleanup, FIFO rejection, link-topology, and publication-failure boundaries. Sol did not rerun the
85 tests and did not access owner or protected payloads; the pass count above is local execution
evidence, not an independent reproduction.

## Portability boundary and remaining risk

The currently tested host bootstrap is macOS-specific. Kingston qualification uses `diskutil`, APFS,
and macOS volume-ownership checks; recurring host services use `launchd`; owner bundles and some
filesystem metadata are macOS-bound. A clean Mac can rebuild the research development environment
quickly, but owner runtime recovery, private secrets, services, protected custody, and full corpus
replay are not yet rehearsed.

Linux/server recovery requires a separately qualified volume-identity and descriptor-bound writer
backend, a service-manager adapter, private secret delivery, and storage semantics for object-store
or multi-worker paths. Shared mutable SQLite remains single-writer. No cluster, object-store,
replication, or Kingston-loss capability is implied by this work.

Kingston is presently the only reset-surviving physical copy of `BrainData` and any future owner
recovery artifacts. Its failure destroys the only declared restore source. This single-copy risk is
accepted only for recovery from owner-Mac loss. A future independent large SSD/NAS must be treated
as a new replication capability with hash verification and a full recovery rehearsal.

## Remaining gates before `SAFE TO ERASE`

The Recovery compatibility/point-check probe has been completed once; do not request another boot
merely to repeat it. A **user-dependent** Recovery boot remains necessary only when a concrete
source-view rehearsal is ready. For that combined gate, record clean shutdown and every
authentication/recovery-key/UI choice, then make the first Terminal action check the pinned Data UUID,
FileVault, lock, and mount state before Disk Utility or source access. Capture boot time, earliest
available disk arbitration/APFS logs, the complete APFS volume/group listing, kernel mounts, and one
`listSnapshots` attempt; repeat the identity and mount checks at the end. An inaccessible snapshot
listing or incomplete log is `unknown`. Stop on changed identity, unlocked or mounted Data, any
unmapped alias, or any earlier successful or uncertain Data mount. If the earliest history cannot be
established, retain the explicit human-attested assumption; do not claim proven no-prior-writable
mount. This gate alone still does not authorize unlock/mount or qualify T. Metadata-only
owner/local-root closure can proceed independently.
The identity-only `HeldDataT` layer does not perform the source-view mount or establish boot history.
It has only been exercised against the synthetic APFS image in its receipt. If the offline route is
infeasible, the live-snapshot alternative currently has no qualified creation mechanism: ordinary
root is insufficient under the documented API entitlement requirement, and there is no configured
Time Machine destination. Reconsider it only after an authorized, supported mechanism, conservative
Data free-space floor, bounded snapshot retention, and read-only mount procedure are independently
qualified. The 06:15Z read-only preflight found about 12.8 GiB
free at 97% Data use and no Data snapshot; the prior 06:10Z check found about 12 GiB at 98%. An
offline route must still prove shutdown boundary, exact volume/root identity, read-only mount flags,
held-view stability, and tool availability. The APFS T attestor contract remains synthetic-tested; a
real T still requires an independently pinned source-view identity and Sol-reviewed host plan. A
later read-only status check saw 30 Brain/D43 launchd labels, eight with PIDs, and nine owner/research
cwd matches including the probe and `lsof`; it inspected neither Data nor the storage binding. Counts
differ from earlier point checks and do not establish a write frontier. No service was changed. See
the [`latest live runtime refresh`](evidence/portability-dr-live-runtime-refresh-2026-10-15T2351Z.json).
The earlier process census found 72 processes in the configured owner/research roots. The three
`work/wallets` subtrees are authorized for opaque, private, unresolved custody only; preserve them
without semantic reading and do not infer a role from their names. No complete owner-wide payload
custody set is qualified. The later, narrowly scoped B09 source-root, B09r1 state/bundle, and
private-config delta captures recorded above remain specific partial custody results, not broad
closure. The existing recovery procedure keeps restored services inert; this checkpoint does not
authorize starting them.

A read-only refresh on 2026-10-16 at 05:06Z found 30 Brain/D43 launchd labels loaded, seven with
PIDs. The bounded status command did not inspect owner/research roots, process arguments, or
environments, and changed no service. The pinned Data identity still matched; normal macOS showed
Data unlocked and mounted writable, with zero Data snapshots. This is not a source cutoff or mount
history evidence. Kingston remained mounted at its pinned UUID with 213,833,076,736 bytes available;
the refresh wrote no Kingston artifact. The Recovery transcript remains hash-bound at 249,641 bytes;
the text records the complete rerun after one earlier interrupted command session, includes
`/usr/bin/log show`, contains no `listSnapshots` invocation, and records the failed automatic Data
mount with uninterpreted status `0x0000004D`. The four photographs are from the earlier session, not
four boots or an independent copy. See the
[`05:06Z live-state refresh`](evidence/portability-dr-live-state-refresh-2026-10-16T0506Z.json).

A bounded 07:12Z refresh found the same pinned Data UUID mounted at `/System/Volumes/Data` on
`disk3s5`, with the volume at 92% used. Kingston remained mounted at its pinned UUID as case-sensitive
APFS with owners enabled and 213,834,174,464 bytes free. A label/PID-only `launchctl list` filter
matched 30 Brain/D43 labels, seven with PIDs. The probe read no BrainData payload, arguments, or
environments and changed no services; it does not establish active writers, source cutoff, Data T,
or Kingston K. See the [`07:12Z live-state refresh`](evidence/portability-dr-live-state-refresh-2026-10-16T0712Z.json).

A 07:16Z label/PID-only refresh still found 30 matching Brain/D43 launchd labels and eight with
PIDs; all eight labels were UI labels. It read no arguments, environments, or data payloads and
changed no service. The increase from seven PIDs in the 07:12Z probe has no inferred cause and does
not identify all writers or establish a frontier. See the
[`07:16Z runtime label refresh`](evidence/portability-dr-runtime-label-refresh-2026-10-16T0716Z.json).

The read-only `tools/brain_dr_v6_terminal_prefix_v1.py` helper checks a pinned v6 release's
descriptor-held file identity, exact membership, canonical hash chain, structural request/outcome/
confirmation joins, and opaque request/response byte digests. Thirteen focused synthetic tests pass,
including a full disposable descriptor-backed fixture and fail-closed membership, rooting, payload,
duplicate-key, and inode-replacement mutations; Sol accepted this synthetic scope. One live audit
then passed on 2026-10-16 at 09:45Z after checking the exact release's three labels disabled and
absent, pinned plist hashes, empty `lsof +D` output under the release root, and the pinned Kingston
UUID/root. It verified 21 events, five terminal intents/outcomes/confirmations, four opaque response
members, and 19 pinned member hashes through held descriptors. A preceding attempt stopped at receipt parsing:
the helper had required canonical whitespace for the two SHA-pinned repository receipts. Commit
`08ce3f0` now permits formatting whitespace only for those externally hash-pinned receipts while
still rejecting duplicate keys; journal and release JSON remain canonical. Runtime observations are
external and unbracketed, not independently authenticated. The live prefix still has
`capture_authorized=true` and one unresolved source-manifest mismatch. This is scoped byte/structure
evidence only: no STOP, global Kingston K, Data T, restore, or erase readiness is established. See
the [`live v6 structural-prefix attempt`](evidence/portability-dr-v6-terminal-prefix-live-attempt-2026-10-16T094549Z.json).

The new `tools/brain_host_restore_flow_v2.py inspect` is a negative-only nine-role handoff/index
inspector. It binds a separately hash-pinned private handoff, approved Data plan, policy roster,
clean execution commit, entry card, Kingston identity, and mixed-candidate index; local inputs are
read through a bounded no-symlink descriptor walk and stable owner-only file check. It joins source
presence separately from capture custody, rejects legacy aliases/bridges, and keeps MCP configuration
files in quarantine/reissue-only disposition. It reads card/index metadata only, never capture-unit
payloads, and every output remains blocked with all activation, restore, T/K, and erase authority
false. Sol accepted this interface after review of path, mode, owner, link-count race, join, and
authority boundaries. Failures report path-free inspection phase and a safe operator action tied to
independently approved pins. Seventy-five focused tests across v2/v1 handoff, assessment, and root-set
readers pass with Ruff, formatting, and scoped strict mypy. The pytest CLI-path fixture reaches a
blocked mixed-index result with mocked Git/storage boundaries and rejects missing/malformed handoffs
without reading Kingston. This qualifies only synthetic blocked-path behavior:
there is no real v2 owner handoff, source custody closure, payload verification, clean-compatible-host
restore, T/K, or erase readiness.
The exact pushed `ec14560` was then fetched into a fresh clone on this Mac; locked setup, 72 focused
restore/root-set tests, v2 CLI help, and synthetic smoke passed. `make check` stops at the repository's
first Ruff stage with 107 diagnostics, none in v2; later global check stages did not run. Kingston was
not attached or read. See the [`v2 published-clone rehearsal`](evidence/portability-dr-restore-flow-v2-published-clone-2026-10-16T1002Z.json).
The follow-up exact clone at `52882d5` also passed 75 focused tests, including pytest CLI-path
coverage for a disposable blocked mixed index and missing/malformed handoffs, plus CLI help and
smoke. The fixture mocks Git and the read-only storage guard; it is not a separate-process, real
APFS, or Kingston integration test. See the
[`v2 synthetic CLI-path clone receipt`](evidence/portability-dr-restore-flow-v2-cli-clone-2026-10-16T1005Z.json).

The local v6b recovery-kit candidate packages 21 selected tracked tool/storage files from the pinned
commit and a 3,688-member CPython runtime; all runtime paths, types, modes, symlink targets, sizes,
and file hashes match its manifest. Neutral-cwd imports resolve from `source.zip` with both the
checkout and Homebrew prefix unreadable. On disposable synthetic APFS volumes, HeldDataT found two
present roots and seven unresolved roles; recovery preflight matched identity but stopped at
`blocked_unencrypted`. Archive-only v5 capture produced an eight-member, 2,097,501-byte candidate,
which a fresh read-only verifier accepted. `extract-inert` then copied those bytes to a distinct
three-GiB quarantine volume and completed its internal readback; metadata stayed unapplied and no
service or activation ran. The fixture had three files, five directories, no symlinks, and no
hardlinks. Sol accepted this bounded same-host synthetic integration only. The `synthetic_fixture_only`
result field is false because the production backend ran; it does not describe the content provenance.
The earlier v6 probes launched from the checkout and are not archive-only evidence because checkout
modules could shadow the ZIP. A manifest-pinned v6b transport archive now has a create-only custody
copy on Kingston at `BrainData/host-recovery/offline-execution-kit-v6b/` with fresh descriptor
readback; it contains only the selected source archive, runtime, and manifest. It remains inert,
unexecuted, Recovery-unqualified, and is not a handoff target. This custody write does not qualify a
clean host, Recovery, owner capture, T/K, full Brain restore, or erase readiness; see the
[`v6b archive capture and quarantine receipt`](evidence/portability-dr-v6b-archive-capture-quarantine-2026-10-16T1033Z.json)
and [`v6b Kingston candidate-kit custody receipt`](evidence/portability-dr-v6b-kingston-candidate-kit-custody-2026-10-16T1046Z.json).

A source-only v7 builder candidate defines four explicit entrypoint roles and a versioned 12-module
roster for operator status, Recovery preflight, held-view inspection, and bound-v2 capacity/capture.
It reads only tracked Git blobs from a full pinned commit, binds the builder's own Git blob identity
in the manifest, and rejects execution when builder bytes do not match that commit. A static audit
of the exact 12 rostered source blobs at commit `83074d1a1abfe3e575cbdf9272df767b16c1e1c0` found
no recognized dynamic-import or package-resource calls. Builder tests reject unresolved or
unrostered local imports, extra archive members, and specified syntax forms: direct `__import__`
and `importlib.import_module` calls, imported-function/simple assignment aliases,
`builtins.__import__`, literal `getattr` access to import functions/resources, and tested direct,
assigned, and transitive `importlib.resources` aliases; `pkgutil.get_data` is also recognized.
These checks are syntax-bounded; they do not prove absence of arbitrary computed reflection,
`eval`/`exec`, or native import hooks.
The ZIP uses sorted paths, fixed timestamps/modes, and stored members; its external manifest binds
the commit, role/roster digest, builder and source Git blob IDs, member sizes/hashes, and archive
hash. The runtime requirement is CPython 3.13.6 arm64, minimum macOS 26.0; this builder emits source
only, no runtime or disk image. After publishing commit `83074d1a1abfe3e575cbdf9272df767b16c1e1c0`,
two independent clean clones detached at that exact commit built byte-identical archives and
manifests. Archive-only neutral-cwd `-S` checks imported all 12 first-party modules from the ZIP,
passed six entrypoint help commands, and passed negative Recovery and wrong-spec capture preflights.
See the [`v7 published clean-clone replay receipt`](evidence/portability-dr-v7-published-clean-clone-replay-2026-10-16T1411Z.json).
These checks used the local interpreter, not the separately bundled runtime. No provider, Data,
Kingston, or service operation ran. This qualifies source-kit reproducibility and inert entrypoint
behavior for the exact published commit only; RecoveryOS compatibility, bundled-runtime launch,
actual capture, T/K, restore, and erase readiness remain unqualified. The existing staging helper
still pins v6b and is not a v7 execution path.

A local-only operator staging helper, `tools/brain_host_recovery_stage_v1.sh` (SHA-256
`0724acb9220830517c732a59b3c4e32fee86ab82cfe368501495f860c84019d5`), now pins the exact
deposited archive, manifest, source ZIP, runtime, and Data UUID internally. On this compatible
macOS host it staged that archive from a separate read-only APFS transport volume to a distinct
writable APFS scratch volume; checked every manifested runtime member and source member; ran only
the three inert `--help` entry points from a neutral directory with an allowlisted environment; and
revalidated the staged members, mount identities, read-only/writable flags, scratch device, and
remaining reserve before reporting completion. The rehearsal preserved a 256 MiB scratch reserve.
An impossible near-`2^63` reserve was rejected before creating a stage. Negative identity checks
rejected the pinned Data UUID as transport and rejected a false Kingston UUID on the disposable
transport before staging. Sol accepted this exact helper only as a local, inert staging candidate.
It is not in the Kingston kit and has not run in Recovery; this is compatible-host staging evidence
only. It is not a Recovery command or handoff target. A reset-surviving custody packet and exact
RecoveryOS qualification remain open.

The required approximately 819k-entry/30-GB full synthetic capture and fresh readback remain open.
The prior read-only check found about 12.6 GiB free on internal Data (not Kingston), and measured
host scratch and other disposable volumes cannot satisfy the source, target, and reserve requirements
together. Do not reduce reserves or count sparse logical bytes as streamed payload evidence. Another
Recovery boot is not justified until the complete role/source policy and an adequately sized,
separately pinned capacity plan are ready.

### Conditional offline source-view and capture plan, draft 3

**Conditional; Sol-reviewed as a truthful non-executable design, not an operator authorization.**
The intended work is one combined Recovery session: observe the locked Data/history boundary,
qualify the exact offline kit and Recovery runtime without touching Data, then (only after history
acceptance) unlock
and mount Data read-only for a pinned held-view inventory. Capture to Kingston is a later
conditional phase and may begin only after the nine-role policy, source closure, capacity model,
target identity, and runtime bindings are accepted. Any failed or unknown check ends the session
before payload capture. Do not schedule a boot until the exact pins and complete executable packet
have passed Sol review. This section authorizes no boot, unlock, mount, or write today.

The deposited v6b artifact is transport custody only. Its Recovery compatibility, archive extraction
in Recovery, `diskutil`/mount behavior in the installed Recovery version, and RecoveryOS scratch
capacity remain unqualified. The exact artifact and completion marker must be freshly read back and
hash-checked before the gate; do not replace them with a newer checkout or runtime. The candidate
contains the held-view, root-set, tree-capture-v5, production-capture, and inert-extraction entry
points, but their Recovery invocation and platform filesystem bindings have not been exercised.

The known Data pins from the completed Recovery transcript are volume UUID and volume-group UUID
`430740ca-42e7-4e6c-a07a-e4e7370152cc`, container UUID
`414bc719-2fea-44a6-9952-d12cd31cb0ca`, and observed device `disk3s5`. Treat these as expected
identities, not as permission to continue after any mismatch. The v6b source is commit
`74e5e284cc32e7972b93aa34808088a56c2fd301`; `source.zip` SHA-256 is
`323b114d5d5a59274f41609e47596ef5269593a53c499e8ae09e6748f5c9fee8` and `MANIFEST.json` SHA-256 is
`d7c0ddda0cc52f05fee4353e163f18d134a2c223fff012f575ef63685db6b07a`. It contains 21 selected
source files and 3,688 CPython 3.13.6 arm64 runtime members, declares macOS 26.0, and was tested on
the online host only. Current Kingston free-space observations are not a future capacity pin and do
not authorize a synthetic payload write there.

The pinned production v5 writer creates its SQLite inventory spool on Kingston under
`host-recovery/tree-capture-v5/spool`; this spool is a Kingston allocation, not a separate scratch
device. Its `scratch_reserve_bytes` must be budgeted as `S_K` in the target equation. The independent
candidate verifier uses a different `PinnedScratchV5` interface that accepts only pinned APFS/HFS
scratch, rejects RecoveryOS `tmpfs`, and requires Kingston itself to be mounted read-only
(`ST_RDONLY`). A verifier scratch volume must have a distinct volume UUID and `st_dev` from source
and target. A disposable local APFS-container rehearsal has confirmed that a sibling APFS volume
on the same container passes `PinnedScratchV5.open/validate` while the target sibling is mounted
read-only. A 16 MiB scratch write reduced both volumes' reported available bytes by the same amount,
confirming they expose one shared pool. This is logical volume separation, not a separate failure
domain. It does not qualify a Kingston mutation or full candidate verification. Sol accepted the
conditional design: shared-pool verification must pin the APFS container UUID and enforce the
50 GiB policy floor plus bounded headroom and remaining DB reserve at each verifier capacity check.
The code derives shared-pool headroom from the SHA-pinned private operation spec. Sol reviewed the
current verifier and approved a disposable sibling-volume rehearsal. A 64 GiB local sparse APFS
image was created and inspected, but its shared pool exposed only 29.17 GiB free, below the 50 GiB
floor before verifier scratch; no candidate was read or accepted. The image was detached and
removed. Following Sol's lineage decision, the operator capture CLI hashes and validates the private
operation spec, then passes that digest into candidate creation; production capture writes it into a
versioned INDEX and the readback witness binds the INDEX digest. The direct Python capture API
accepts a digest pin but does not read or validate the private spec bytes, so the validated-spec claim
applies only to the operator CLI path. Ordinary integrity verification continues to accept legacy
INDEX schema, while shared-pool verification requires an exact bound spec. The verifier rejects a
same-container target/scratch pair when shared-pool mode is omitted and fails closed if either APFS
container identity is missing or malformed. An explicitly supplied spec hash is checked for both
shared- and external-scratch API verification. Its shared-pool path still needs a successful
disposable candidate/remount rehearsal. The later sizing-only planner review does not qualify that
path. No Kingston sibling-scratch operation or writable-to-read-only transition is authorized.
These constraints make the proposed one-boot capture-and-verify sequence non-executable today.

Any future gate must establish a worst-case bound for entries, payload, `S_K`, and `M` before
creating the Kingston spool or writing candidate payload. Then recheck Kingston capacity before
spooling. If the production path cannot establish this bound without creating a large Kingston
spool, stop and use the bounded no-payload planning path before Recovery. Capture and readback
verification are separate phases: after capture, close the writer, safely unmount Kingston, remount
the pinned target read-only with an exact reviewed command, verify UUID/root/device and `ST_RDONLY`,
then run the candidate verifier using a pinned APFS/HFS volume distinct from source and target by
volume UUID and `st_dev`. The verifier rejects APFS sibling target/scratch volumes unless shared
pool mode is explicit. If scratch is on a sibling APFS volume in the Kingston container, bind the
exact shared APFS container UUID and use one shared-pool ledger; do not sum sibling `df` values.
The implementation's DB-reserve ceiling is 32 GiB. The new shared-pool code path requires the
SHA-pinned operation spec bound into the candidate INDEX and has focused unit coverage, but remains
unqualified until a real disposable candidate, target read-only remount, low-space rejection, and
post-failure integrity rehearsal pass. Never use Data or the target volume itself as scratch. The
verifier proves candidate integrity only, not global Kingston K.

The `plan-capacity` operator path uses the v5 descriptor-relative role scanner and global hardlink
reconciliation, holds Kingston through the pinned read-only storage guard, and creates its bounded
SQLite planning database only on separately pinned APFS/HFS scratch outside both the Data and
Kingston APFS containers. The planner database is capped at 8 GiB with a retained 256 MiB free
floor and descriptor-bound cleanup. It reads filesystem metadata and symlink text, but no regular
file contents, xattr values, or ACL bytes. The two-stage v2 contract pins an immutable intent,
emits a capacity receipt, and requires a separate final capture spec; a receipt by itself cannot
authorize capture. `B` counts included logical primary-file sizes after global hardlink
reconciliation (so sparse files expand), `M` comes from bounded metadata name/size probes, `J` is
projected from the exact candidate JSON serializers and page splitting, and `S_K` replays the
capture database mutation shapes with a pinned overshoot margin. Before streaming payload, capture
must repeat the metadata-only census and match the receipt-bound inventory digest.

The live capture guard retains the unspent `B/M/J` ceilings and `S_K` cap. It credits output only
after the writer has fsynced the file and a no-follow descriptor confirms its size, identity, and
allocated blocks; when allocated blocks are unavailable it retains the full relevant ceiling.
SQLite is committed and fsynced before its descriptor's allocated blocks reduce the remaining
`S_K` cap. Source, target, free-space, and container pins are rechecked at preflight and bounded
full-capture boundaries. Each verifier pass checks the shared-pool floor at preflight and after
each batch of at most 128 inserted rows/pages, releasing only the measured allocated portion of
`S_V`; the `A` cushion remains reserved. These bounded checks assume no competing writer and do
not provide a mathematical continuous APFS allocation guarantee. `A` is an operational reserve,
not a hard upper bound. `U` is a read-only
observation already reflected in live `F`, so it is recorded and compared but never subtracted a
second time. PersonalBackup is a transition-confirmation gate, not a permanent reserve.

Sol reviewed the exact v2 implementation and accepted the bounded pre-spool/candidate CLI
capability. The focused inventory/production suite has 120 passing tests, with Ruff, formatting,
`py_compile`, and diff checks clean. This source review is not a positive mounted-APFS rehearsal:
the production CLI still needs a disposable plan → capture → fresh-verify run before operational
candidate use. The flat sidecar-reference list remains limited by the 16 MiB `INDEX.json` cap and
may fail closed on owner-scale metadata; do not drop metadata or protected classes to bypass it.
This sizing output does not qualify T, owner custody, Kingston K, restore, or erase readiness.
Synthetic tests are not owner source, Kingston, or RecoveryOS qualification. The
in-process capability token is an internal API convention, not a security boundary against
arbitrary code already running in the same process. RecoveryOS Python/runtime and scratch
availability, owner-role closure, protected custody policy, source cutoff T, and Kingston K remain
separate gates.

1. Shut down cleanly. Record the Recovery entry path, all pre-Terminal user/authentication/key/UI
   choices, and approximate times without recording credentials. Do not open Disk Utility, unlock
   Data, or access a Data path before the first Terminal probe.
2. Make the first Terminal command the read-only identity/lock probe against the pinned UUID:

   ```sh
   /usr/sbin/diskutil info 430740ca-42e7-4e6c-a07a-e4e7370152cc
   ```

   Stop immediately unless the UUID, volume-group UUID, container UUID, expected device, FileVault
   `Yes`, `Locked: Yes`, and `Mounted: No` all match. If Recovery has already unlocked or mounted
   Data, or the expected identity cannot be resolved, do not continue with any source operation.
3. Without unlocking or mounting, record boot time and run these read-only probes once, preserving
   complete stdout, stderr, exit status, and command order:

   ```sh
   /usr/sbin/sysctl kern.boottime
   /usr/sbin/diskutil apfs list
   /sbin/mount
   /usr/sbin/diskutil apfs listSnapshots 430740ca-42e7-4e6c-a07a-e4e7370152cc
   /usr/bin/log show --last 6h --info --style compact --predicate 'process == "diskarbitrationd" OR process == "mount_apfs" OR process == "fsck_apfs"'
   /usr/sbin/diskutil info 430740ca-42e7-4e6c-a07a-e4e7370152cc
   /sbin/mount
   ```

   `listSnapshots` failure or incomplete/unavailable logs mean `unknown`; they are not an empty
   snapshot list or proof of no earlier mount. Preserve the full APFS listing so every alias and
   volume-group member can be mapped. Repeat identity and mount checks at the end. The history
   precondition passes only when the operator records the Recovery entry/UI/authentication path and
   confirms no Data access before the first probe; the first probe reports the pinned Data locked and
   unmounted; and complete boot-session logs cover the interval through the first probe and
   explicitly show every automatic mount attempt and result. Any successful mount before the first
   probe, unexplained identity transition, evidence of a writable mount, or log gap stops the
   source-view phase. The saved session's ~3.7-second pre-log interval is unknown, so its logged
   failed mount attempt and human attestation cannot qualify the history precondition. Preserve that
   transcript and its gap as evidence; do not unlock Data on that boot. T can only be considered
   after a later explicit held read-only view passes its own identity, mount, root, and source-cutoff
   checks.
4. Save a verbatim transcript with command outputs and failures to the recovery evidence intake
   during this same Recovery session. Include Recovery entry/authentication choices, boot time, first
   and last probes, and any observed automatic mount event. Never record passwords or recovery keys.
   Apply the preapproved history precondition during this session. If it fails, any boot interval is
   missing, or an automatic mount attempt has unknown outcome, stop before unlock and preserve the
   uncertainty for review. After returning to normal macOS, hash the completed transcript and bind
   its digest in the receipt.
5. Before any unlock, re-read the exact kit marker and candidate archive digest, verify runtime and
   member pins, and run only read-only archive help/status and filesystem identity/capacity probes.
   No unpinned staging path is allowed. Stop if a required module or system interface is absent or
   the exact Recovery-compatible entry point has not been proven. A local staging helper now
   verifies the pinned kit and launches only inert help commands on this compatible host, but it is
   not deposited on Kingston and RecoveryOS compatibility remains unknown. Independent verifier scratch must
   be a separately pinned writable APFS/HFS volume with known UUID/device and at least 32 GiB +
   256 MiB free before unlock, matching the implementation's maximum verifier DB reserve; no Data
   operation occurs during this check. A lower actual `S_V` can be rechecked after the candidate is
   built, but does not waive the pinned preflight maximum for this one-session plan. This is not yet
   a runnable command packet because exact staging and Recovery invocation remain unqualified.
6. Only after the in-session history precondition passes and the prior-boot uncertainty is
   explicitly carried, interactively unlock the pinned volume without mounting. Recheck UUID,
   group/container/device, aliases, `Locked: No`, and `Mounted: No`. Then perform one explicit
   read-only mount, recheck read-only mount flags and held descriptor identity, and run `HeldDataT`
   for every declared root. Stop if an alias, required/protected root, or hardlink relation is
   unresolved, or if the view is writable or changes identity. HeldDataT/v1 and capture/v5 remain
   unchanged and encode the absent `verifier_worktree` as `absent_unresolved`. Sol has accepted an
   inert v2 plan/witness/policy/capacity/spec/index document-consistency validator for an explicitly
   nonqualifying absence-gap candidate. The bounded v3 capture-adapter draft now covers streamed
   member hashing, bounded disk-backed indexes, targeted protected traversal, reissue precedence,
   external operation pins, INDEX-last publication, and a real temporary-fixture pass through the
   stored-INDEX read-only verifier. Its focused validation passed 200 tests plus Ruff, formatting,
   `py_compile`, and `git diff --check`. Sol's 2026-10-16 hard-fork review rejected it for
   production acceptance: `ScratchSessionV3` has no trusted production adapter, and the SQLite
   spool checks a scratch pathname before `sqlite3.connect(path)` reopens it, leaving a replacement
   window and no descriptor-bound database identity. The read-only verifier is also not yet proven
   after a physical target read-only remount. Keep the public capture entrypoint fail-closed; do not
   start another protocol generation. Reopen this slice only with a held-directory/file-descriptor
   scratch binding and post-open identity checks, then qualify the actual capture flow on distinct
   disposable source, target, and scratch APFS/HFS volumes, including fresh verification after a
   physical read-only reattach and owner-scale resource/free-floor evidence. No such volumes or
   owner-scale qualification were available in this run. A passing result would still be only an
   explicitly nonqualifying absence-gap candidate; T, root closure, K, restore, and erase remain
   false.
7. Only under a separately reviewed plan, run the complete metadata/root/hardlink preflight against
   the held view and establish worst-case Kingston spool, payload, metadata, and partial-publication
   bounds before candidate writes. Re-evaluate the Kingston equation immediately before capture.
   A full-closure capture still requires all nine roles and the protected overlay below to have
   evidence-backed capture, reissue, rebuild, or explicit owner-accepted-loss dispositions, with no
   unresolved custody role. A future v2 production path may separately write an explicitly
   nonqualifying candidate for the eight present roles while preserving the ninth historical gap;
   it cannot establish T, root closure, restore, or erase readiness and has not been implemented.
   Any writer must preserve the 50 GiB policy floor at every guard. It may leave a partial candidate
   on failure; never resume, overwrite, or delete it. Preserve it as unaccepted evidence and require
   a new reviewed plan.
8. Candidate verification is a separate post-capture operation, not part of the current Recovery
   handoff. It requires a reviewed non-force unmount/remount-read-only sequence for Kingston, fresh
   target identity/`ST_RDONLY` checks, and independently pinned APFS/HFS verifier scratch with the
   calculated reserve plus 256 MiB. Preserve verifier inputs/outputs. This verifies candidate
   integrity only; it does not qualify global K. Do not extract, install services, authenticate
   providers, or activate Brain in Recovery.

The preceding sequence is a conditional contract, not an executable user command packet. The local
staging helper narrows one part of the staging/invocation gap but is not yet reset-surviving or
Recovery-qualified. Exact Recovery-compatible paths and staging, a pre-spool capacity bound, the absent-role policy, held-view
and capture arguments, a reviewed Kingston remount-read-only transition, independent APFS/HFS
verifier scratch, and a deterministic boot-history acceptance rule must be pinned before Sol can
approve execution. A clean capture and fresh candidate verification could support K qualification
only after independent reconciliation; they do not establish root/protected closure, clean-host
restore, or erase readiness. Never repair or remap source, target, scratch, or role pins to make a
gate pass.

#### Nine-role disposition policy for the combined gate

The role policy is an externally pinned input; the table defines the permitted disposition, not
proof that roots are present or custody-complete. Exact path-to-role mappings must be resolved from
the accepted held view before capture. Unknown or duplicate mappings stop the operation.

| Role | Gate disposition | Capture / restore rule | Stop condition |
|---|---|---|---|
| `owner_checkout` | Private, unique owner/runtime state; capture required | Opaque byte custody for declared nonsecret roots; keep deployment and local runtime inert; credentials excluded for reissue | Any undeclared descendant, private value needing review, external alias, or missing source point |
| `research_checkout` | Code fetch/rebuild plus local-only custody | Re-fetch exact published commits from Git; separately capture unreachable/ref/reflog and ignored unique state; never assume `work/` is disposable | Unreachable objects, ignored paths, or external hardlinks lack a byte-preserving disposition |
| `owner_codex_worktree` | Private, opaque, backup-required | Preserve worktree administration and `.local` state as bytes; do not interpret candidate artifacts; restore into quarantine only | Worktree metadata or ignored/local state is absent, unstable, or semantically unclassified but omitted |
| `private_brain_config` | Private, backup-required | Preserve noncredential config opaquely; exclude `.env.local`, tokens, auth/session stores for user reissue; rebind host paths only after restore review | Credential classification uncertain or any excluded credential bytes enter the candidate |
| `launch_agents` | Host-specific definitions, backup-required, inert | Preserve definitions and hashes; never install/load from Recovery or restore; later reconstitute individually from verified code/config | Definition-to-role mapping absent or any service is enabled/started by restore |
| `helius_mcp_wrapper` | Private, quarantine/reissue-only | Preserve wrapper bytes opaquely; exclude referenced credentials; no install or execution; rebind verified paths after restore | Wrapper/config/credential boundary cannot be separated without reading secret values |
| `codex_mcp_config` | Private, quarantine/reissue-only | Preserve opaquely; no automatic install/authentication; user reauthenticates | Any secret/session material cannot be excluded or held opaque |
| `cursor_mcp_config` | Private, quarantine/reissue-only | Preserve opaquely; no automatic install/authentication; user reauthenticates | Any secret/session material cannot be excluded or held opaque |
| `verifier_worktree` | Path absent; tracked code commit is fetchable; historical non-Git state is unknown and unrecoverable from current refs | Sol accepted a versioned split disposition: `source_absent_at_T`, exact published commit, and `historical_non_git_state=unknown_unrecoverable` | This can support only a nonqualifying candidate of other present roots after T, with `root_closure=false`; full recovery requires verified prior custody or explicit owner acceptance of the historical gap |

Protected F01, D43, and reserved-holdout data form an additional custody overlay, not extra semantic
roles. Their exact roots and lineage must be mapped before capture; allowed handling is opaque
byte/hash custody only. Never parse, score, train, relabel, resplit, or evaluate them. Preserve
SQLite database/WAL/SHM sidecars together from the same held view without opening them; record
application consistency as unproven. Preserve symlink text and hardlink topology only where the
accepted capture policy proves it; never follow symlink targets. xattrs, ACLs, flags, and provenance
remain evidence-only and must not be applied during inert extraction. `.env.local` and reissue-only
auth stores are exact exclusions; all other exclusions require explicit paths, rationale, and
hardlink-alias closure in the pinned policy.

The latest metadata-only v2 census names its absent ninth entry `research_verifier_worktree`; the
v1/v5 HeldDataT schema names the required role `verifier_worktree`. Sol approved a strictly
versioned split: pin a Data-relative locator in an externally hash-pinned plan, observe
`source_absent_on_held_readonly_view` through the held read-only Data descriptor, pin the fetchable
commit `f330999930afdb29485b89908fb275736587a815`, and record historical ignored, untracked, or
uncommitted state as `unknown_unrecoverable`. The v2 offline validators now bind plan → witness →
nine-role policy → capacity intent → capacity receipt/inventory → capture spec → candidate index,
reject v1 readers, and keep T, K, root closure, full restore, activation, and erase authority false.
Sol accepted this only as an inert document-consistency stage: its synthetic fixtures and digests
are not held-view, Kingston-capacity, payload, or readback observations. No v2 production writer,
CLI, or publication path exists. The schema path may eventually capture the other eight roots only
as a `nonqualifying_absence_gap` candidate, and may call absence `source_absent_at_T` only after a
separately qualified same-view T witness. It does not recover or prove absence of former non-Git
state. Full recovery still requires independently verified prior custody of that state or the
owner's explicit acceptance that this historical gap is outside the recovery obligation.

A live read-only Git worktree listing shows the active checkout and a stale `/private/tmp`
`brain-senses-v3-verifier` entry whose path is absent and whose gitdir pointer is prunable. Its
recorded detached commit `f330999930afdb29485b89908fb275736587a815` is contained by published
branches and the existing DR handoff tags, so its code commit is fetchable. This does not establish
whether the missing worktree held ignored or uncommitted unique state. Do not prune the stale
worktree metadata or treat code reachability as closure. Current live macOS reports no APFS
snapshots for Data, so no current local snapshot source for recovering the missing path was found.
The saved Recovery transcript contains no `listSnapshots` invocation or result; snapshot history at
that boot remains unknown.

The v5 policy also carries a static unresolved-hardlink baseline of 6,053 groups under `.git`.
Current capture policy preserves these as unresolved and cannot claim root closure. The four research
reflog-only commits, linked-worktree administration, ignored/local records, protected-root/lineage
mapping, opaque `work/wallets` and owner Codex `.local` subtrees remain separately backup-required.
These are source-custody blockers, not capacity inputs that can be repaired by increasing Kingston
space.

#### Kingston and scratch capacity worksheet

All quantities are bytes in the signed operation worksheet; display GiB only for operator
readability. This is a post-PersonalBackup-transition Brain worksheet: PersonalBackup has no
permanent capacity reservation. The user will separately move the temporary personal copy from
Kingston to the second MacBook, verify it there, then delete that copy from Kingston. Do not inspect,
move, delete, or reserve an estimated PersonalBackup size. The user has not confirmed that transition
complete, so no Brain capture may rely on post-transition capacity yet. Re-measure the exact Kingston
volume/root and free bytes after that confirmation and immediately before any Brain write. Existing
BrainData artifacts already reduce the observed free-space value and must not be subtracted twice.

| Symbol | Definition | Current value / rule |
|---|---|---|
| `F_now` | Current read-only Kingston availability, UUID `8f8f1081-9be4-4914-9111-75f1b90255bf`, root `9428695af6d4f46c08e1a444cdc66de8` | 213,767,380,992 bytes = 199.086 GiB; baseline only, may change during the user's personal-data transition |
| `F_after` | Kingston availability after the user confirms second-MacBook verification and separately deletes the temporary PersonalBackup copy | Unknown until that future confirmation; this is the `F` used by the Brain write gate |
| `B` | Unique included Brain payload bytes for the pinned role policy | Must be computed from accepted held-view inventory before first payload write; no qualified value exists today |
| `R` | Kingston free-space floor that remains after Brain capture | 50 GiB policy floor; recheck continuously; a smaller tool-internal 8 GiB floor does not override it |
| `S_K` | v5 SQLite inventory spool cap | Replay the actual capture DB mutation shapes in planner scratch; pin the replayed peak plus its 16 MiB overshoot margin. The eventual capture spool lives on Kingston. |
| `S_V` | Verifier SQLite scratch DB cap | The current bound v2 mode requires exactly 32 GiB. The capacity planner supports only a shared target pool; external verifier scratch fails closed until its identity and capacity are separately pinned. |
| `W_V` | Verifier non-DB free-space headroom | Exactly 256 MiB in the current shared-pool mode. |
| `X` | Shared-pool allocation/partial-use margin | Explicitly pinned in the intent and charged once in the shared-pool equation. |
| `M` | Metadata sidecar value bytes | Measured by size-only metadata probes under the capture limits; candidate JSON is accounted separately as `J`. |
| `J` | Candidate JSON bytes | Exact member/payload-index pages, `INDEX`, and `READBACK` from the shared canonical serializers and page split rules. |
| `A` | Conservative operational allocation reserve | Pinned in intent and must meet the count-derived floor below. This is not a mathematical APFS allocation upper bound. |
| `G` | Production writer's fixed Kingston free-space floor | 8 GiB in the pinned v5 writer; additional to `R` and unavailable for Brain payload |
| `Q` | Inert extraction/quarantine capacity for later restore rehearsal | Separate compatible-host destination and scratch; not counted as Kingston payload |

The path-free aggregate from the latest complete traversal of the eight declared-present roots
recorded **29,742,143,024 bytes (27.700 GiB) of regular-file logical size** across 819,063 entries.
This was read-only metadata, not payload; the census excluded the absent ninth role, did not prove
source consistency or root closure, includes files that may later receive explicit rebuild/reissue
or exclusion dispositions, and can count hardlink aliases more than once. It is a historical sizing
observation, not `B` or a capture authorization. Re-reading the receipt-bound, hash-verified census
manifest from Kingston this session produced that path-free aggregate; no source payload or protected
payload was read. The receipt is the [`v2 root-boundary census`](evidence/portability-dr-root-boundary-census-v2-2026-10-15T2239Z.json).

The metadata-only capacity planner now shares the v5 traversal, global hardlink reconciliation,
SQLite schema, and canonical candidate serializers. It computes `B` from unique included primary
file logical sizes (sparse files therefore expand to logical size), measures `M` through bounded
size-only xattr/ACL probes, derives `J` from exact serialization and page splits, and replays the
capture DB mutation shapes to derive `S_K`. It binds the full source inventory and metadata census
into a receipt; capture must reopen the same held read-only view and match that inventory before
the first payload read. Source drift, metadata drift, target-usage drift, or a retained partial
requires a fresh plan. Production capture is entered only through the bound v2 CLI contract; the
Python factory rejects direct real-view calls without its bound-plan authority. The source and
target APFS container UUIDs must differ; the target UUID from the capacity receipt is checked again
at capture start and at every full boundary.

`N_new` is the exact planned number of candidate regular files/directories, the spool child and DB,
and any missing capture parent directories; transient verifier/probe objects are charged to `S_V`,
not counted in `N_new`. The selected `A` must satisfy the versioned operational
floor `A_min = 8 GiB + 32 KiB * N_new + ceil(0.10 * (B + M + J + S_K))`. This is a conservative
operational reserve, not a mathematical APFS bound. A positive disposable allocation study is
still required before real Kingston capture. Capacity credit during capture uses durable allocated
blocks only; if allocation attribution is uncertain, the full relevant reserve remains held.

The pinned shared-target-pool pre-write gate is
`F_after >= B + M + J + S_K + R + G + A + S_V + W_V + X`, with `S_V = 32 GiB` and
`W_V = 256 MiB`. The writer separately rechecks remaining planned output, unused `S_K` cap, and
the fixed floors throughout capture. Both in-capture verifier passes use the shared-pool guard:
after each verifier DB commit and fsync, measured allocated blocks may release only the matching
portion of the pinned `S_V` cap; the remainder and `W_V` stay reserved. Existing `U` is an
informational, identity-bound observation:
it is already reflected in live `F` and must not be subtracted a second time. The old path-count
screening estimate above is historical only; it is not this bound. No personal-data capacity is
reserved for Brain after the confirmed PersonalBackup transition. The current `F_now` is not the
post-transition measurement. The intent/receipt/capture-spec chain does not authorize a write by
itself; the operator must review the receipt and final capture pins. If the worksheet does not fit,
stop before spool/payload capture; do not lower R or write a partial set to Kingston.

Current source-role disposition is:

| Required role | Current classification | Remaining qualification |
|---|---|---|
| `owner_checkout` | Present; unique owner/runtime state remains backup-required | Descendant closure, source cutoff, and complete capture |
| `research_checkout` | Present; current code is fetchable from Git; local `.git` state remains backup-required | Four reflog-only commits and 6,053 `.git` hardlink groups remain unqualified for rebuild/exclusion |
| `owner_codex_worktree` | Present; linked-worktree and local state remain backup-required | `.local`, worktree administration, and ignored-state closure |
| `private_brain_config` | Present and private | Capture nonsecret state from a held view; exclude credentials without opening their bytes |
| `launch_agents` | Present; 56 Brain/D43 definitions match the bounded Kingston archive | Bind definitions to restored runtime/state; keep every service inert |
| `helius_mcp_wrapper` | Private file role; quarantine/reissue only | Held-source custody and host-path rebinding |
| `codex_mcp_config` | Private file role; quarantine/reissue only | Held-source custody; no automatic installation or authentication |
| `cursor_mcp_config` | Private file role; quarantine/reissue only | Held-source custody; no automatic installation or authentication |
| `verifier_worktree` | Absent in the latest root census; v1/v5 still encode `absent_unresolved`; v2 inert schema binds a held-view absence witness | Production adapter, same-view T qualification, and historical non-Git gap disposition remain open |

Cross-cutting holds remain: three research `work/wallets` subtrees and owner Codex `.local` are
opaque, private, unresolved, and backup-required; two `.env.local` credential files are excluded for
reissue. F01, D43, and reserved-holdout bytes and lineage remain protected and unqualified for
complete custody. Do not infer disposability from names or Git ignore rules. Symlink targets remain
unfollowed; external hardlink aliases, SQLite/WAL consistency, and xattr/ACL/flag restoration policy
remain open. Captured metadata is evidence-only and must not be applied during inert extraction.

This plan is a bounded operator aid, not proof that the current package can run in Recovery. The
next evidence must bind the deposited kit readback, exact source/target/scratch volume and root
identities, measured capacity margins, complete role-policy digest, all command outputs, and the
post-boot interpretation. The deposited-candidate readback is bound by the
[`v6b Kingston custody receipt`](evidence/portability-dr-v6b-kingston-candidate-kit-custody-2026-10-16T1046Z.json);
recheck it at gate time. Until the other prerequisites and user-assisted boot are complete, no
source-view capture is ready to run.

1. Inventory every owner/research/local-config/service root, including owner Codex worktree local
   state, and bind each to a source point or an
   explicit reauthentication, rebuild, disposable, or unresolved disposition. Classify potentially
   private environment entries and all excluded Codex worktree ignored records. Keep the three
   research `work/wallets` roots opaque, private, unresolved, and backup-required. The current
   byte-custody check matches all 56 Brain/D43 definitions to the Kingston archive; keep them inert
   until each service role is separately authorized and bound to verified runtime state.
2. Rehearse the conditional offline read-only Data view, including first-action Locked-state gate,
   pre-Terminal authentication log, boot-session mount history/gaps, all Data aliases, and explicit
   read-only held-view checks. If prior mount history cannot be established, retain that assumption
   explicitly. If the offline route is infeasible, qualify a conservative free-space floor, bounded
   snapshot retention, and interactive admin procedure.
   Bind the exact approved Data volume and all declared roots; select and independently pin the
   approved source view. No live-copy pass can prove an owner frontier while state changes.
3. Complete opaque custody of every identified F01/D43/reserved root and its lineage; verify hashes
   without semantic access. Current metadata-only path inventory does not prove F01/reserved are
   absent or covered.
4. Capture the authorized owner runtime/private configuration and unique local research state from
   the accepted source view. Classify and exclude only proven disposable caches.
5. Establish the real Kingston K STOP/pending/durable prefix and reconcile it with the source cutoff.
6. Restore the local archive and Kingston-backed corpus dependencies into an isolated clean
   compatible checkout; run identity, integrity, replay, and inert-service checks. Do not issue
   provider requests or launch a collector.
7. Record the Data snapshot cutoff and separate Kingston frontier, post-cutoff uncertainty,
   exclusions, artifact hashes, host identity, inert service count, and single-Kingston/plaintext
   custody risks in an immutable receipt; commit and push the work.
8. Obtain Sol's independent final readiness review. Until then the owner Mac is not safe to erase.
