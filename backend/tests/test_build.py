from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from build import (
    ARTIFACTS,
    LAUNCHER,
    PROFILES,
    BuildError,
    checked_path,
    main,
    prepare_artifacts,
    publish_artifacts,
    validate_artifacts,
)


def write_packages(directory: Path, version: str) -> None:
    for profile, host in PROFILES.items():
        package = directory / profile
        package.mkdir(parents=True, exist_ok=True)
        (package / "server.exe").write_bytes(f"server-{profile}-{version}".encode("ascii"))
        (package / "public" / "assets").mkdir(parents=True)
        (package / "public" / "index.html").write_text(version, encoding="utf-8")
        (package / "public" / "assets" / "app.js").write_text(
            f"script-{profile}-{version}", encoding="utf-8",
        )
        (package / "run.bat").write_text(f"run-{profile}-{version}", encoding="ascii")
        # Different old defaults make runtime.json replacement and rollback observable.
        runtime_host = host if version == "new" else f"old-{host}"
        (package / "runtime.json").write_text(
            json.dumps({"host": runtime_host}), encoding="ascii",
        )
        ports = {"frontend_port": 4100, "backend_port": 9200}
        if version != "new":
            ports = {"frontend_port": 4300, "backend_port": 9400}
        (package / "port_mapping.json").write_text(json.dumps(ports), encoding="ascii")


def package_contents(directory: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*") if path.is_file()
    }


def managed_contents(directory: Path) -> dict[str, dict[Path, bytes]]:
    return {profile: package_contents(directory / profile) for profile in PROFILES}


