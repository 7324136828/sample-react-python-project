"""Run the portable backend and built frontend from one executable."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


class RunError(RuntimeError):
    """Raised when the portable application cannot start or keep running."""


def runtime_directory() -> Path:
    # A one-file executable extracts its Python modules to a temporary directory.
    # Public files and user state belong beside server.exe, outside that directory.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1] / "dist"


def load_environment(root: Path) -> None:
    env_file = root / ".env"
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    database = Path(os.environ.get("COUNTER_DB_PATH", "data/counter.db")).expanduser()
    if not database.is_absolute():
        database = root / database
    # Set this before importing the backend: its module creates the app on import.
    os.environ["COUNTER_DB_PATH"] = str(database.resolve())


def runtime_host(root: Path) -> str:
    profile_path = root / "runtime.json"
    if not profile_path.exists():
        return "0.0.0.0"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RunError(f"Cannot read runtime profile at {profile_path}: {error}") from error
    if not isinstance(profile, dict) or profile.get("host") not in ("127.0.0.1", "0.0.0.0"):
        raise RunError(
            f"Invalid runtime profile at {profile_path}: host must be 127.0.0.1 or 0.0.0.0"
        )
    return profile["host"]


def port_number(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def parse_args(
    argv: list[str] | None = None, *, default_host: str = "0.0.0.0",
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frontend", "--frontend-port", dest="frontend_port", type=port_number,
        default=os.environ.get("FRONTEND_PORT", "3000"),
        help="frontend port (default: FRONTEND_PORT or 3000)",
    )
    parser.add_argument(
        "--backend", "--backend-port", dest="backend_port", type=port_number,
        default=os.environ.get("BACKEND_PORT", "8000"),
        help="backend port (default: BACKEND_PORT or 8000)",
    )
    parser.add_argument(
        "--host", default=os.environ.get("BACKEND_HOST", default_host),
        help="bind address for both services (default: BACKEND_HOST or runtime profile)",
    )
    options = parser.parse_args(argv)
    if options.frontend_port == options.backend_port:
        parser.error("frontend and backend ports must be different")
    return options


def bind_listener(host: str, port: int, label: str) -> socket.socket:
    """Reserve the requested port before starting either application."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RunError(f"Cannot resolve bind address {host!r}: {error}") from error
    last_error: OSError | None = None
    for family, kind, protocol, _, address in addresses:
        listener = socket.socket(family, kind, protocol)
        try:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(address)
            listener.listen(socket.SOMAXCONN)
            listener.setblocking(False)
            return listener
        except OSError as error:
            listener.close()
            last_error = error
    raise RunError(
        f"Cannot bind {label} to {host}:{port}; choose another port or close "
        f"the service using it. {last_error}"
    ) from last_error


def listener_url(listener: socket.socket) -> str:
    host, port = listener.getsockname()[:2]
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def forwarded_headers(
    headers: httpx.Headers | Headers, *, response: bool = False,
) -> list[tuple[bytes, bytes]]:
    excluded = HOP_BY_HOP_HEADERS | {
        item.strip().lower() for item in headers.get("connection", "").split(",")
    }
    # HTTPX decodes response bodies; let Response compute their new length.
    excluded |= {"host", "content-length"}
    if response:
        excluded.add("content-encoding")
    return [(key, value) for key, value in headers.raw
            if key.decode("latin-1").lower() not in excluded]


def create_frontend_app(public: Path, backend_url: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # Local traffic must not depend on the target machine's HTTP proxy setup.
        async with httpx.AsyncClient(
            base_url=backend_url, trust_env=False, timeout=30.0,
        ) as client:
            application.state.backend_client = client
            yield

    application = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
    )

    async def proxy(request: Request) -> Response:
        path = request.url.path
        if request.url.query:
            path += "?" + request.url.query
        try:
            upstream = await request.app.state.backend_client.request(
                request.method, path, content=await request.body(),
                headers=forwarded_headers(request.headers),
            )
        except httpx.RequestError:
            return JSONResponse({"detail": "Backend is unavailable"}, status_code=502)
        response = Response(content=upstream.content, status_code=upstream.status_code)
        response.raw_headers = forwarded_headers(upstream.headers, response=True) + [
            (b"content-length", str(len(upstream.content)).encode("ascii")),
        ]
        return response

    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    application.add_api_route("/api", proxy, methods=methods)
    application.add_api_route("/api/{path:path}", proxy, methods=methods)
    application.mount("/", StaticFiles(directory=public, html=True), name="public")
    return application


