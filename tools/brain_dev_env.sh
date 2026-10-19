# Source from the research checkout root with: source tools/brain_dev_env.sh
# This changes only the current shell. It activates the existing .venv and selects
# the already installed, pinned OpenSSL and Node binaries without installing anything.

if [[ ! -f pyproject.toml || ! -d src/memecoin_ai || ! -x .venv/bin/python ]]; then
  printf '%s\n' 'brain_dev_env: source this file from the research checkout after make setup.' >&2
  return 1
fi

BRAIN_DEV_OPENSSL_BIN=${BRAIN_DEV_OPENSSL_BIN:-/opt/homebrew/opt/openssl@3/bin/openssl}
BRAIN_DEV_NODE_BIN=${BRAIN_DEV_NODE_BIN:-${HOME}/Developer/tools/node-v24.5.0-darwin-arm64/bin/node}
BRAIN_DEV_ZSTD_BIN=${BRAIN_DEV_ZSTD_BIN:-/opt/homebrew/bin/zstd}
if [[ -n ${BRAIN_DEV_OPENSSL_CONF:-} ]]; then
  _brain_dev_openssl_conf=$BRAIN_DEV_OPENSSL_CONF
elif [[ -n ${OPENSSL_CONF:-} ]]; then
  _brain_dev_openssl_conf=$OPENSSL_CONF
elif [[ -f /opt/homebrew/etc/openssl@3/openssl.cnf ]]; then
  _brain_dev_openssl_conf=/opt/homebrew/etc/openssl@3/openssl.cnf
else
  _brain_dev_openssl_conf=/opt/homebrew/opt/openssl@3/.bottle/etc/openssl@3/openssl.cnf
fi

if [[ ! -x "$BRAIN_DEV_OPENSSL_BIN" ]]; then
  printf 'brain_dev_env: pinned OpenSSL is unavailable: %s\n' "$BRAIN_DEV_OPENSSL_BIN" >&2
  return 1
fi
if [[ ! -x "$BRAIN_DEV_NODE_BIN" ]]; then
  printf 'brain_dev_env: pinned Node is unavailable: %s\n' "$BRAIN_DEV_NODE_BIN" >&2
  return 1
fi
if [[ ! -x "$BRAIN_DEV_ZSTD_BIN" ]]; then
  printf 'brain_dev_env: pinned zstd is unavailable: %s\n' "$BRAIN_DEV_ZSTD_BIN" >&2
  return 1
fi

_brain_dev_openssl_version=$("$BRAIN_DEV_OPENSSL_BIN" version 2>/dev/null) || {
  printf '%s\n' 'brain_dev_env: could not read OpenSSL version.' >&2
  return 1
}
case "$_brain_dev_openssl_version" in
  'OpenSSL 3.6.4 '*) ;;
  *)
    printf 'brain_dev_env: expected OpenSSL 3.6.4, found: %s\n' "$_brain_dev_openssl_version" >&2
    return 1
    ;;
esac
if [[ ! -f "$_brain_dev_openssl_conf" ]]; then
  printf 'brain_dev_env: OpenSSL config is unavailable: %s\n' "$_brain_dev_openssl_conf" >&2
  return 1
fi

_brain_dev_node_version=$("$BRAIN_DEV_NODE_BIN" --version 2>/dev/null) || {
  printf '%s\n' 'brain_dev_env: could not read Node version.' >&2
  return 1
}
if [[ "$_brain_dev_node_version" != v24.5.0 ]]; then
  printf 'brain_dev_env: expected Node v24.5.0, found: %s\n' "$_brain_dev_node_version" >&2
  return 1
fi

_brain_dev_zstd_version=$("$BRAIN_DEV_ZSTD_BIN" --version 2>/dev/null) || {
  printf '%s\n' 'brain_dev_env: could not read zstd version.' >&2
  return 1
}
case "$_brain_dev_zstd_version" in
  '*** Zstandard CLI (64-bit) v1.5.7'*) ;;
  *)
    printf 'brain_dev_env: expected zstd 1.5.7, found: %s\n' "$_brain_dev_zstd_version" >&2
    return 1
    ;;
esac

source .venv/bin/activate || return 1
OPENSSL_CONF=$_brain_dev_openssl_conf
export OPENSSL_CONF

_brain_dev_openssl_dir=${BRAIN_DEV_OPENSSL_BIN%/*}
_brain_dev_node_dir=${BRAIN_DEV_NODE_BIN%/*}
_brain_dev_zstd_dir=${BRAIN_DEV_ZSTD_BIN%/*}
case ":$PATH:" in
  *":$_brain_dev_openssl_dir:"*) ;;
  *) PATH="$_brain_dev_openssl_dir${PATH:+:$PATH}" ;;
esac
case ":$PATH:" in
  *":$_brain_dev_zstd_dir:"*) ;;
  *) PATH="$_brain_dev_zstd_dir${PATH:+:$PATH}" ;;
esac
case ":$PATH:" in
  *":$_brain_dev_node_dir:"*) ;;
  *) PATH="$_brain_dev_node_dir${PATH:+:$PATH}" ;;
esac
export PATH

_brain_dev_python_version=$(python --version 2>&1) || return 1
case "$_brain_dev_python_version" in
  'Python 3.13.6') ;;
  *)
    printf 'brain_dev_env: expected Python 3.13.6, found: %s\n' "$_brain_dev_python_version" >&2
    return 1
    ;;
esac

printf 'Activated research .venv; %s; OpenSSL 3.6.4; Node v24.5.0; zstd 1.5.7.\n' \
  "$_brain_dev_python_version"
unset _brain_dev_openssl_version _brain_dev_node_version _brain_dev_zstd_version
unset _brain_dev_openssl_conf
unset _brain_dev_openssl_dir _brain_dev_node_dir _brain_dev_zstd_dir _brain_dev_python_version
