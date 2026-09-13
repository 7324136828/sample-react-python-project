#!/usr/bin/env python3
"""Set up the Python backend, React frontend, and end-to-end tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_REQUIREMENTS = ROOT / "backend" / "requirements.txt"
FRONTEND = ROOT / "frontend"
E2E = ROOT / "e2e"
LOCAL_VENV = ROOT / ".venv"
LOCAL_PYTHON = LOCAL_VENV / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)


class SetupError(RuntimeError):
    """Raised when setup cannot be completed."""


def in_active_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )


def run(command: list[str | Path], *, cwd: Path = ROOT) -> None:
    rendered = [str(part) for part in command]
    print(f"[setup] $ {' '.join(rendered)}", flush=True)
    completed = subprocess.run(rendered, cwd=cwd, check=False)
    if completed.returncode:
        raise SetupError(
            f"Command failed with exit code {completed.returncode}: "
            f"{' '.join(rendered)}"
        )


def select_python() -> Path:
    if in_active_environment():
        print(f"[setup] Using active Python environment: {sys.prefix}")
        return Path(sys.executable)

    if not LOCAL_PYTHON.is_file():
        print(f"[setup] Creating virtual environment at {LOCAL_VENV}")
        venv.EnvBuilder(with_pip=True).create(LOCAL_VENV)
    else:
        print(f"[setup] Reusing virtual environment at {LOCAL_VENV}")
    if not LOCAL_PYTHON.is_file():
        raise SetupError(f"Virtual environment has no interpreter at {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def seed_environment_file() -> None:
    example = ROOT / ".env.example"
    target = ROOT / ".env"
    if example.is_file() and not target.exists():
        shutil.copy2(example, target)
        print("[setup] Created .env from .env.example")


def main() -> int:
    if sys.version_info < (3, 10):
        raise SetupError("Python 3.10 or newer is required")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise SetupError("Node.js and npm were not found on PATH")
    if not BACKEND_REQUIREMENTS.is_file():
        raise SetupError(f"Requirements file not found: {BACKEND_REQUIREMENTS}")
    if not (FRONTEND / "package.json").is_file():
        raise SetupError(f"React project not found: {FRONTEND}")
    if not (E2E / "package.json").is_file():
        raise SetupError(f"End-to-end test project not found: {E2E}")

    python = select_python()
    print(f"[setup] Installing backend dependencies with {python}")
    run([python, "-m", "pip", "install", "-r", BACKEND_REQUIREMENTS])
    print("[setup] Installing frontend dependencies")
    run([npm, "install"], cwd=FRONTEND)
    print("[setup] Installing end-to-end test dependencies")
    run([npm, "install"], cwd=E2E)
    seed_environment_file()

    print("\n[setup] Ready. Start the application with run.bat or ./run.sh")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as error:
        print(f"\n[setup] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
