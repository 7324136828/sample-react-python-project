from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build import BuildError, checked_path, publish_artifacts


def write_release(directory: Path, version: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "server.exe").write_bytes(f"server-{version}".encode("ascii"))
    (directory / "public").mkdir()
    (directory / "public" / "index.html").write_text(version, encoding="utf-8")
    (directory / "run.bat").write_text(f"run-{version}", encoding="ascii")


def release_contents(directory: Path) -> tuple[bytes, str, str]:
    return (
        (directory / "server.exe").read_bytes(),
        (directory / "public" / "index.html").read_text(encoding="utf-8"),
        (directory / "run.bat").read_text(encoding="ascii"),
    )


class PublicationTests(unittest.TestCase):
    def test_replacement_preserves_database_and_local_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "release"
            write_release(output, "old")
            write_release(stage, "new")
            (output / "data").mkdir()
            database = output / "data" / "counter.db"
            database.write_bytes(b"persistent counter data")
            configuration = output / ".env"
            configuration.write_text("BACKEND_PORT=9000\n", encoding="utf-8")

            publish_artifacts(stage, repository=repository)

            self.assertEqual(release_contents(output), (b"server-new", "new", "run-new"))
            self.assertEqual(database.read_bytes(), b"persistent counter data")
            self.assertEqual(configuration.read_text(encoding="utf-8"), "BACKEND_PORT=9000\n")
            self.assertEqual(
                release_contents(stage / "previous"),
                (b"server-old", "old", "run-old"),
            )

    def test_failed_public_replacement_restores_the_previous_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "release"
            write_release(output, "old")
            write_release(stage, "new")
            original_rename = Path.rename

            def fail_public_move(source: Path, target: Path) -> Path:
                if source == stage / "public" and Path(target) == output / "public":
                    raise OSError("simulated locked destination")
                return original_rename(source, target)

            with mock.patch.object(Path, "rename", new=fail_public_move):
                with self.assertRaises(BuildError):
                    publish_artifacts(stage, repository=repository)

            self.assertEqual(release_contents(output), (b"server-old", "old", "run-old"))
            self.assertEqual(release_contents(stage), (b"server-new", "new", "run-new"))

    def test_checked_path_rejects_parent_and_paths_escaping_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            for unsafe in (repository, repository / ".." / "outside"):
                with self.subTest(path=unsafe), self.assertRaises(BuildError):
                    checked_path(unsafe, repository)


if __name__ == "__main__":
    unittest.main()
