"""Outcome-based end-to-end acceptance for scan, advice, action, and state change.

Everything is hermetic: fake tool executables live in a temp ``bin`` directory,
the resolver is wired with injected ``which``/``run`` fakes, and Trash moves land
in a temp ``TrashRoot`` instead of the host Trash.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import pytest

from space_scout.cli import main
from space_scout.knowledge import ResolvedTargetLike, build_advice
from space_scout.models import Entry, ScanOptions
from space_scout.output import flatten
from space_scout.policy import Policy, PolicyDecision
from space_scout.resolver import CommandResult, resolve_targets
from space_scout.scanner import scan
from space_scout.trash import trash_one


def _make_fixture_tree(root: Path) -> tuple[Path, Path]:
    """Create ``root/.cache/uv`` and ``root/.npm/_cacache/entry`` with known bytes."""
    uv_dir = root / ".cache" / "uv"
    uv_dir.mkdir(parents=True)
    (uv_dir / "artifact.bin").write_bytes(b"u" * 128)
    npm_dir = root / ".npm"
    entry = npm_dir / "_cacache" / "entry"
    entry.mkdir(parents=True)
    (entry / "blob.bin").write_bytes(b"n" * 256)
    return uv_dir, npm_dir


def _write_fake_tool(bin_dir: Path, name: str, output: Path) -> Path:
    """Write an executable that prints one fixture path, like a tool query would."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _resolver_over(bin_dir: Path, root: Path):
    """Return a resolve_targets-shaped callable backed by the fake executables."""

    def resolver(tool: str) -> tuple[ResolvedTargetLike, ...]:
        resolved = resolve_targets(
            tool,
            platform="linux",
            env={"HOME": str(root)},
            which=lambda name: str(bin_dir / name) if (bin_dir / name).exists() else None,
        )
        return cast(tuple[ResolvedTargetLike, ...], resolved)

    return resolver


def _advice_for(
    path: Path,
    root: Path,
    *,
    platform: str = "linux",
    tool_present=None,
    resolve_targets_fn=None,
):
    """Build advice for one directory as if it were a scanned, policy-approved row."""
    policy = Policy(root, (), ())
    entry = Entry(path, path.name, "directory", 0, None)
    decision = PolicyDecision("scan", "path is eligible for scanning")
    return build_advice(
        entry,
        decision,
        policy,
        platform=platform,
        classification="cache",
        tool_present=tool_present,
        resolve_targets=resolve_targets_fn,
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell script fixture")
def test_scan_advice_action_state_change(tmp_path, monkeypatch, capsys, isolated_cli):
    root = tmp_path / "root"
    uv_dir, npm_dir = _make_fixture_tree(root)
    bin_dir = tmp_path / "bin"
    _write_fake_tool(bin_dir, "uv", uv_dir)
    _write_fake_tool(bin_dir, "npm", npm_dir)

    policy = Policy(root, (), ())
    snapshot = scan(ScanOptions(root), policy)
    rows = flatten(
        snapshot,
        policy,
        platform="linux",
        tool_present=lambda _tool: True,
        resolve_targets=_resolver_over(bin_dir, root),
    )
    by_path = {row.path: row for row in rows}
    uv_row = by_path[str(uv_dir)]
    npm_row = by_path[str(npm_dir)]

    assert uv_row.advice is not None
    assert uv_row.advice.risk == "safe"
    assert uv_row.advice.method == "uv cache prune"
    assert npm_row.advice is not None
    assert npm_row.advice.risk == "review"

    trash_root = tmp_path / "Trash"
    trash_root.mkdir()
    moved: list[Path] = []

    def fake_send2trash(value: str) -> None:
        moved.append(Path(value).rename(trash_root / Path(value).name))

    monkeypatch.setattr("space_scout.trash.send2trash", fake_send2trash)
    monkeypatch.setattr("space_scout.trash.trash_root", lambda path: trash_root)

    assert main(["trash", str(npm_dir), "--yes"]) == 0
    output = capsys.readouterr().out
    assert "moved" in output
    assert "freed" in output
    assert not npm_dir.exists()
    assert (trash_root / ".npm").is_dir()


def test_disk_state_after_trash_move(tmp_path, monkeypatch):
    source = tmp_path / "cache-dir"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"x" * 64)
    trash_root = tmp_path / "Trash"
    trash_root.mkdir()
    monkeypatch.setattr("space_scout.trash.trash_root", lambda path: trash_root)
    monkeypatch.setattr(
        "space_scout.trash.send2trash",
        lambda value: Path(value).rename(trash_root / Path(value).name),
    )

    result = trash_one(source, moved_bytes=64)

    assert result.success
    assert not source.exists()
    assert (trash_root / "cache-dir").is_dir()
    assert (trash_root / "cache-dir" / "payload.bin").read_bytes() == b"x" * 64


def test_preview_target_equals_reresolved_target(tmp_path):
    root = tmp_path / "root"
    uv_dir, _ = _make_fixture_tree(root)

    def run(binary: Path, args: tuple[str, ...], **kwargs: object) -> CommandResult:
        return CommandResult((str(binary), *args), 0, f"{uv_dir}\n", "", False)

    kwargs = {
        "platform": "linux",
        "env": {"HOME": str(root)},
        "which": lambda name: "/usr/bin/uv",
        "run": run,
    }
    first = resolve_targets("uv", **kwargs)
    second = resolve_targets("uv", **kwargs)

    assert [target.path for target in first] == [target.path for target in second]
    assert [target.identity for target in first] == [target.identity for target in second]
    assert first and first[0].path == uv_dir


def test_tool_missing_reports_adapter_missing_with_trash_fallback(tmp_path):
    root = tmp_path / "root"
    uv_dir, _ = _make_fixture_tree(root)

    assert (
        resolve_targets("uv", platform="linux", env={"HOME": str(root)}, which=lambda name: None)
        == ()
    )
    advice = _advice_for(uv_dir, root, tool_present=lambda _tool: False)

    assert advice.assessment_status == "adapter_missing"
    assert advice.method == "Trash"
    assert advice.risk == "review"
    assert advice.target is None


def test_unsupported_platform_reports_adapter_missing(tmp_path):
    root = tmp_path / "root"
    uv_dir, _ = _make_fixture_tree(root)

    def fail_run(*args: object, **kwargs: object) -> CommandResult:
        raise AssertionError("run must not be called on an unsupported platform")

    assert (
        resolve_targets(
            "uv",
            platform="aix",
            env={"HOME": str(root)},
            which=lambda name: "/usr/bin/uv",
            run=fail_run,
        )
        == ()
    )
    # Unsupported platform yields no resolver target; the advice layer reports
    # adapter_missing and falls back to Trash instead of offering a command.
    advice = _advice_for(
        uv_dir,
        root,
        platform="linux",
        tool_present=lambda _tool: False,
        resolve_targets_fn=lambda _tool: (),
    )

    assert advice.assessment_status == "adapter_missing"
    assert advice.method == "Trash"
    assert advice.target is None
