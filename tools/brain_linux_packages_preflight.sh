#!/bin/sh
# Read-only entry check for the Python package provisioner.
set -eu

missing=""
add_missing() {
    if [ -n "$missing" ]; then
        missing="$missing,$1"
    else
        missing="$1"
    fi
}

if [ ! -r /etc/os-release ]; then
    printf '%s\n' 'bootstrap_prerequisite_missing:os_release' >&2
    exit 78
fi
os_id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
if [ "$os_id" != ubuntu ] || [ "$os_version" != 24.04 ] || [ "$(uname -m)" != x86_64 ]; then
    printf '%s\n' 'bootstrap_prerequisite_missing:ubuntu_24_04_x86_64' >&2
    exit 78
fi

for pair in \
    '/usr/bin/python3.12:python3.12' \
    '/usr/bin/apt-get:apt_get' \
    '/usr/bin/dpkg:dpkg' \
    '/usr/bin/systemd-detect-virt:systemd_detect_virt'
do
    path=${pair%%:*}
    name=${pair#*:}
    if [ ! -x "$path" ]; then
        add_missing "$name"
    fi
done

if [ -x /usr/bin/python3.12 ]; then
    python_version=$(/usr/bin/python3.12 -c 'import sys; print("%s.%s" % sys.version_info[:2])') || python_version=unknown
    if [ "$python_version" != 3.12 ]; then
        add_missing python3.12_runtime
    fi
fi
if [ ! -r /usr/share/keyrings/ubuntu-archive-keyring.gpg ]; then
    add_missing ubuntu_archive_keyring
fi

if [ ! -r /proc/1/comm ] || [ "$(cat /proc/1/comm)" != systemd ] || [ ! -d /run/systemd/system ]; then
    add_missing systemd_pid1
fi
if [ ! -r /run/systemd/container ] || [ "$(cat /run/systemd/container)" != systemd-nspawn ]; then
    add_missing systemd_nspawn
fi
if [ -x /usr/bin/systemd-detect-virt ] && [ "$(/usr/bin/systemd-detect-virt --container 2>/dev/null || true)" != systemd-nspawn ]; then
    add_missing systemd_nspawn
fi

if [ -n "$missing" ]; then
    printf 'bootstrap_prerequisite_missing:%s\n' "$missing" >&2
    exit 78
fi
printf '%s\n' 'bootstrap_prerequisites_available'
