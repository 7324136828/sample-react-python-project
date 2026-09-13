#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]; then
    PYTHON_BIN="$(command -v python || command -v python3)"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python was not found on PATH. Install Python 3.10 or newer."
    exit 1
fi

exec "$PYTHON_BIN" setup.py "$@"
