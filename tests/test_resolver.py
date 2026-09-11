"""Tests for the read-only tool target resolver and its execution contract."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from space_scout.resolver import CommandResult, resolve_targets, run_query


def _fake_run(stdout: str, returncode: int = 0, timed_out: bool = False):
    def run(binary: Path, args: tuple[str, ...], **kwargs: object) -> CommandResult:
        return CommandResult((str(binary), *args), returncode, stdout, "", timed_out)

    return run


def _capture_run(captured: list[list[str]]):
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_build_argv_uses_absolute_binary_and_terminator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _capture_run(captured))
    result = run_query(Path("/opt/tools/uv"), ("cache", "clean"), positional=("ruff",))
    assert captured == [["/opt/tools/uv", "cache", "clean", "--", "ruff"]]
    assert result.argv == ("/opt/tools/uv", "cache", "clean", "--", "ruff")


def test_build_argv_omits_terminator_without_positionals(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _capture_run(captured))
    run_query(Path("/opt/tools/uv"), ("cache", "dir"))
    assert captured == [["/opt/tools/uv", "cache", "dir"]]


def test_run_query_rejects_dash_leading_positional(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called for rejected positionals")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(ValueError):
        run_query(Path("/opt/tools/uv"), ("cache", "clean"), positional=("--force",))


@pytest.mark.parametrize("value", ["bad\nvalue", "bad\x01value", "bad\x7fvalue"])
def test_run_query_rejects_newline_and_control_positionals(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called for rejected positionals")

    monkeypatch.setattr(subprocess, "run", fail_run)
    with pytest.raises(ValueError):
        run_query(Path("/opt/tools/uv"), ("cache",), positional=(value,))


def test_run_query_contract_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def spy_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", spy_run)
    result = run_query(Path("/opt/tools/uv"), ("cache", "dir"), cwd=tmp_path)
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 2.0
    assert captured["cwd"] == tmp_path
    assert result.stdout == "out"


def test_resolve_uv_reads_single_line() -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def run(binary: Path, args: tuple[str, ...], **kwargs: object) -> CommandResult:
        calls.append((binary, args))
        return CommandResult((str(binary), *args), 0, "/opt/u/.cache/uv\n", "", False)

    targets = resolve_targets(
        "uv",
        platform="linux",
        env={"HOME": "/opt/u"},
        which=lambda name: "/usr/bin/uv",
        run=run,
    )
    assert calls == [(Path("/usr/bin/uv"), ("cache", "dir"))]
    assert len(targets) == 1
    assert (targets[0].tool, targets[0].key) == ("uv", "cache")
    assert targets[0].path == Path("/opt/u/.cache/uv")
    assert targets[0].redirected is False


def test_resolve_npm_redirect_on_env_override() -> None:
    targets = resolve_targets(
        "npm",
        platform="linux",
        env={"HOME": "/opt/u", "npm_config_cache": "/custom/npm"},
        which=lambda name: "/usr/bin/npm",
        run=_fake_run("/opt/u/.npm\n"),
    )
    assert len(targets) == 1
    assert targets[0].redirected is True
    assert "redirected" in targets[0].reason


def test_resolve_go_parses_two_lines_in_order() -> None:
    targets = resolve_targets(
        "go",
        platform="linux",
        env={"HOME": "/opt/u"},
        which=lambda name: "/usr/local/go/bin/go",
        run=_fake_run("/opt/u/.cache/go-build\n/opt/u/go/pkg/mod\n"),
    )
    assert [target.key for target in targets] == ["gocache", "gomodcache"]
    assert [target.path for target in targets] == [
        Path("/opt/u/.cache/go-build"),
        Path("/opt/u/go/pkg/mod"),
    ]
    assert all(not target.redirected for target in targets)


@pytest.mark.parametrize("tool", ["uv", "npm", "go"])
def test_resolve_missing_binary_returns_empty(tool: str) -> None:
    assert resolve_targets(tool, platform="linux", env={}, which=lambda name: None) == ()


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(("uv",), -1, "", "", True),
        CommandResult(("uv",), 1, "", "boom", False),
    ],
)
def test_resolve_timeout_or_nonzero_returns_empty(result: CommandResult) -> None:
    assert (
        resolve_targets(
            "uv",
            platform="linux",
            env={"HOME": "/opt/u"},
            which=lambda name: "/usr/bin/uv",
            run=lambda *args, **kwargs: result,
        )
        == ()
    )


def test_resolve_unsupported_platform_returns_empty() -> None:
    def fail_run(*args: object, **kwargs: object) -> CommandResult:
        raise AssertionError("run must not be called on an unsupported platform")

    assert (
        resolve_targets(
            "uv",
            platform="aix",
            env={"HOME": "/opt/u"},
            which=lambda name: "/usr/bin/uv",
            run=fail_run,
        )
        == ()
    )


@pytest.mark.parametrize("tool", ["bun", "cargo", "vscode"])
def test_resolve_unsupported_tool_returns_empty(tool: str) -> None:
    def fail_run(*args: object, **kwargs: object) -> CommandResult:
        raise AssertionError("run must not be called for an unsupported tool")

    assert (
        resolve_targets(
            tool,
            platform="linux",
            env={},
            which=lambda name: "/usr/bin/tool",
            run=fail_run,
        )
        == ()
    )


def test_resolve_records_lstat_identity(tmp_path: Path) -> None:
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    stat = os.lstat(cache)
    targets = resolve_targets(
        "uv",
        platform="linux",
        env={"HOME": str(tmp_path)},
        which=lambda name: "/usr/bin/uv",
        run=_fake_run(f"{cache}\n"),
    )
    assert targets[0].path == cache
    assert targets[0].redirected is True
    assert targets[0].identity == (stat.st_dev, stat.st_ino, stat.st_mode)


def test_resolve_identity_none_when_target_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing-cache"
    targets = resolve_targets(
        "uv",
        platform="linux",
        env={"HOME": str(tmp_path)},
        which=lambda name: "/usr/bin/uv",
        run=_fake_run(f"{missing}\n"),
    )
    assert targets[0].identity is None
    assert "does not exist" in targets[0].reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script fixture")
def test_fake_executable_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    script = bin_dir / "uv"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{cache}'\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    stat = os.lstat(cache)

    targets = resolve_targets("uv", platform="linux", env={"HOME": str(tmp_path)})
    assert len(targets) == 1
    assert targets[0].path == cache
    assert targets[0].redirected is True
    assert targets[0].identity == (stat.st_dev, stat.st_ino, stat.st_mode)
