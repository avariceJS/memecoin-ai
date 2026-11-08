#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WORKSPACE_ROOT=$(dirname -- "$SCRIPT_DIR")
PYTHONHOME="$SCRIPT_DIR/Python.framework/Versions/3.13"
export PYTHONHOME
export DYLD_FRAMEWORK_PATH="$SCRIPT_DIR"
export DYLD_LIBRARY_PATH="$SCRIPT_DIR/lib"
export PYTHONPATH="$WORKSPACE_ROOT/src:$SCRIPT_DIR/site-packages"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT || true
exec "$PYTHONHOME/bin/python3.13" -S "$@"
