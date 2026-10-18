#!/bin/bash
# Copy into a verified worker kit beside BASE_COMMIT, SHA256SUMS and project.bundle.
set -euo pipefail
kit_dir="$(cd "$(dirname "$0")" && pwd)"
worker_checkout="${MEMECOIN_WORKER_CHECKOUT:-$HOME/Developer/memecoin-worker}"
worker_node="${MEMECOIN_WORKER_NODE_ID:-mac2}"

command -v git >/dev/null
command -v python3 >/dev/null
command -v shasum >/dev/null
if [ -e "$worker_checkout" ] || [ -L "$worker_checkout" ]; then
    echo "Refusing existing checkout: $worker_checkout" >&2
    exit 2
fi
cd "$kit_dir"
shasum -a 256 -c SHA256SUMS
expected_commit="$(cat BASE_COMMIT)"
if ! [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid BASE_COMMIT" >&2
    exit 2
fi
mkdir -p "$(dirname "$worker_checkout")"
git clone --branch main --single-branch "$kit_dir/project.bundle" "$worker_checkout"
actual_commit="$(git -C "$worker_checkout" rev-parse HEAD)"
if [ "$actual_commit" != "$expected_commit" ]; then
    echo "Commit mismatch; checkout preserved for inspection, no code executed." >&2
    exit 2
fi
git -C "$worker_checkout" remote set-url origin https://github.com/avariceJS/memecoin-ai.git
git -C "$worker_checkout" switch -c worker/mac2-bootstrap
worker_receipt="$kit_dir/worker-preflight-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
python3 "$worker_checkout/tools/worker_preflight.py" \
    --repo "$worker_checkout" --node-id "$worker_node" --output "$worker_receipt"
echo "Worker checkout: $worker_checkout"
echo "Read-only receipt: $worker_receipt"
echo "No dependencies, models, providers or services started. See docs/two-mac-workflow.md."
