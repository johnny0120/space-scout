"""Persistent, plain-text user configuration for Space Scout."""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .knowledge import TOOL_METHODS


@dataclass(frozen=True, slots=True)
class Config:
    shortcuts: dict[str, Path]
    exclusions: tuple[Path, ...]
    overrides: dict[str, str]
    sort_key: str = "size"
    minimum_bytes: int = 0
    adapter_allowlist: tuple[str, ...] = ()


def config_path() -> Path:
    """Return the per-user configuration file path for the current platform."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "space-scout" / "config.toml"


def common_shortcuts() -> dict[str, Path]:
    home = Path.home()
    candidates = {
        "downloads": home / "Downloads",
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "developer": home / "Developer",
    }
    return {name: path for name, path in candidates.items() if path.is_dir()}


def _path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty path string")
    return Path(value).expanduser()


def load_config(path: Path | None = None) -> Config:
    target = Path(path) if path is not None else config_path()
    if not target.exists():
        return Config({}, (), {})
    try:
        with target.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read configuration {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a TOML table")
    allowed = {
        "shortcuts",
        "exclusions",
        "overrides",
        "sort_key",
        "minimum_bytes",
        "adapter_allowlist",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")

    shortcuts_raw = raw.get("shortcuts", {})
    if not isinstance(shortcuts_raw, dict) or any(not isinstance(k, str) for k in shortcuts_raw):
        raise ValueError("shortcuts must be a table of names and paths")
    shortcuts = {name: _path(value, f"shortcuts.{name}") for name, value in shortcuts_raw.items()}

    exclusions_raw = raw.get("exclusions", [])
    if not isinstance(exclusions_raw, list):
        raise ValueError("exclusions must be an array of paths")
    exclusions = tuple(_path(value, "exclusions") for value in exclusions_raw)

    overrides_raw = raw.get("overrides", {})
    if not isinstance(overrides_raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in overrides_raw.items()
    ):
        raise ValueError("overrides must be a table of labels")
    sort_key = raw.get("sort_key", "size")
    if not isinstance(sort_key, str) or not sort_key:
        raise ValueError("sort_key must be a non-empty string")
    minimum_bytes = raw.get("minimum_bytes", 0)
    if isinstance(minimum_bytes, bool) or not isinstance(minimum_bytes, int) or minimum_bytes < 0:
        raise ValueError("minimum_bytes must be a non-negative integer")

    adapter_raw = raw.get("adapter_allowlist", [])
    if not isinstance(adapter_raw, list):
        raise ValueError("adapter_allowlist must be an array of adapter ids")
    if any(not isinstance(entry, str) or entry not in TOOL_METHODS for entry in adapter_raw):
        raise ValueError("adapter_allowlist must contain only valid adapter ids")
    adapter_allowlist = tuple(adapter_raw)

    return Config(
        shortcuts, exclusions, dict(overrides_raw), sort_key, minimum_bytes, adapter_allowlist
    )


def _toml_string(value: str) -> str:
    """Encode a string as a TOML basic string.

    Retain Unicode scalars: JSON's surrogate-pair escapes are invalid TOML.
    DEL must still be escaped because TOML forbids it in basic strings.
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _validate_config(config: Config) -> None:
    if not isinstance(config, Config):
        raise ValueError("config must be a Config instance")
    if not isinstance(config.shortcuts, dict):
        raise ValueError("shortcuts must be a dictionary of string labels and Path values")
    for name, shortcut_value in config.shortcuts.items():
        if not isinstance(name, str):
            raise ValueError("shortcuts labels must be strings")
        if not isinstance(shortcut_value, Path):
            raise ValueError(f"shortcuts.{name} must be a Path")
    if not isinstance(config.exclusions, tuple):
        raise ValueError("exclusions must be a tuple of Path values")
    for exclusion in config.exclusions:
        if not isinstance(exclusion, Path):
            raise ValueError("exclusions values must be Path objects")
    if not isinstance(config.overrides, dict):
        raise ValueError("overrides must be a dictionary of string labels")
    for name, override_value in config.overrides.items():
        if not isinstance(name, str):
            raise ValueError("overrides labels must be strings")
        if not isinstance(override_value, str):
            raise ValueError(f"overrides.{name} must be a string")
    if not isinstance(config.sort_key, str) or not config.sort_key:
        raise ValueError("sort_key must be a non-empty string")
    if isinstance(config.minimum_bytes, bool) or not isinstance(config.minimum_bytes, int):
        raise ValueError("minimum_bytes must be a non-negative integer")
    if config.minimum_bytes < 0:
        raise ValueError("minimum_bytes must be a non-negative integer")
    if not isinstance(config.adapter_allowlist, tuple):
        raise ValueError("adapter_allowlist must be a tuple of adapter ids")
    for adapter in config.adapter_allowlist:
        if not isinstance(adapter, str) or adapter not in TOOL_METHODS:
            raise ValueError("adapter_allowlist must contain only valid adapter ids")


def save_config(config: Config, path: Path | None = None) -> None:
    _validate_config(config)
    target = Path(path) if path is not None else config_path()
    lines = [
        f"sort_key = {_toml_string(config.sort_key)}",
        f"minimum_bytes = {config.minimum_bytes}",
        "adapter_allowlist = [",
        *[f"  {_toml_string(value)}," for value in config.adapter_allowlist],
        "]",
        "exclusions = [",
        *[f"  {_toml_string(str(value))}," for value in config.exclusions],
        "]",
        "",
    ]
    lines.append("[shortcuts]")
    for shortcut_name, shortcut_value in config.shortcuts.items():
        lines.append(f"{_toml_string(shortcut_name)} = {_toml_string(str(shortcut_value))}")
    lines.extend(["", "[overrides]"])
    for override_name, override_value in config.overrides.items():
        lines.append(f"{_toml_string(override_name)} = {_toml_string(str(override_value))}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not write configuration {target}: {exc}") from exc
