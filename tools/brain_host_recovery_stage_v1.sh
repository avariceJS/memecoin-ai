#!/bin/bash
# Stage and launch only the pinned, inert v6b recovery kit on a private APFS/HFS volume.
# This script never unlocks or mounts Data, writes to Kingston, or invokes capture.

set -euo pipefail

fail() {
  printf 'brain-dr-stage: blocked: %s\n' "$1" >&2
  exit 2
}

usage() {
  cat >&2 <<'EOF'
usage: brain_host_recovery_stage_v1.sh ARCHIVE ARCHIVE_MOUNT ARCHIVE_VOLUME_UUID \
  SCRATCH_MOUNT SCRATCH_VOLUME_UUID MINIMUM_SCRATCH_FREE_BYTES
EOF
  exit 64
}

[ "$#" -eq 6 ] || usage

archive=$1
# These are reviewed custody pins, not operator inputs. Update them only with a newly
# reviewed and create-only deposited kit plus its independent receipt.
expected_archive_sha=15fd56f2c5d9c64c7a60e54fbc027f52f3d097871899e9b983f3eaf310c166b9
expected_manifest_sha=d7c0ddda0cc52f05fee4353e163f18d134a2c223fff012f575ef63685db6b07a
expected_data_uuid=430740ca-42e7-4e6c-a07a-e4e7370152cc
expected_source_sha=323b114d5d5a59274f41609e47596ef5269593a53c499e8ae09e6748f5c9fee8
expected_python_sha=4407bdbb4b9983a44a024fd5cd03460fc8e89a5f38b4970002da4a1ec5e86295
archive_mount=$2
archive_uuid=$(printf '%s' "$3" | /usr/bin/tr '[:upper:]' '[:lower:]')
scratch_mount=$4
scratch_uuid=$(printf '%s' "$5" | /usr/bin/tr '[:upper:]' '[:lower:]')
data_uuid=$expected_data_uuid
minimum_free_bytes=$6

is_uuid() {
  case "$1" in ????????-????-????-????-????????????) ;; *) return 1 ;; esac
  case "$1" in *[!0123456789abcdef-]*) return 1 ;; esac
  return 0
}

is_uuid "$archive_uuid" || fail invalid_archive_volume_pin
is_uuid "$scratch_uuid" || fail invalid_scratch_volume_pin
is_uuid "$data_uuid" || fail invalid_data_volume_pin
case "$minimum_free_bytes" in
  ''|*[!0123456789]*) fail invalid_scratch_capacity_pin ;;
esac
while [ "${#minimum_free_bytes}" -gt 1 ] && [ "${minimum_free_bytes#0}" != "$minimum_free_bytes" ]; do
  minimum_free_bytes=${minimum_free_bytes#0}
done
[ "${#minimum_free_bytes}" -le 18 ] || fail invalid_scratch_capacity_pin
[ "$minimum_free_bytes" -gt 0 ] || fail invalid_scratch_capacity_pin
[ "$(/usr/bin/uname -m 2>/dev/null)" = arm64 ] || fail unsupported_host_architecture
[ -x /usr/bin/sw_vers ] || fail required_sw_vers_unavailable
os_version=$(/usr/bin/sw_vers -productVersion) || fail host_version_unavailable
os_major=${os_version%%.*}
case "$os_major" in ''|*[!0123456789]*) fail host_version_unavailable ;; esac
[ "$os_major" -ge 26 ] || fail unsupported_host_version
[ -x /usr/bin/shasum ] || fail required_shasum_unavailable
[ -x /usr/sbin/diskutil ] || fail required_diskutil_unavailable
[ -x /usr/bin/plutil ] || fail required_plutil_unavailable
[ -x /usr/bin/ditto ] || fail required_ditto_unavailable
[ -x /usr/bin/mktemp ] || fail required_mktemp_unavailable
[ -x /bin/df ] || fail required_df_unavailable
[ -x /sbin/mount ] || fail required_mount_unavailable
[ -x /usr/bin/awk ] || fail required_awk_unavailable
[ -x /usr/bin/grep ] || fail required_grep_unavailable
[ -x /usr/bin/tr ] || fail required_tr_unavailable
[ -x /usr/bin/dirname ] || fail required_dirname_unavailable
[ -x /usr/bin/basename ] || fail required_basename_unavailable
[ -x /usr/bin/stat ] || fail required_stat_unavailable
[ -x /bin/chmod ] || fail required_chmod_unavailable
[ -x /bin/mkdir ] || fail required_mkdir_unavailable
[ -x /bin/pwd ] || fail required_pwd_unavailable
[ -x /usr/bin/env ] || fail required_env_unavailable
[ -x /bin/cat ] || fail required_cat_unavailable

