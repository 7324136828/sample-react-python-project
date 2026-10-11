from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import httpx

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