class ManagedServer(uvicorn.Server):
    """Two listeners share one process-level signal handler."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    def install_signal_handlers(self) -> None:
        # Compatibility with Uvicorn releases before capture_signals was added.
        pass


async def serve_applications(
    backend: FastAPI, frontend: FastAPI,
    backend_socket: socket.socket, frontend_socket: socket.socket,
) -> int:
    servers = [
        ManagedServer(uvicorn.Config(
            application, loop="asyncio", http="h11", ws="none", lifespan="on",
            access_log=False, use_colors=False, timeout_graceful_shutdown=5,
        ))
        for application in (backend, frontend)
    ]
    stopping = False

    def stop_services(signum: int, frame: object) -> None:
        nonlocal stopping
        for server in servers:
            server.should_exit = True
            if stopping:
                server.force_exit = True
        if not stopping:
            print("\n[run] Stopping both services...", flush=True)
        stopping = True

    async def serve(server: ManagedServer, listener: socket.socket, label: str) -> None:
        try:
            await server.serve(sockets=[listener])
        except SystemExit as error:
            # Keep startup failure inside this task so its sibling can shut down.
            raise RunError(f"{label} failed to start (exit code {error.code})") from error

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous = {item: signal.signal(item, stop_services) for item in signals}
    tasks = [
        asyncio.create_task(serve(server, listener, label))
        for server, listener, label in zip(
            servers, (backend_socket, frontend_socket), ("Backend", "Frontend"),
        )
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for server in servers:
            server.should_exit = True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for item, handler in previous.items():
            signal.signal(item, handler)
    for label, result in zip(("Backend", "Frontend"), results):
        if isinstance(result, BaseException):
            raise RunError(f"{label} failed: {result}") from result
    if not stopping:
        raise RunError("A service stopped unexpectedly; both services have been stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    root = runtime_directory()
    load_environment(root)
    options = parse_args(argv, default_host=runtime_host(root))
    public = root / "public"
    if not (public / "index.html").is_file():
        raise RunError(f"Frontend is missing at {public}; rebuild with build.bat")

    backend_socket = bind_listener(options.host, options.backend_port, "backend")
    frontend_socket: socket.socket | None = None
    try:
        frontend_socket = bind_listener(options.host, options.frontend_port, "frontend")
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from backend.app.main import app
        from backend.network import lan_urls

        backend_url = listener_url(backend_socket)
        frontend_url = listener_url(frontend_socket)
        frontend = create_frontend_app(public, backend_url)
        print("Starting Sample React + Python Full-Stack Services")
        print(f"Python:   {sys.executable}")
        print(f"Backend:  {backend_url} (API docs: {backend_url}/docs)")
        print(f"Frontend: {frontend_url}")
        backend_lan_urls = {urlsplit(url).hostname: url for url in lan_urls(backend_socket)}
        frontend_lan_urls = lan_urls(frontend_socket)
        for url in frontend_lan_urls:
            print(f"LAN:      {url}")
            api_url = backend_lan_urls.get(urlsplit(url).hostname)
            if api_url is not None:
                print(f"LAN API:  {api_url}/docs")
        if frontend_lan_urls:
            print("Open a LAN URL on another device connected to the same network.")
        print(f"Database: {os.environ['COUNTER_DB_PATH']}")
        print("[run] Press Ctrl+C to stop both services.", flush=True)
        return asyncio.run(serve_applications(app, frontend, backend_socket, frontend_socket))
    finally:
        if frontend_socket is not None:
            frontend_socket.close()
        backend_socket.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except (RunError, OSError) as error:
        print(f"[run] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