is_mountpoint() {
  [ -d "$1" ] || return 1
  local resolved
  resolved=$(cd "$1" && /bin/pwd -P) || return 1
  [ "$resolved" = "$1" ] || return 1
  /sbin/mount | /usr/bin/grep -F " on $1 (" >/dev/null
}

volume_field() {
  local mountpoint=$1 field=$2
  /usr/sbin/diskutil info -plist "$mountpoint" |
    /usr/bin/plutil -extract "$field" raw -o - - 2>/dev/null
}

mount_flags() {
  local mountpoint=$1
  /sbin/mount | /usr/bin/awk -v target="$mountpoint" '
    index($0, " on " target " (") {
      count++
      line=$0
    }
    END {
      if (count != 1) exit 1
      print line
    }'
}

has_mount_flag() {
  local line=$1 wanted=$2
  printf '%s\n' "$line" | /usr/bin/awk -v wanted="$wanted" '
    {
      open=index($0, " (")
      if (!open || substr($0, length($0), 1) != ")") exit 1
      flags=substr($0, open + 2, length($0) - open - 2)
      n=split(flags, items, ",")
      for (i=1; i<=n; i++) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", items[i])
        if (items[i] == wanted) found=1
      }
    }
    END { if (!found) exit 1 }'
}

is_mountpoint "$archive_mount" || fail archive_mountpoint_unavailable
is_mountpoint "$scratch_mount" || fail scratch_mountpoint_unavailable
[ "$archive_mount" != "$scratch_mount" ] || fail archive_and_scratch_are_same_mount
[ "$archive_uuid" != "$data_uuid" ] || fail archive_is_source_data
[ "$scratch_uuid" != "$data_uuid" ] || fail scratch_is_source_data
[ "$scratch_uuid" != "$archive_uuid" ] || fail scratch_is_archive_volume

observed_archive_uuid=$(volume_field "$archive_mount" VolumeUUID | /usr/bin/tr '[:upper:]' '[:lower:]') || fail archive_volume_identity_unavailable
observed_scratch_uuid=$(volume_field "$scratch_mount" VolumeUUID | /usr/bin/tr '[:upper:]' '[:lower:]') || fail scratch_volume_identity_unavailable
archive_fs=$(volume_field "$archive_mount" FilesystemType) || fail archive_filesystem_unavailable
scratch_fs=$(volume_field "$scratch_mount" FilesystemType) || fail scratch_filesystem_unavailable
scratch_writable=$(volume_field "$scratch_mount" WritableVolume) || fail scratch_writability_unavailable
archive_writable=$(volume_field "$archive_mount" WritableVolume) || fail archive_writability_unavailable
observed_archive_mount=$(volume_field "$archive_mount" MountPoint) || fail archive_mount_identity_unavailable
observed_scratch_mount=$(volume_field "$scratch_mount" MountPoint) || fail scratch_mount_identity_unavailable
[ "$observed_archive_uuid" = "$archive_uuid" ] || fail archive_volume_identity_mismatch
[ "$observed_scratch_uuid" = "$scratch_uuid" ] || fail scratch_volume_identity_mismatch
[ "$observed_archive_mount" = "$archive_mount" ] || fail archive_mount_identity_mismatch
[ "$observed_scratch_mount" = "$scratch_mount" ] || fail scratch_mount_identity_mismatch
case "$archive_fs" in apfs|hfs) ;; *) fail archive_filesystem_not_supported ;; esac
case "$scratch_fs" in apfs|hfs) ;; *) fail scratch_filesystem_not_supported ;; esac
[ "$scratch_writable" = true ] || fail scratch_not_writable
[ "$archive_writable" = false ] || fail archive_not_read_only_by_diskutil
archive_mount_line=$(mount_flags "$archive_mount") || fail archive_mount_flags_unavailable
scratch_mount_line=$(mount_flags "$scratch_mount") || fail scratch_mount_flags_unavailable
if has_mount_flag "$archive_mount_line" read-only || has_mount_flag "$archive_mount_line" rdonly; then :; else fail archive_not_mounted_read_only; fi
if has_mount_flag "$scratch_mount_line" noexec; then fail scratch_is_noexec; fi

