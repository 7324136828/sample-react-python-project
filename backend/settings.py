"""Per-user application settings and storage paths."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "sample-react-python-project"


class SettingsError(RuntimeError):
    """Raised when the user's settings cannot be read or created."""


@dataclass(frozen=True)
class AppSettings:
    settings_path: Path
    data_directory: Path


def settings_path() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root.expanduser().resolve() / APP_NAME / "settings.json"


def default_data_directory() -> Path:
    root = Path(os.environ.get("TEMP") or tempfile.gettempdir())
    return root.expanduser().resolve() / APP_NAME


def _create_default_settings(path: Path, data_directory: Path) -> None:
    # Publish complete JSON without replacing a file another startup created.
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".settings-", suffix=".tmp", delete=False,
        ) as settings_file:
            temporary_path = Path(settings_file.name)
            json.dump({"data_directory": str(data_directory)}, settings_file, indent=2)
            settings_file.write("\n")
        try:
            if os.name == "nt":
                # Windows rename fails if the destination already exists.
                temporary_path.rename(path)
            else:
                os.link(temporary_path, path)
        except FileExistsError:
            pass
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_settings() -> AppSettings:
    """Create defaults once; preserve settings the user has already written."""
    path = settings_path()
    default_directory = default_data_directory()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _create_default_settings(path, default_directory)
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"Cannot read or create settings at {path}: {error}") from error

    if not isinstance(settings, dict):
        raise SettingsError(f"Invalid settings at {path}: expected a JSON object")
    configured = settings.get("data_directory", str(default_directory))
    if not isinstance(configured, str) or not configured.strip() or "\0" in configured:
        raise SettingsError(
            f"Invalid settings at {path}: data_directory must be a non-empty folder path"
        )
    try:
        directory = Path(os.path.expandvars(configured)).expanduser()
        if not directory.is_absolute():
            directory = path.parent / directory
        directory = directory.resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise SettingsError(f"Invalid data_directory in settings at {path}: {error}") from error
    return AppSettings(settings_path=path, data_directory=directory)


def configured_database_path(base_directory: Path) -> Path:
    settings = load_settings()
    override = os.environ.get("COUNTER_DB_PATH")
    if override and override.strip():
        database = Path(os.path.expandvars(override)).expanduser()
        if not database.is_absolute():
            database = base_directory / database
        return database.resolve()
    return settings.data_directory / "counter.db"