class ArtifactPreparationTests(unittest.TestCase):
    def test_build_creates_two_self_contained_packages_with_mode_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "server.exe").write_bytes(b"shared backend")
            (stage / "public" / "assets").mkdir(parents=True)
            (stage / "public" / "index.html").write_text("shared frontend", encoding="utf-8")
            (stage / "public" / "assets" / "app.js").write_bytes(b"shared script")
            defaults = stage / "defaults.json"
            defaults.write_text(
                '{"frontend_port": 4100, "backend_port": 9200}\n', encoding="utf-8",
            )

            packages = prepare_artifacts(stage, defaults_path=defaults)

            self.assertEqual(packages, stage / "packages")
            self.assertEqual({path.name for path in packages.iterdir()}, {"Debug", "Release"})
            for profile, expected_host in {"Debug": "127.0.0.1", "Release": "0.0.0.0"}.items():
                with self.subTest(profile=profile):
                    package = packages / profile
                    validate_artifacts(package, host=expected_host)
                    self.assertEqual({path.name for path in package.iterdir()}, set(ARTIFACTS))
                    self.assertEqual((package / "server.exe").read_bytes(), b"shared backend")
                    self.assertEqual(package_contents(package / "public"), package_contents(stage / "public"))
                    self.assertEqual((package / "run.bat").read_text(encoding="ascii"), LAUNCHER)
                    self.assertEqual((package / "port_mapping.json").read_bytes(), defaults.read_bytes())
                    self.assertEqual(
                        json.loads((package / "runtime.json").read_text(encoding="ascii")),
                        {"host": expected_host},
                    )
                    self.assertFalse(any(path.is_symlink() for path in package.rglob("*")))

            # Each mode remains usable after the shared staging files or the other mode change.
            (stage / "server.exe").unlink()
            defaults.unlink()
            (packages / "Debug" / "public" / "index.html").write_text("changed", encoding="utf-8")
            self.assertEqual((packages / "Release" / "server.exe").read_bytes(), b"shared backend")
            self.assertEqual(
                (packages / "Release" / "public" / "index.html").read_text(encoding="utf-8"),
                "shared frontend",
            )
            self.assertEqual(
                json.loads((packages / "Release" / "port_mapping.json").read_text(encoding="utf-8")),
                {"frontend_port": 4100, "backend_port": 9200},
            )

    def test_launcher_initializes_settings_before_starting_servers(self) -> None:
        initialize = '"%~dp0server.exe" --initialize-settings\n'
        abort_on_failure = "if errorlevel 1 exit /b %ERRORLEVEL%\n"
        launch = ':launch\n"%~dp0server.exe" %*\n'
        self.assertIn(initialize + abort_on_failure + launch, LAUNCHER)
        for flag in ("--help", "-h"):
            help_bypass = f'if /i "%~1"=="{flag}" if "%~2"=="" goto launch\n'
            self.assertLess(LAUNCHER.index(help_bypass), LAUNCHER.index(initialize))

    def test_preparation_rejects_missing_or_invalid_port_defaults_before_creating_packages(self) -> None:
        for contents in (None, "{}", "not json", '{"frontend_port": 0, "backend_port": 9200}'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                defaults = stage / "defaults.json"
                if contents is not None:
                    defaults.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(BuildError, "Invalid port defaults"):
                    prepare_artifacts(stage, defaults_path=defaults)
                self.assertFalse((stage / "packages").exists())

    def test_build_rejects_invalid_port_defaults_before_installing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_defaults = Path(directory) / "missing.json"
            with (
                mock.patch("build.os.name", "nt"),
                mock.patch("build.PORT_MAPPING", missing_defaults),
                mock.patch("build.run") as run,
                mock.patch("build.select_python") as select_python,
            ):
                with self.assertRaisesRegex(BuildError, "Invalid port defaults"):
                    main([])
                run.assert_not_called()
                select_python.assert_not_called()

    def test_validation_rejects_an_incorrect_runtime_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            packages = Path(directory)
            write_packages(packages, "new")
            with self.assertRaisesRegex(BuildError, "Unexpected runtime defaults"):
                validate_artifacts(packages / "Debug", host="0.0.0.0")

    def test_validation_rejects_missing_or_invalid_packaged_port_defaults(self) -> None:
        for contents in (None, "{}", "[]", '{"frontend_port": 4100, "backend_port": "9200"}'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                packages = Path(directory)
                write_packages(packages, "new")
                defaults = packages / "Debug" / "port_mapping.json"
                if contents is None:
                    defaults.unlink()
                else:
                    defaults.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(BuildError, "Invalid port defaults"):
                    validate_artifacts(packages / "Debug", host=PROFILES["Debug"])


class PublicationTests(unittest.TestCase):
    def test_replacement_preserves_each_modes_database_and_local_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "packages"
            write_packages(output, "old")
            write_packages(stage, "new")
            old_artifacts = managed_contents(output)
            new_artifacts = managed_contents(stage)
            user_state = {}
            for profile in PROFILES:
                package = output / profile
                (package / "data").mkdir()
                (package / "data" / "counter.db").write_bytes(f"{profile} counter data".encode("ascii"))
                (package / ".env").write_text(f"BACKEND_PORT=9000\nMODE={profile}\n", encoding="utf-8")
                (package / "notes.txt").write_text(f"{profile} user notes", encoding="utf-8")
                user_state[profile] = {
                    name: (package / name).read_bytes()
                    for name in ("data/counter.db", ".env", "notes.txt")
                }

            publish_artifacts(stage, repository=repository)

            for profile in PROFILES:
                with self.subTest(profile=profile):
                    actual = package_contents(output / profile)
                    self.assertEqual(
                        {path: content for path, content in actual.items() if path.parts[0] in ARTIFACTS},
                        new_artifacts[profile],
                    )
                    for name, content in user_state[profile].items():
                        self.assertEqual((output / profile / name).read_bytes(), content)
                    self.assertEqual(package_contents(stage / "previous" / profile), old_artifacts[profile])
                    self.assertFalse(any((stage / profile / name).exists() for name in ARTIFACTS))

    def test_locked_release_destination_rolls_back_both_existing_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "packages"
            write_packages(output, "old")
            write_packages(stage, "new")
            for profile in PROFILES:
                (output / profile / ".env").write_text(f"MODE={profile}", encoding="utf-8")
            old_contents = managed_contents(output)
            new_contents = managed_contents(stage)
            original_rename = Path.rename
            moves = []

            def fail_locked_release(source: Path, target: Path) -> Path:
                if source == output / "Release" / "public":
                    raise OSError("simulated locked Release destination")
                moves.append((source, Path(target)))
                return original_rename(source, target)

            with mock.patch.object(Path, "rename", new=fail_locked_release):
                with self.assertRaisesRegex(BuildError, "Could not replace Debug/Release artifacts"):
                    publish_artifacts(stage, repository=repository)

            for name in ARTIFACTS:
                self.assertIn((stage / "Debug" / name, output / "Debug" / name), moves)
            self.assertIn((stage / "Release" / "server.exe", output / "Release" / "server.exe"), moves)
            self.assertEqual(managed_contents(output), old_contents)
            self.assertEqual(managed_contents(stage), new_contents)

    def test_failed_first_install_leaves_no_mixed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "packages"
            write_packages(stage, "new")
            new_contents = managed_contents(stage)
            original_rename = Path.rename

            def fail_release_install(source: Path, target: Path) -> Path:
                if source == stage / "Release" / "public":
                    raise OSError("simulated failed first installation")
                return original_rename(source, target)

            with mock.patch.object(Path, "rename", new=fail_release_install):
                with self.assertRaises(BuildError):
                    publish_artifacts(stage, repository=repository)

            self.assertEqual(managed_contents(stage), new_contents)
            for profile in PROFILES:
                with self.subTest(profile=profile):
                    self.assertEqual(package_contents(output / profile), {})
                    self.assertFalse(any((output / profile / name).exists() for name in ARTIFACTS))

    def test_unsafe_release_target_is_rejected_before_either_mode_is_moved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            output = repository / "dist"
            stage = repository / "build" / "packages"
            write_packages(output, "old")
            write_packages(stage, "new")
            old_contents = managed_contents(output)
            new_contents = managed_contents(stage)
            original_resolve = Path.resolve

            def resolve_unsafe_target(path: Path, *args: object, **kwargs: object) -> Path:
                if path == output / "Release" / "public":
                    return repository / "outside"
                return original_resolve(path, *args, **kwargs)

            with (
                mock.patch.object(Path, "resolve", new=resolve_unsafe_target),
                mock.patch.object(Path, "rename") as rename,
            ):
                with self.assertRaisesRegex(BuildError, "Unsafe build path"):
                    publish_artifacts(stage, repository=repository)
                rename.assert_not_called()

            self.assertEqual(managed_contents(output), old_contents)
            self.assertEqual(managed_contents(stage), new_contents)

    def test_checked_path_rejects_parent_and_paths_escaping_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            for unsafe in (repository, repository / ".." / "outside"):
                with self.subTest(path=unsafe), self.assertRaises(BuildError):
                    checked_path(unsafe, repository)


if __name__ == "__main__":
    unittest.main()