archive=$(cd "$(/usr/bin/dirname "$archive")" && /bin/pwd -P)/$(/usr/bin/basename "$archive") || fail archive_path_unavailable
case "$archive" in "$archive_mount"/*) ;; *) fail archive_not_on_pinned_volume ;; esac
[ -f "$archive" ] && [ ! -L "$archive" ] || fail archive_not_regular_file

available_kib=$(/bin/df -kP "$scratch_mount" | /usr/bin/awk 'NR == 2 {print $4}') || fail scratch_capacity_unavailable
case "$available_kib" in ''|*[!0123456789]*) fail scratch_capacity_unavailable ;; esac
required_kib=$(( minimum_free_bytes / 1024 + (minimum_free_bytes % 1024 != 0) ))
[ "$available_kib" -ge "$required_kib" ] || fail insufficient_scratch_capacity

actual_archive_sha=$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/awk '{print $1}') || fail archive_read_failed
[ "$actual_archive_sha" = "$expected_archive_sha" ] || fail archive_digest_mismatch

stage=$(/usr/bin/mktemp -d "$scratch_mount/.brain-dr-stage.XXXXXXXX") || fail stage_directory_create_failed
/bin/chmod 700 "$stage" || fail stage_directory_permissions_failed
cleanup_on_failure() {
  status=$?
  if [ "$status" -ne 0 ]; then
    printf 'brain-dr-stage: unaccepted private stage retained; status=%s\n' "$status" >&2
  fi
}
trap cleanup_on_failure EXIT

/usr/bin/ditto -x -k "$archive" "$stage" || fail archive_extraction_failed
kit="$stage/v6b"
[ -d "$kit" ] && [ ! -L "$kit" ] || fail extracted_kit_root_invalid
[ -f "$kit/MANIFEST.json" ] && [ ! -L "$kit/MANIFEST.json" ] || fail extracted_manifest_missing
actual_manifest_sha=$(/usr/bin/shasum -a 256 "$kit/MANIFEST.json" | /usr/bin/awk '{print $1}') || fail manifest_read_failed
[ "$actual_manifest_sha" = "$expected_manifest_sha" ] || fail manifest_digest_mismatch

python="$kit/runtime/Python.framework/Versions/3.13/bin/python3.13"
[ -f "$python" ] && [ -x "$python" ] && [ ! -L "$python" ] || fail bundled_python_not_regular_executable
actual_python_sha=$(/usr/bin/shasum -a 256 "$python" | /usr/bin/awk '{print $1}') || fail bundled_python_read_failed
[ "$actual_python_sha" = "$expected_python_sha" ] || fail bundled_python_digest_mismatch

home="$stage/operator-home"
tmp="$stage/operator-tmp"
neutral="$stage/neutral-cwd"
/bin/mkdir -m 700 "$home" "$tmp" "$neutral" || fail private_operator_dirs_create_failed

verify_code=$(/bin/cat <<'PY'
import hashlib, json, os, stat, sys, zipfile

root, expected_manifest, expected_archive, expected_source, expected_python = sys.argv[1:]
kit = os.path.join(root, "v6b")
if set(os.listdir(root)) != {"v6b", "operator-home", "operator-tmp", "neutral-cwd"}:
    raise SystemExit("staged_root_member_set_mismatch")

def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

manifest_path = os.path.join(kit, "MANIFEST.json")
if digest(manifest_path) != expected_manifest:
    raise SystemExit("manifest_digest_mismatch")
with open(manifest_path, "rb") as stream:
    manifest = json.load(stream)
if manifest.get("schema") != "brain-portability-offline-execution-kit-manifest-v1":
    raise SystemExit("manifest_schema_mismatch")
if manifest.get("runtime_executable_sha256") != expected_python:
    raise SystemExit("manifest_python_pin_mismatch")
if manifest.get("source_archive_sha256") != expected_source:
    raise SystemExit("source_archive_pin_mismatch")
source_path = os.path.join(kit, "source.zip")
if digest(source_path) != expected_archive or digest(source_path) != expected_source:
    raise SystemExit("source_archive_digest_mismatch")

members = manifest.get("runtime_members")
if not isinstance(members, list) or len(members) != 3688:
    raise SystemExit("runtime_roster_invalid")
expected = {"MANIFEST.json", "source.zip"}
member_paths = set()
for row in members:
    if not isinstance(row, dict) or row.get("kind") not in {"directory", "file", "symlink"}:
        raise SystemExit("runtime_member_invalid")
    rel = row.get("path")
    if not isinstance(rel, str) or rel.startswith("/") or "\\" in rel or any(x in {"", ".", ".."} for x in rel.split("/")):
        raise SystemExit("runtime_path_invalid")
    if not rel.startswith("runtime/") or rel in expected:
        raise SystemExit("runtime_path_invalid")
    if rel in member_paths:
        raise SystemExit("runtime_path_duplicate")
    member_paths.add(rel)
    expected.add(rel)
    parts = rel.split("/")
    for i in range(1, len(parts)):
        expected.add("/".join(parts[:i]))
implicit_dirs = expected - {"MANIFEST.json", "source.zip"} - member_paths

actual = set()
for base, dirs, files in os.walk(kit, topdown=True, followlinks=False):
    relbase = os.path.relpath(base, kit)
    if relbase != ".":
        actual.add(relbase.replace(os.sep, "/"))
    keep = []
    for name in dirs:
        path = os.path.join(base, name)
        if os.path.islink(path):
            actual.add(os.path.relpath(path, kit).replace(os.sep, "/"))
        else:
            keep.append(name)
    dirs[:] = keep
    for name in files:
        actual.add(os.path.relpath(os.path.join(base, name), kit).replace(os.sep, "/"))
if actual != expected:
    raise SystemExit("staged_member_set_mismatch")

for rel in implicit_dirs:
    st = os.lstat(os.path.join(kit, *rel.split("/")))
    if not stat.S_ISDIR(st.st_mode):
        raise SystemExit("staged_implicit_parent_type_mismatch")

rows = {r["path"]: r for r in members}
for rel, row in rows.items():
    path = os.path.join(kit, *rel.split("/"))
    st = os.lstat(path)
    if stat.S_IMODE(st.st_mode) != row.get("mode"):
        raise SystemExit("staged_member_mode_mismatch")
    if row["kind"] == "directory":
        if not stat.S_ISDIR(st.st_mode):
            raise SystemExit("staged_member_type_mismatch")
    elif row["kind"] == "symlink":
        if not stat.S_ISLNK(st.st_mode) or os.readlink(path) != row.get("target"):
            raise SystemExit("staged_member_symlink_mismatch")
    else:
        if not stat.S_ISREG(st.st_mode) or st.st_size != row.get("bytes") or digest(path) != row.get("sha256"):
            raise SystemExit("staged_member_content_mismatch")

source_rows = manifest.get("source_files")
if not isinstance(source_rows, list) or len(source_rows) != 21:
    raise SystemExit("source_roster_invalid")
source_expected = {r.get("path") for r in source_rows if isinstance(r, dict)}
if len(source_expected) != 21 or None in source_expected:
    raise SystemExit("source_roster_invalid")
with zipfile.ZipFile(source_path) as archive:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if {info.filename for info in infos} != source_expected:
        raise SystemExit("source_member_set_mismatch")
    for info in infos:
        row = next(r for r in source_rows if r["path"] == info.filename)
        h = hashlib.sha256()
        total = 0
        with archive.open(info, "r") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                h.update(chunk)
        if total != row.get("bytes") or h.hexdigest() != row.get("sha256"):
            raise SystemExit("source_member_content_mismatch")
print("stage_manifest_and_members=verified")
print("runtime_members=3688 source_members=21")
PY
)

 /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$home" TMPDIR="$tmp" \
  PYTHONHOME="$kit/runtime/Python.framework/Versions/3.13" PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$python" -B -S -c "$verify_code" \
  "$stage" "$expected_manifest_sha" "$expected_source_sha" "$expected_source_sha" "$expected_python_sha" || fail staged_member_validation_failed

cd "$neutral" || fail neutral_cwd_unavailable
 /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$home" TMPDIR="$tmp" \
  PYTHONHOME="$kit/runtime/Python.framework/Versions/3.13" \
  PYTHONPATH="$kit/source.zip:$kit/source.zip/src" PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$python" -B -m tools.brain_host_recovery --help >/dev/null || fail recovery_entrypoint_launch_failed
 /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$home" TMPDIR="$tmp" \
  PYTHONHOME="$kit/runtime/Python.framework/Versions/3.13" \
  PYTHONPATH="$kit/source.zip:$kit/source.zip/src" PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$python" -B -m tools.brain_host_held_data_t_cli_v1 --help >/dev/null || fail held_data_entrypoint_launch_failed
 /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$home" TMPDIR="$tmp" \
  PYTHONHOME="$kit/runtime/Python.framework/Versions/3.13" \
  PYTHONPATH="$kit/source.zip:$kit/source.zip/src" PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$python" -B -m tools.brain_host_recovery_preflight_v1 --help >/dev/null || fail recovery_preflight_launch_failed

 /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME="$home" TMPDIR="$tmp" \
  PYTHONHOME="$kit/runtime/Python.framework/Versions/3.13" PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 "$python" -B -S -c "$verify_code" \
  "$stage" "$expected_manifest_sha" "$expected_source_sha" "$expected_source_sha" "$expected_python_sha" >/dev/null || fail staged_member_postlaunch_revalidation_failed

# Reconfirm the removable transport and scratch identities/flags after execution, and
# prove the extracted stage still belongs to the pinned scratch filesystem.
observed_archive_uuid=$(volume_field "$archive_mount" VolumeUUID | /usr/bin/tr '[:upper:]' '[:lower:]') || fail final_archive_volume_identity_unavailable
observed_scratch_uuid=$(volume_field "$scratch_mount" VolumeUUID | /usr/bin/tr '[:upper:]' '[:lower:]') || fail final_scratch_volume_identity_unavailable
observed_archive_mount=$(volume_field "$archive_mount" MountPoint) || fail final_archive_mount_identity_unavailable
observed_scratch_mount=$(volume_field "$scratch_mount" MountPoint) || fail final_scratch_mount_identity_unavailable
archive_writable=$(volume_field "$archive_mount" WritableVolume) || fail final_archive_writability_unavailable
scratch_writable=$(volume_field "$scratch_mount" WritableVolume) || fail final_scratch_writability_unavailable
[ "$observed_archive_uuid" = "$archive_uuid" ] && [ "$observed_archive_mount" = "$archive_mount" ] || fail final_archive_volume_identity_mismatch
[ "$observed_scratch_uuid" = "$scratch_uuid" ] && [ "$observed_scratch_mount" = "$scratch_mount" ] || fail final_scratch_volume_identity_mismatch
[ "$archive_writable" = false ] || fail final_archive_not_read_only
[ "$scratch_writable" = true ] || fail final_scratch_not_writable
archive_mount_line=$(mount_flags "$archive_mount") || fail final_archive_mount_flags_unavailable
scratch_mount_line=$(mount_flags "$scratch_mount") || fail final_scratch_mount_flags_unavailable
if has_mount_flag "$archive_mount_line" read-only || has_mount_flag "$archive_mount_line" rdonly; then :; else fail final_archive_not_mounted_read_only; fi
if has_mount_flag "$scratch_mount_line" noexec; then fail final_scratch_is_noexec; fi
stage_device=$(/usr/bin/stat -f '%d' "$stage") || fail final_stage_device_unavailable
scratch_device=$(/usr/bin/stat -f '%d' "$scratch_mount") || fail final_scratch_device_unavailable
[ "$stage_device" = "$scratch_device" ] || fail final_stage_device_mismatch
final_available_kib=$(/bin/df -kP "$scratch_mount" | /usr/bin/awk 'NR == 2 {print $4}') || fail final_scratch_capacity_unavailable
case "$final_available_kib" in ''|*[!0123456789]*) fail final_scratch_capacity_unavailable ;; esac
[ "$final_available_kib" -ge "$required_kib" ] || fail final_scratch_reserve_not_preserved

trap - EXIT
printf 'brain-dr-stage: status=complete archive=%s manifest=%s runtime=verified entrypoints=3 inert=true stage_id=%s\n' \
  "$expected_archive_sha" "$expected_manifest_sha" "${stage##*/}"
