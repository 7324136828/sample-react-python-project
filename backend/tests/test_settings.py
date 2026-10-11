from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import settings
from backend.app import main


class SettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(
            os.environ,
            {
                "APPDATA": str(self.root / "roaming"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "TEMP": str(self.root / "temp"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("COUNTER_DB_PATH", None)

    def write_settings(self, content: str) -> Path:
        path = settings.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return path


class SettingsTests(SettingsTestCase):
    def test_first_load_creates_per_user_settings_with_temp_default(self) -> None:
        config_root = self.root / ("roaming" if os.name == "nt" else "config")
        expected_settings = config_root / settings.APP_NAME / "settings.json"
        expected_data = self.root / "temp" / settings.APP_NAME

        loaded = settings.load_settings()

        self.assertEqual(loaded.settings_path, expected_settings)
        self.assertEqual(loaded.data_directory, expected_data)
        self.assertEqual(
            json.loads(expected_settings.read_text(encoding="utf-8")),
            {"data_directory": str(expected_data)},
        )
        original = expected_settings.read_bytes()
        self.assertEqual(settings.load_settings(), loaded)
        self.assertEqual(expected_settings.read_bytes(), original)

    def test_simultaneous_first_loads_read_complete_identical_defaults(self) -> None:
        workers = 8
        ready = Barrier(workers)

        def load_from_worker(_: int) -> settings.AppSettings:
            ready.wait(timeout=10)
            return settings.load_settings()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            loaded_settings = list(executor.map(load_from_worker, range(workers)))

        expected_path = settings.settings_path()
        expected_data = self.root / "temp" / settings.APP_NAME
        for loaded in loaded_settings:
            self.assertEqual(loaded.settings_path, expected_path)
            self.assertEqual(loaded.data_directory, expected_data)
        self.assertEqual(
            json.loads(expected_path.read_text(encoding="utf-8")),
            {"data_directory": str(expected_data)},
        )

    def test_existing_settings_preserve_formatting_and_unknown_keys(self) -> None:
        content = '{\n  "data_directory": "../my-data",\n  "theme": "dark"\n}\n'
        path = self.write_settings(content)

        loaded = settings.load_settings()

        self.assertEqual(loaded.data_directory, (path.parent / "../my-data").resolve())
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_missing_data_directory_uses_default_without_rewriting(self) -> None:
        content = '{ "theme": "dark" }\n'
        path = self.write_settings(content)

        self.assertEqual(
            settings.load_settings().data_directory,
            self.root / "temp" / settings.APP_NAME,
        )
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_invalid_settings_report_path_and_preserve_user_file(self) -> None:
        invalid_contents = [
            "{broken json",
            "null",
            "[]",
            '"a string"',
            '{"data_directory": null}',
            '{"data_directory": 42}',
            '{"data_directory": false}',
            '{"data_directory": ""}',
            '{"data_directory": "   "}',
        ]
        for content in invalid_contents:
            with self.subTest(content=content):
                path = self.write_settings(content)
                with self.assertRaises(settings.SettingsError) as error:
                    settings.load_settings()
                self.assertIn(str(path), str(error.exception))
                self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_data_directory_expands_environment_variables_and_home(self) -> None:
        variable = "%SETTINGS_TEST_STORAGE%" if os.name == "nt" else "${SETTINGS_TEST_STORAGE}"
        expanded_root = self.root / "expanded"
        with patch.dict(os.environ, {"SETTINGS_TEST_STORAGE": str(expanded_root)}):
            self.write_settings(json.dumps({"data_directory": variable + "/records"}))
            self.assertEqual(
                settings.load_settings().data_directory, expanded_root / "records"
            )

        home = self.root / "home"
        with patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            self.write_settings(json.dumps({"data_directory": "~/records"}))
            self.assertEqual(settings.load_settings().data_directory, home / "records")

    def test_settings_directory_falls_back_to_user_home(self) -> None:
        os.environ.pop("APPDATA", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        home = self.root / "home"
        with patch.object(Path, "home", return_value=home):
            config_root = home / "AppData" / "Roaming" if os.name == "nt" else home / ".config"
            self.assertEqual(
                settings.settings_path(), config_root / settings.APP_NAME / "settings.json"
            )

    def test_temp_directory_falls_back_to_platform_temp(self) -> None:
        os.environ.pop("TEMP", None)
        fallback = self.root / "platform-temp"
        with patch.object(settings.tempfile, "gettempdir", return_value=str(fallback)):
            self.assertEqual(
                settings.default_data_directory(), fallback / settings.APP_NAME
            )

    def test_database_override_takes_priority_and_retains_settings_creation(self) -> None:
        base = self.root / "project"
        os.environ["COUNTER_DB_PATH"] = "overrides/custom.db"

        self.assertEqual(
            settings.configured_database_path(base), base / "overrides" / "custom.db"
        )
        self.assertTrue(settings.settings_path().is_file())
        with patch.object(main, "PROJECT_ROOT", base):
            self.assertEqual(
                main.configured_database_path(), base / "overrides" / "custom.db"
            )

        for empty_override in ("", "  \t  "):
            with self.subTest(override=empty_override):
                os.environ["COUNTER_DB_PATH"] = empty_override
                self.assertEqual(
                    settings.configured_database_path(base),
                    self.root / "temp" / settings.APP_NAME / "counter.db",
                )

    def test_database_override_still_reports_invalid_settings(self) -> None:
        path = self.write_settings("{invalid}")
        os.environ["COUNTER_DB_PATH"] = str(self.root / "override.db")
        with self.assertRaises(settings.SettingsError) as error:
            settings.configured_database_path(self.root)
        self.assertIn(str(path), str(error.exception))
        self.assertEqual(path.read_bytes(), b"{invalid}")

    def test_application_reads_settings_at_startup(self) -> None:
        application = main.create_app()
        self.assertFalse(settings.settings_path().exists())

        with TestClient(application) as client:
            self.assertEqual(client.get("/api/counter").json()["count"], 0)
            self.assertTrue(settings.settings_path().is_file())
            self.assertTrue(
                (self.root / "temp" / settings.APP_NAME / "counter.db").is_file()
            )

    def test_custom_storage_persists_counter_after_application_recreation(self) -> None:
        storage = self.root / "chosen-storage" / "records"
        content = json.dumps({"data_directory": str(storage), "theme": "dark"})
        path = self.write_settings(content)

        with TestClient(main.create_app()) as client:
            self.assertEqual(client.post("/api/counter/click").json()["count"], 1)

        self.assertTrue((storage / "counter.db").is_file())
        self.assertFalse((self.root / "temp" / settings.APP_NAME / "counter.db").exists())
        with TestClient(main.create_app()) as restarted_client:
            self.assertEqual(restarted_client.get("/api/counter").json()["count"], 1)
            self.assertEqual(restarted_client.post("/api/counter/click").json()["count"], 2)
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_explicit_database_path_does_not_require_valid_settings(self) -> None:
        path = self.write_settings("{invalid}")
        database = self.root / "explicit" / "counter.db"

        with TestClient(main.create_app(database)) as client:
            self.assertEqual(client.post("/api/counter/click").json()["count"], 1)

        self.assertTrue(database.is_file())
        self.assertEqual(path.read_bytes(), b"{invalid}")


class PortSettingsTests(SettingsTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mapping_path = self.root / "project" / "port_mapping.json"
        self.mapping_path.parent.mkdir(parents=True)
        self.defaults = {"frontend_port": 4310, "backend_port": 4320}
        self.mapping_path.write_text(json.dumps(self.defaults), encoding="utf-8")

    def test_mapping_reads_nonstandard_defaults_and_ignores_other_keys(self) -> None:
        self.mapping_path.write_text(
            json.dumps({**self.defaults, "application_name": "example"}), encoding="utf-8"
        )
        self.assertEqual(settings.load_port_mapping(self.mapping_path), self.defaults)
        self.assertFalse(settings.settings_path().exists())

    def test_mapping_accepts_port_boundaries(self) -> None:
        ports = {"frontend_port": 1, "backend_port": 65535}
        self.mapping_path.write_text(json.dumps(ports), encoding="utf-8")
        self.assertEqual(settings.load_port_mapping(self.mapping_path), ports)

    def test_first_initialization_creates_empty_json_then_fills_ports(self) -> None:
        with patch.object(settings, "_write_json", wraps=settings._write_json) as write:
            self.assertEqual(settings.initialize_port_settings(self.mapping_path), self.defaults)

        path = settings.settings_path()
        self.assertEqual(write.call_args_list[0].args, (path, {}))
        self.assertEqual(write.call_args_list[1].args, (path, self.defaults))
        self.assertEqual(write.call_args_list[1].kwargs, {"overwrite": True})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.defaults)
        self.assertFalse(list(path.parent.glob(".settings-*.tmp")))
        self.assertEqual(
            settings.load_settings().data_directory,
            self.root / "temp" / settings.APP_NAME,
        )
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.defaults)

    def test_partial_configuration_fills_only_missing_port(self) -> None:
        for existing in ("frontend_port", "backend_port"):
            with self.subTest(existing=existing):
                original = {
                    existing: 4500,
                    "data_directory": "../saved-data",
                    "theme": {"color": "blue", "enabled": True},
                    "custom": [1, "two", None],
                }
                path = self.write_settings(json.dumps(original))
                expected_ports = {**self.defaults, existing: 4500}
                self.assertEqual(
                    settings.initialize_port_settings(self.mapping_path), expected_ports
                )
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    {**original, **expected_ports},
                )

    def test_complete_configuration_preserves_custom_ports_and_formatting(self) -> None:
        content = (
            '{\n "frontend_port": 4510, "backend_port": 4520,\n'
            ' "data_directory": "../saved-data", "theme": "dark"\n}\n'
        )
        path = self.write_settings(content)
        for _ in range(2):
            self.assertEqual(
                settings.initialize_port_settings(self.mapping_path),
                {"frontend_port": 4510, "backend_port": 4520},
            )
            self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_initialization_is_idempotent(self) -> None:
        ports = settings.initialize_port_settings(self.mapping_path)
        path = settings.settings_path()
        original = path.read_bytes()
        with patch.object(settings, "_write_json") as write:
            self.assertEqual(settings.initialize_port_settings(self.mapping_path), ports)
            write.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_existing_data_settings_gain_ports_without_changing_data_directory(self) -> None:
        loaded = settings.load_settings()
        self.assertEqual(settings.initialize_port_settings(self.mapping_path), self.defaults)
        self.assertEqual(
            json.loads(loaded.settings_path.read_text(encoding="utf-8")),
            {"data_directory": str(loaded.data_directory), **self.defaults},
        )

    def test_utf8_bom_is_accepted_in_settings_and_mapping(self) -> None:
        self.mapping_path.write_text(json.dumps(self.defaults), encoding="utf-8-sig")
        path = self.write_settings('\ufeff{"frontend_port": 4510, "backend_port": 4520}')
        original = path.read_bytes()
        self.assertEqual(settings.load_port_mapping(self.mapping_path), self.defaults)
        self.assertEqual(
            settings.initialize_port_settings(self.mapping_path),
            {"frontend_port": 4510, "backend_port": 4520},
        )
        self.assertEqual(path.read_bytes(), original)

    def test_malformed_or_nonobject_settings_are_preserved(self) -> None:
        for content in ("{broken", "null", "[]", '"text"', "true"):
            with self.subTest(content=content):
                path = self.write_settings(content)
                with self.assertRaises(settings.SettingsError) as error:
                    settings.initialize_port_settings(self.mapping_path)
                self.assertIn(str(path), str(error.exception))
                self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_invalid_existing_port_is_reported_and_never_replaced(self) -> None:
        for field in self.defaults:
            for invalid in (None, True, False, "4500", 4500.0, 0, -1, 65536, [], {}):
                with self.subTest(field=field, value=invalid):
                    content = json.dumps({field: invalid, "theme": "dark"})
                    path = self.write_settings(content)
                    with self.assertRaises(settings.SettingsError) as error:
                        settings.initialize_port_settings(self.mapping_path)
                    self.assertIn(str(path), str(error.exception))
                    self.assertIn(field, str(error.exception))
                    self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_duplicate_existing_or_effective_ports_are_preserved(self) -> None:
        for ports in (
            {"frontend_port": 4500, "backend_port": 4500},
            {"frontend_port": self.defaults["backend_port"]},
            {"backend_port": self.defaults["frontend_port"]},
        ):
            with self.subTest(ports=ports):
                content = json.dumps(ports)
                path = self.write_settings(content)
                with self.assertRaises(settings.SettingsError) as error:
                    settings.initialize_port_settings(self.mapping_path)
                self.assertIn(str(path), str(error.exception))
                self.assertIn("frontend_port", str(error.exception))
                self.assertIn("backend_port", str(error.exception))
                self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_invalid_mapping_never_creates_user_settings(self) -> None:
        invalid_objects = [
            ("{broken", None),
            ("null", None),
            ("[]", None),
            ("{}", "frontend_port"),
            ('{"frontend_port": 4310}', "backend_port"),
            ('{"frontend_port": 4310, "backend_port": 4310}', "backend_port"),
        ]
        for field in self.defaults:
            for invalid in (None, True, False, "4500", 4500.0, 0, -1, 65536, [], {}):
                invalid_objects.append((json.dumps({**self.defaults, field: invalid}), field))
        for content, field in invalid_objects:
            with self.subTest(content=content):
                self.mapping_path.write_text(content, encoding="utf-8")
                with self.assertRaises(settings.SettingsError) as error:
                    settings.initialize_port_settings(self.mapping_path)
                self.assertIn(str(self.mapping_path), str(error.exception))
                if field is not None:
                    self.assertIn(field, str(error.exception))
                self.assertFalse(settings.settings_path().exists())
                self.assertEqual(self.mapping_path.read_text(encoding="utf-8"), content)

    def test_missing_mapping_reports_path_without_creating_settings(self) -> None:
        missing_path = self.root / "missing_mapping.json"
        with self.assertRaises(settings.SettingsError) as error:
            settings.initialize_port_settings(missing_path)
        self.assertIn(str(missing_path), str(error.exception))
        self.assertFalse(settings.settings_path().exists())

    def test_invalid_mapping_does_not_rewrite_existing_settings(self) -> None:
        content = '{ "theme": "dark" }\n'
        path = self.write_settings(content)
        self.mapping_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(settings.SettingsError):
            settings.initialize_port_settings(self.mapping_path)
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))

    def test_initial_empty_json_does_not_overwrite_another_startups_file(self) -> None:
        path = settings.settings_path()
        path.parent.mkdir(parents=True)
        content = '{ "frontend_port": 4510, "backend_port": 4520, "theme": "dark" }\n'
        original_writer = settings._write_json

        def competing_write(target: Path, values: dict, *, overwrite: bool = False) -> None:
            target.write_bytes(content.encode("utf-8"))
            original_writer(target, values, overwrite=overwrite)

        with patch.object(settings, "_write_json", side_effect=competing_write):
            self.assertEqual(
                settings.initialize_port_settings(self.mapping_path),
                {"frontend_port": 4510, "backend_port": 4520},
            )
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))
        self.assertFalse(list(path.parent.glob(".settings-*.tmp")))

    def test_initialized_ports_are_reread_from_persisted_settings(self) -> None:
        path = self.write_settings('{"theme": "dark"}')
        original_writer = settings._write_json
        updated_ports = {"frontend_port": 4510, "backend_port": 4520}

        def write_then_edit(target: Path, values: dict, *, overwrite: bool = False) -> None:
            original_writer(target, values, overwrite=overwrite)
            target.write_text(json.dumps({**values, **updated_ports}), encoding="utf-8")

        with patch.object(settings, "_write_json", side_effect=write_then_edit):
            self.assertEqual(
                settings.initialize_port_settings(self.mapping_path), updated_ports
            )
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"theme": "dark", **updated_ports},
        )

    def test_failed_atomic_update_preserves_existing_settings_and_cleans_temp(self) -> None:
        content = '{ "theme": "dark" }\n'
        path = self.write_settings(content)
        with patch.object(Path, "replace", side_effect=OSError("write denied")):
            with self.assertRaises(settings.SettingsError) as error:
                settings.initialize_port_settings(self.mapping_path)
        self.assertIn(str(path), str(error.exception))
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))
        self.assertFalse(list(path.parent.glob(".settings-*.tmp")))


if __name__ == "__main__":
    unittest.main()
