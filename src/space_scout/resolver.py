"""Read-only target resolution and the safe subset of the execution contract.

The resolver asks a supported tool where its cache lives, records the target
identity, and reports redirects. It never mutates the filesystem, never runs
shell text (``shell=False`` always), and never caches results: callers own
memoization so preview and execution always see a fresh answer.

v1 supports exactly uv, npm, and go. Other tools return an empty tuple; Bun is
out of scope because ``bun pm cache`` exits 1 without a package.json.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .knowledge import TOOL_METHODS, TargetSpec

_SUPPORTED_TOOLS = frozenset({"uv", "npm", "go"})


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    tool: str
    key: str
    path: Path
    redirected: bool
    reason: str
    identity: tuple[int, int, int] | None


def _absolute_binary(binary: Path) -> str:
    return os.fspath(binary if binary.is_absolute() else binary.absolute())


def _reject_unsafe(value: str) -> None:
    if value.startswith("-"):
        raise ValueError(f"positional argument may not start with '-': {value!r}")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"positional argument contains control characters: {value!r}")


def run_query(
    binary: Path,
    args: tuple[str, ...],
    *,
    positional: tuple[str, ...] = (),
    timeout: float = 2.0,
    cwd: Path | None = None,
) -> CommandResult:
    """Run one read-only query with an absolute argv and no shell interpretation."""
    for value in positional:
        _reject_unsafe(value)
    argv = [_absolute_binary(binary), *args]
    if positional:
        argv += ["--", *positional]
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(tuple(argv), -1, "", "", True)
    return CommandResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr, False)


def _output_lines(stdout: str) -> list[str]:
    stripped = [line.strip() for line in stdout.splitlines()]
    return [line for line in stripped if line]


def _expand_template(template: str, env: Mapping[str, str]) -> str:
    home = env.get("HOME") or env.get("USERPROFILE") or str(Path.home())
    values = {
        "home": home,
        "xdg_cache": env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache"),
        "localappdata": env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"),
        "userprofile": env.get("USERPROFILE") or home,
        "appdata": env.get("APPDATA") or os.path.join(home, "AppData", "Roaming"),
    }
    return template.format_map(values)


def _same_path(reported: str, default: str, platform: str) -> bool:
    if platform == "win32":
        return PureWindowsPath(reported) == PureWindowsPath(default)
    return PurePosixPath(reported) == PurePosixPath(default)


def _identity(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = os.lstat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_mode)


def _build_target(
    tool: str,
    spec: TargetSpec,
    reported: str,
    platform: str,
    env: Mapping[str, str],
) -> ResolvedTarget:
    path = Path(reported)
    override = next((name for name in spec.env_vars if name in env), None)
    if override is not None:
        redirected = True
        reason = f"redirected: environment variable {override} overrides the default location"
    else:
        default = _expand_template(spec.default_templates[platform], env)
        redirected = not _same_path(reported, default, platform)
        reason = (
            "redirected: tool-reported path differs from the default location"
            if redirected
            else f"{tool} reports its {spec.key} location"
        )
    identity = _identity(path)
    if identity is None:
        reason = f"{reason}; target does not exist"
    return ResolvedTarget(tool, spec.key, path, redirected, reason, identity)


def resolve_targets(
    tool: str,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., CommandResult] = run_query,
) -> tuple[ResolvedTarget, ...]:
    """Resolve every cache target a supported tool reports, or an empty tuple."""
    effective_platform = sys.platform if platform is None else platform
    method = TOOL_METHODS.get(tool)
    if tool not in _SUPPORTED_TOOLS or method is None or method.query_argv is None:
        return ()
    if effective_platform not in method.platforms:
        return ()
    effective_env: Mapping[str, str] = os.environ if env is None else env
    found = which(tool)
    if found is None:
        return ()
    result = run(Path(found), method.query_argv)
    if result.timed_out or result.returncode != 0:
        return ()
    lines = _output_lines(result.stdout)
    if len(lines) < len(method.targets):
        return ()
    return tuple(
        _build_target(tool, spec, reported, effective_platform, effective_env)
        for spec, reported in zip(method.targets, lines)
    )
