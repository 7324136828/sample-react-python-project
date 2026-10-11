#!/usr/bin/env python3
"""Run the FastAPI backend and Vite frontend together."""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from backend.network import lan_urls_for_host

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
LOCAL_PYTHON = ROOT / ".venv" / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)


class RunError(RuntimeError):
    """Raised when the application cannot be started."""


def in_active_environment() -> bool:
    return (
        sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        or bool(os.environ.get("VIRTUAL_ENV"))
        or bool(os.environ.get("CONDA_PREFIX"))
    )


def load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def select_runtime() -> Path:
    if in_active_environment():
        return Path(sys.executable)
    if not LOCAL_PYTHON.is_file():
        print("[run] No active environment or local .venv; running setup...")
        if subprocess.call([sys.executable, str(ROOT / "setup.py")], cwd=ROOT):
            raise RunError("Setup failed")
    if not LOCAL_PYTHON.is_file():
        raise RunError(f"Python environment not found at {LOCAL_PYTHON}")
    return LOCAL_PYTHON


def parse_port(value: str | int, label: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise RunError(f"{label} must be an integer") from error
    if not 1 <= port <= 65535:
        raise RunError(f"{label} must be between 1 and 65535")
    return port


def resolve_host(host: str) -> tuple[int, str]:
    try:
        addresses = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RunError(f"Cannot resolve bind address {host!r}: {error}") from error
    family, _, _, _, address = addresses[0]
    return family, address[0]


def available_port(
    start: int, reserved: set[int] | None = None, *,
    host: str = "127.0.0.1", family: int = socket.AF_INET,
) -> int:
    reserved = reserved or set()
    for port in range(start, 65536):
        if port in reserved:
            continue
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            try:
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind((host, port))
            except OSError as error:
                if error.errno in (errno.EADDRINUSE, errno.EACCES):
                    continue
                raise RunError(f"Cannot bind to {host}:{port}: {error}") from error
        return port
    raise RunError(f"No available TCP port was found at or above {start}")


def service_url(host: str, port: int) -> str:
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def merge_cors_origins(
    environment: dict[str, str], frontend_port: int,
    extra_origins: list[str] | None = None,
) -> None:
    current = environment.get("CORS_ORIGINS", "")
    if current.strip() == "*":
        return
    origins = [item.strip() for item in current.split(",") if item.strip()]
    for origin in (
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
        *(extra_origins or []),
    ):
        if origin not in origins:
            origins.append(origin)
    environment["CORS_ORIGINS"] = ",".join(origins)


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-port", type=int, help="first backend port to try")
    parser.add_argument("--frontend-port", type=int, help="first frontend port to try")
    parser.add_argument(
        "--host", help="bind address for both services (default: BACKEND_HOST or 0.0.0.0)",
    )
    parser.add_argument("--no-reload", action="store_true", help="disable backend reload")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    runtime = select_runtime()
    if Path(sys.executable).resolve() != runtime.resolve():
        return subprocess.call([str(runtime), str(Path(__file__).resolve()), *sys.argv[1:]])

    load_env_file()
    requested_backend = parse_port(
        args.backend_port or os.environ.get("BACKEND_PORT", "8000"),
        "backend port",
    )
    requested_frontend = parse_port(
        args.frontend_port or os.environ.get("FRONTEND_PORT", "5173"),
        "frontend port",
    )
    family, host = resolve_host(args.host or os.environ.get("BACKEND_HOST", "0.0.0.0"))
    backend_port = available_port(requested_backend, host=host, family=family)
    frontend_port = available_port(
        requested_frontend, {backend_port}, host=host, family=family,
    )
    backend_url = service_url(host, backend_port)
    frontend_url = (
        f"http://localhost:{frontend_port}" if host == "0.0.0.0"
        else service_url(host, frontend_port)
    )
    frontend_lan_urls = lan_urls_for_host(host, frontend_port)
    backend_lan_urls = [
        f"{url.rsplit(':', 1)[0]}:{backend_port}" for url in frontend_lan_urls
    ]

    backend_env = os.environ.copy()
    merge_cors_origins(backend_env, frontend_port, [frontend_url, *frontend_lan_urls])
    frontend_env = os.environ.copy()
    frontend_env["VITE_BACKEND_URL"] = backend_url

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise RunError("npm was not found; run setup first")
    if not (FRONTEND / "node_modules").is_dir():
        raise RunError("Frontend dependencies are missing; run setup first")

    if backend_port != requested_backend:
        print(f"[run] Backend port {requested_backend} is busy; using {backend_port}.")
    if frontend_port != requested_frontend:
        print(f"[run] Frontend port {requested_frontend} is busy; using {frontend_port}.")
    print("=" * 62)
    print("Starting Sample React + Python Full-Stack Services")
    print(f"Python:   {sys.executable}")
    print(f"Backend:  {backend_url} (API docs: {backend_url}/docs)")
    print(f"Frontend: {frontend_url}")
    for frontend_lan_url, backend_lan_url in zip(frontend_lan_urls, backend_lan_urls):
        print(f"LAN:      {frontend_lan_url}")
        print(f"LAN API:  {backend_lan_url}/docs")
    if frontend_lan_urls:
        print("Open a LAN URL on another device connected to the same network.")
    print("Press Ctrl+C to stop both services.")
    print("=" * 62, flush=True)

    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        host,
        "--port",
        str(backend_port),
    ]
    if not args.no_reload:
        backend_command.append("--reload")
    frontend_command = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        host,
        "--port",
        str(frontend_port),
        "--strictPort",
    ]

    backend_process: subprocess.Popen[bytes] | None = None
    frontend_process: subprocess.Popen[bytes] | None = None
    try:
        backend_process = subprocess.Popen(backend_command, cwd=ROOT, env=backend_env)
        frontend_process = subprocess.Popen(
            frontend_command, cwd=FRONTEND, env=frontend_env
        )
        while True:
            for label, process in (
                ("Backend", backend_process),
                ("Frontend", frontend_process),
            ):
                status = process.poll()
                if status is not None:
                    print(f"[run] {label} exited with code {status}.")
                    return status
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[run] Stopping services...")
        return 0
    finally:
        stop(frontend_process)
        stop(backend_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunError as error:
        print(f"[run] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
