from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from backend import packaged
from backend.network import discover_lan_ipv4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend import packaged
packaged.runtime_directory = lambda: Path(sys.argv[2])
try:
    raise SystemExit(packaged.main(sys.argv[3:]))
except (packaged.RunError, OSError) as error:
    print(f'[run] ERROR: {error}', file=sys.stderr)
    raise SystemExit(1)
"""


def free_ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


class PackagedRuntimeTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        for name in ("BACKEND_PORT", "FRONTEND_PORT", "BACKEND_HOST", "COUNTER_DB_PATH"):
            environment.pop(name, None)
        return environment

    def command(self, root: Path, *arguments: str) -> list[str]:
        return [sys.executable, "-c", ENTRYPOINT, str(PROJECT_ROOT), str(root), *arguments]

    @contextmanager
    def running(self, root: Path, frontend: int, backend: int):
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen(
                self.command(root, "--frontend", str(frontend), "--backend-port", str(backend)),
                cwd=root, env=self.environment(), stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(trust_env=False, timeout=1) as client:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read().decode("utf-8", errors="replace"))
                        try:
                            response = client.get(f"http://127.0.0.1:{frontend}/api/health")
                            if response.status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail("Portable frontend/API did not become ready")
                    yield client
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    def make_public(self, root: Path) -> None:
        (root / "public").mkdir()
        (root / "public" / "index.html").write_text("<h1>Portable frontend</h1>")
        (root / ".env").write_text("COUNTER_DB_PATH=data/portable.db\n")

    def test_default_lan_and_explicit_bind_configuration(self) -> None:
        with patch.dict(os.environ, self.environment(), clear=True):
            self.assertEqual(packaged.parse_args([]).host, "0.0.0.0")
            self.assertEqual(packaged.parse_args([], default_host="127.0.0.1").host, "127.0.0.1")
            self.assertEqual(packaged.parse_args(["--host", "127.0.0.1"]).host, "127.0.0.1")
            with patch.dict(os.environ, {"BACKEND_HOST": "192.168.1.156"}):
                self.assertEqual(packaged.parse_args([]).host, "192.168.1.156")
                self.assertEqual(
                    packaged.parse_args([], default_host="127.0.0.1").host, "192.168.1.156",
                )
                self.assertEqual(packaged.parse_args(["--host", "127.0.0.1"]).host, "127.0.0.1")

    def test_runtime_profile_defaults_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "runtime.json"
            self.assertEqual(packaged.runtime_host(root), "0.0.0.0")
            for host in ("127.0.0.1", "0.0.0.0"):
                profile.write_text(json.dumps({"host": host}), encoding="utf-8")
                self.assertEqual(packaged.runtime_host(root), host)
            for content in (
                "{", "[]", "{}", '{"host": "localhost"}',
                '{"host": null}', '{"host": ["127.0.0.1"]}',
            ):
                with self.subTest(content=content):
                    profile.write_text(content, encoding="utf-8")
                    with self.assertRaises(packaged.RunError) as raised:
                        packaged.runtime_host(root)
                    self.assertIn(str(profile), str(raised.exception))
                    self.assertIn("runtime profile", str(raised.exception))
            profile.unlink()
            profile.mkdir()
            with self.assertRaisesRegex(packaged.RunError, "Cannot read runtime profile"):
                packaged.runtime_host(root)

    def test_frozen_runtime_profile_follows_executable_after_folder_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Debug"
            root.mkdir()
            (root / "runtime.json").write_text('{"host": "127.0.0.1"}', encoding="utf-8")
            for name in ("Debug", "RenamedPortable"):
                with self.subTest(folder=name):
                    if root.name != name:
                        root = root.rename(root.with_name(name))
                    with (
                        patch.object(packaged.sys, "frozen", True, create=True),
                        patch.object(packaged.sys, "executable", str(root / "server.exe")),
                    ):
                        self.assertEqual(packaged.runtime_directory(), root.resolve())
                        self.assertEqual(packaged.runtime_host(packaged.runtime_directory()), "127.0.0.1")

    def test_profile_environment_file_process_environment_and_cli_precedence(self) -> None:
        for profile_host in ("127.0.0.1", "0.0.0.0"):
            for file_host, process_host, arguments, expected in (
                (None, None, [], profile_host),
                ("192.168.1.156", None, [], "192.168.1.156"),
                ("192.168.1.156", "192.168.1.157", [], "192.168.1.157"),
                ("192.168.1.156", "192.168.1.157", ["--host", "127.0.0.1"], "127.0.0.1"),
            ):
                with self.subTest(profile=profile_host, file=file_host, process=process_host, cli=arguments):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        self.make_public(root)
                        (root / "runtime.json").write_text(json.dumps({"host": profile_host}), encoding="utf-8")
                        if file_host is not None:
                            (root / ".env").write_text(f"BACKEND_HOST={file_host}\n", encoding="utf-8")
                        environment = self.environment()
                        if process_host is not None:
                            environment["BACKEND_HOST"] = process_host
                        with (
                            patch.dict(os.environ, environment, clear=True),
                            patch.object(packaged, "runtime_directory", return_value=root),
                            patch.object(packaged, "bind_listener", side_effect=packaged.RunError("binding checked")) as bind,
                            self.assertRaisesRegex(packaged.RunError, "binding checked"),
                        ):
                            packaged.main(arguments)
                        bind.assert_called_once_with(expected, 8000, "backend")

    def test_startup_links_follow_binding_and_proxy_uses_local_backend(self) -> None:
        addresses = ["192.168.1.156", "192.168.56.1"]
        for host, arguments, expected_addresses in (
            ("0.0.0.0", [], addresses),
            ("127.0.0.1", ["--host", "127.0.0.1"], []),
            ("192.168.1.156", ["--host", "192.168.1.156"], addresses[:1]),
        ):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.make_public(root)
                backend_socket = Mock(spec=socket.socket, family=socket.AF_INET)
                frontend_socket = Mock(spec=socket.socket, family=socket.AF_INET)
                backend_socket.getsockname.return_value = (host, 8000)
                frontend_socket.getsockname.return_value = (host, 3000)
                output = io.StringIO()
                with (
                    patch.dict(os.environ, self.environment(), clear=True),
                    patch.object(packaged, "runtime_directory", return_value=root),
                    patch.object(packaged, "bind_listener", side_effect=[backend_socket, frontend_socket]) as bind,
                    patch("backend.network.discover_lan_ipv4", return_value=addresses),
                    patch.object(packaged, "create_frontend_app") as frontend_app,
                    patch.object(packaged, "serve_applications", new=Mock(return_value=object())),
                    patch.object(packaged.asyncio, "run", return_value=0),
                    redirect_stdout(output),
                ):
                    self.assertEqual(packaged.main(arguments), 0)
                self.assertEqual(bind.call_args_list[0].args, (host, 8000, "backend"))
                self.assertEqual(bind.call_args_list[1].args, (host, 3000, "frontend"))
                local_host = "127.0.0.1" if host == "0.0.0.0" else host
                frontend_app.assert_called_once_with(root / "public", f"http://{local_host}:8000")
                lines = output.getvalue().splitlines()
                self.assertIn("Starting Sample React + Python Full-Stack Services", lines)
                self.assertIn(f"Frontend: http://{local_host}:3000", lines)
                self.assertIn(
                    f"Backend:  http://{local_host}:8000 (API docs: http://{local_host}:8000/docs)",
                    lines,
                )
                self.assertEqual(
                    [line for line in lines if line.startswith("LAN: ")],
                    [f"LAN:      http://{address}:3000" for address in expected_addresses],
                )
                self.assertEqual(
                    [line for line in lines if line.startswith("LAN API: ")],
                    [f"LAN API:  http://{address}:8000/docs" for address in expected_addresses],
                )
                backend_socket.close.assert_called_once()
                frontend_socket.close.assert_called_once()

    def test_release_profile_serves_frontend_and_proxy_over_lan(self) -> None:
        addresses = discover_lan_ipv4()
        if not addresses:
            self.skipTest("No local LAN IPv4 address is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Release"
            root.mkdir()
            self.make_public(root)
            (root / "runtime.json").write_text('{"host": "0.0.0.0"}', encoding="utf-8")
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                for address in addresses:
                    with self.subTest(address=address):
                        frontend_url = f"http://{address}:{frontend}"
                        backend_url = f"http://{address}:{backend}"
                        self.assertIn("Portable frontend", client.get(frontend_url).text)
                        self.assertEqual(client.get(frontend_url + "/api/health").json(), {"status": "ok"})
                        self.assertEqual(client.get(backend_url + "/docs").status_code, 200)
                        count = client.post(frontend_url + "/api/counter/click").json()["count"]
                        self.assertEqual(client.get(backend_url + "/api/counter").json()["count"], count)

    def test_debug_profile_serves_local_proxy_and_refuses_lan_after_folder_rename(self) -> None:
        addresses = discover_lan_ipv4()
        if not addresses:
            self.skipTest("No local LAN IPv4 address is available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Debug"
            root.mkdir()
            self.make_public(root)
            (root / "runtime.json").write_text('{"host": "127.0.0.1"}', encoding="utf-8")
            root = root.rename(root.with_name("RenamedPortable"))
            frontend, backend = free_ports()
            frontend_url = f"http://127.0.0.1:{frontend}"
            backend_url = f"http://127.0.0.1:{backend}"
            with self.running(root, frontend, backend) as client:
                self.assertIn("Portable frontend", client.get(frontend_url).text)
                self.assertEqual(client.get(frontend_url + "/api/health").json(), {"status": "ok"})
                count = client.post(frontend_url + "/api/counter/click").json()["count"]
                self.assertEqual(client.get(backend_url + "/api/counter").json()["count"], count)
                for address in addresses:
                    for port in (frontend, backend):
                        with self.subTest(address=address, port=port):
                            with self.assertRaises((httpx.ConnectError, httpx.ConnectTimeout)):
                                client.get(f"http://{address}:{port}/api/health")

    def test_separate_ports_proxy_static_files_and_persistent_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_public(root)
            frontend, backend = free_ports()
            frontend_url = f"http://127.0.0.1:{frontend}"
            backend_url = f"http://127.0.0.1:{backend}"
            with self.running(root, frontend, backend) as client:
                self.assertIn("Portable frontend", client.get(frontend_url).text)
                self.assertEqual(client.get(backend_url + "/api/health").json(), {"status": "ok"})
                self.assertEqual(client.get(frontend_url + "/api/counter").json()["count"], 0)
                self.assertEqual(client.post(frontend_url + "/api/counter/click").json()["count"], 1)
                self.assertEqual(client.get(backend_url + "/api/counter").json()["count"], 1)
                self.assertEqual(client.get(frontend_url + "/%2e%2e/.env").status_code, 404)
            self.assertTrue((root / "data" / "portable.db").is_file())
            frontend, backend = free_ports()
            with self.running(root, frontend, backend) as client:
                frontend_url = f"http://127.0.0.1:{frontend}"
                self.assertEqual(client.get(frontend_url + "/api/counter").json()["count"], 1)
                self.assertEqual(client.delete(frontend_url + "/api/counter").json()["count"], 0)

    def test_busy_frontend_port_fails_and_releases_backend_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory, socket.socket() as busy:
            root = Path(directory)
            self.make_public(root)
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            frontend = busy.getsockname()[1]
            _, backend = free_ports()
            completed = subprocess.run(
                self.command(root, "--frontend-port", str(frontend), "--backend", str(backend)),
                cwd=root, env=self.environment(), capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("Cannot bind frontend", completed.stderr)
            with socket.socket() as released:
                released.bind(("127.0.0.1", backend))

    def test_invalid_and_duplicate_ports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for arguments in (("--frontend", "0"), ("--backend", "65536"),
                              ("--backend", "abc"), ("--frontend", "8000")):
                with self.subTest(arguments=arguments):
                    completed = subprocess.run(
                        self.command(Path(directory), *arguments), env=self.environment(),
                        capture_output=True, text=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("error:", completed.stderr)

    def test_backend_startup_failure_stops_both_services(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_public(root)
            (root / "blocked").write_text("A file cannot hold a database directory.")
            (root / ".env").write_text("COUNTER_DB_PATH=blocked/counter.db\n")
            frontend, backend = free_ports()
            completed = subprocess.run(
                self.command(root, "--frontend", str(frontend), "--backend", str(backend)),
                cwd=root, env=self.environment(), capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("Backend failed", completed.stderr)
            for port in (frontend, backend):
                with socket.socket() as released:
                    released.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
