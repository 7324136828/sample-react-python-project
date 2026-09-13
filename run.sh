#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]; then
    PYTHON_BIN="$(command -v python || command -v python3)"
else
    if [ ! -x ".venv/bin/python" ]; then
        echo "[run] No active environment or local .venv. Running setup..."
        ./setup.sh
    fi
    PYTHON_BIN=".venv/bin/python"
fi

exec "$PYTHON_BIN" run.py "$@"
