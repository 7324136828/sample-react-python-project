#!/usr/bin/env python3
"""Build a portable Windows application in dist without replacing user data."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
OUTPUT = ROOT / "dist"
WORK = ROOT / "build"
LOCAL_VENV = ROOT / ".venv"
LOCAL_PYTHON = LOCAL_VENV / "Scripts" / "python.exe"
APPLICATION = "server"
ARTIFACTS = ("server.exe", "public", "run.bat")

LAUNCHER = """@echo off
setlocal
if not exist "%~dp0server.exe" (
  echo [run] ERROR: Backend executable is missing. Rebuild with build.bat. 1>&2
  exit /b 1
)
"%~dp0server.exe" %*
exit /b %ERRORLEVEL%
"""


class BuildError(RuntimeError):
    """Raised when a portable build cannot be completed."""


def checked_path(path: Path, parent: Path) -> Path:
    """Reject symlinks/junctions that resolve outside the intended directory."""
    resolved = path.resolve()
    allowed = parent.resolve()
    if resolved == allowed or not resolved.is_relative_to(allowed):
        raise BuildError(f"Unsafe build path outside {allowed}: {path}")
    return resolved


def run(
    command: list[str | Path], *, cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    rendered = [str(part) for part in command]
    print(f"[build] $ {subprocess.list2cmdline(rendered)}", flush=True)
    completed = subprocess.run(rendered, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise BuildError(f"Command failed with exit code {completed.returncode}")


def select_python() -> Path:
    if (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or os.environ.get("VIRTUAL_ENV")
        or os.environ.get("CONDA_PREFIX")
    ):
        print(f"[build] Using active Python environment: {sys.prefix}")
        return Path(sys.executable)
    if not LOCAL_PYTHON.is_file():
        checked_path(LOCAL_VENV, ROOT)
        print(f"[build] Creating Python environment at {LOCAL_VENV}")
        venv.EnvBuilder(with_pip=True).create(LOCAL_VENV)
    if not LOCAL_PYTHON.is_file():
        raise BuildError(f"Python environment has no interpreter: {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def pyinstaller_command(python: Path, stage: Path) -> list[str | Path]:
    command: list[str | Path] = [
        python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--noupx",
        "--name", APPLICATION,
        "--distpath", stage / "frozen",
        "--workpath", stage / "pyinstaller",
        "--specpath", stage,
        "--paths", ROOT,
    ]
    # Uvicorn loads these configured implementations by their string names.
    for module in (
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl",
    ):
        command.extend(["--hidden-import", module])
    command.append(BACKEND / "packaged.py")
    return command


def validate_artifacts(stage: Path) -> None:
    if not (stage / "server.exe").is_file():
        raise BuildError("PyInstaller did not produce the backend executable")
    if not (stage / "public" / "index.html").is_file():
        raise BuildError("Vite did not produce public/index.html")
    batch_files = [path.relative_to(stage) for path in stage.rglob("*")
                   if path.is_file() and path.suffix.lower() == ".bat"]
    if batch_files != [Path("run.bat")]:
        raise BuildError(f"Unexpected batch files in release artifacts: {batch_files}")


def publish_artifacts(stage: Path, *, repository: Path = ROOT) -> None:
    """Swap only generated artifacts, restoring the previous release on failure."""
    output = repository / "dist"
    checked_path(output, repository)
    checked_path(stage, repository / "build")
    output.mkdir(exist_ok=True)
    previous = stage / "previous"
    checked_path(previous, stage)
    previous.mkdir()
    installed: list[str] = []
    backed_up: list[str] = []
    # Check every mutation target before moving anything. In particular, a
    # junction used for one output must not leave another output half updated.
    for name in ARTIFACTS:
        checked_path(stage / name, stage)
        checked_path(output / name, output)
        checked_path(previous / name, previous)
    try:
        for name in ARTIFACTS:
            source = stage / name
            target = output / name
            backup = previous / name
            checked_path(source, stage)
            checked_path(target, output)
            checked_path(backup, previous)
            if target.exists():
                target.rename(backup)
                backed_up.append(name)
            source.rename(target)
            installed.append(name)
    except OSError as error:
        # Keep the last usable release if a running executable or another locked
        # artifact prevents replacing it. User state under dist is never moved.
        for name in reversed(installed):
            target = output / name
            checked_path(target, output)
            target.rename(stage / name)
        for name in reversed(backed_up):
            backup = previous / name
            checked_path(backup, previous)
            backup.rename(output / name)
        raise BuildError(
            "Could not replace release artifacts; close the running application "
            f"and retry. {error}"
        ) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-install", action="store_true",
        help="Reuse installed Python/npm dependencies instead of installing them",
    )
    options = parser.parse_args(argv)
    if os.name != "nt":
        raise BuildError("Build the Windows executable on Windows")
    if sys.version_info < (3, 10):
        raise BuildError("Python 3.10 or newer is required")
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise BuildError("Node.js and npm were not found on PATH")
    if not (BACKEND / "packaged.py").is_file():
        raise BuildError("Backend packaged entrypoint is missing")
    checked_path(WORK, ROOT)
    WORK.mkdir(exist_ok=True)
    python = select_python()
    if not options.skip_install:
        run([python, "-m", "pip", "install", "--cache-dir", WORK / "pip-cache",
             "-r", BACKEND / "requirements.txt",
             "-r", BACKEND / "requirements-build.txt"])
        run([npm, "ci", "--cache", WORK / "npm-cache"], cwd=FRONTEND)
    run([npm, "run", "typecheck"], cwd=FRONTEND)

    stage = WORK / ("portable-" + uuid.uuid4().hex)
    checked_path(stage, WORK)
    stage.mkdir()
    published = False
    try:
        run([npm, "run", "build", "--", "--outDir", stage / "public"], cwd=FRONTEND)
        freeze_env = os.environ.copy()
        freeze_env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "pyinstaller-cache")
        checked_path(WORK / "pyinstaller-cache", WORK)
        run(pyinstaller_command(python, stage), env=freeze_env)
        frozen = stage / "frozen" / f"{APPLICATION}.exe"
        checked_path(frozen, stage)
        frozen.rename(stage / "server.exe")
        (stage / "run.bat").write_text(LAUNCHER, encoding="ascii", newline="\r\n")
        # Validate the release folders rather than PyInstaller's work/spec files.
        artifacts = stage / "release"
        checked_path(artifacts, stage)
        artifacts.mkdir()
        for name in ARTIFACTS:
            (stage / name).rename(artifacts / name)
        validate_artifacts(artifacts)
        run([artifacts / "server.exe", "--help"])
        publish_artifacts(artifacts)
        published = True
    finally:
        if published:
            checked_path(stage, WORK)
            shutil.rmtree(stage)
        else:
            print(f"[build] Build files retained for troubleshooting: {stage}")

    print(f"\n[build] Ready: {OUTPUT}")
    print("[build] Run: dist\\run.bat --frontend 4000 --backend 9000")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError) as error:
        print(f"\n[build] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
