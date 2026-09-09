"""Regressions for the remaining whole-branch review findings."""

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from space_scout.classify import classify
from space_scout.cli import main
from space_scout.config import Config, load_config, save_config
from space_scout.models import ScanOptions
from space_scout.policy import Policy
from space_scout.scanner import scan
from space_scout.trash import TrashResult
from space_scout.tui import BrowseApp


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["item", "item/child"])
async def test_tui_reloads_persisted_exclusions_after_confirmation(tmp_path, monkeypatch, boundary):
    settings = tmp_path / "settings.toml"
    monkeypatch.setattr("space_scout.config.config_path", lambda: settings)
    root = tmp_path / "tree"
    item = root / "item"
    item.mkdir(parents=True)
    (item / "child").write_text("keep")
    policy = Policy(root, (), (root / "protected",))
    snapshot = scan(ScanOptions(root), policy)
    calls = []
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: calls.extend(paths) or ())
    app = BrowseApp(snapshot, policy, load_config())
    async with app.run_test() as pilot:
        await pilot.press("t")
        assert app.screen.query(Input)
        assert main(["config", "add-exclusion", str(root / boundary)]) == 0
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == []
        assert "rejected" in str(app.query_one("#status", Static).render())
        assert app.policy.protected_roots == policy.protected_roots
        assert app.snapshot is snapshot
        await pilot.press("q")


@pytest.mark.asyncio
async def test_tui_fails_closed_when_final_config_reload_fails(tmp_path, monkeypatch):
    item = tmp_path / "item"
    item.write_text("keep")
    policy = Policy(tmp_path, (), ())
    app = BrowseApp(scan(ScanOptions(tmp_path), policy), policy, Config({}, (), {}))
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: pytest.fail("trashed with unreadable config"))
    # Patch the loader's I/O boundary so this also fails before tui imports it.
    monkeypatch.setattr("space_scout.config.config_path", lambda: tmp_path / "bad.toml")
    async with app.run_test() as pilot:
        await pilot.press("t")
        (tmp_path / "bad.toml").write_text("invalid = [")
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#status", Static).render())
        assert "rejected" in status and "configuration" in status
        assert not app._busy
        await pilot.press("q")
    assert app.return_value == 3


def test_broken_symlink_warns_without_scanning_target(tmp_path, monkeypatch, symlink_supported):
    missing = tmp_path / "missing"
    link = tmp_path / "broken"
    link.symlink_to("missing")
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "sibling").write_text("safe")
    original_scandir = os.scandir
    visited = []

    def scandir(path):
        visited.append(Path(path))
        return original_scandir(path)

    monkeypatch.setattr("space_scout.scanner.os.scandir", scandir)
    snapshot = scan(ScanOptions(tmp_path))
    entries = {entry.name: entry for entry in snapshot.entries}
    assert entries["broken"].kind == "symlink"
    assert entries["broken"].logical_bytes == link.lstat().st_size
    assert entries["broken"].warning
    assert entries["broken"].children == entries["linked-directory"].children == ()
    assert entries["sibling"].logical_bytes == 4
    assert any(w.path == link and w.code == "broken_symlink" for w in snapshot.warnings)
    assert visited == [tmp_path]
    assert not missing.exists()


@pytest.mark.parametrize("override", [None, "model-cache"])
def test_cli_trash_preview_includes_shared_classification(tmp_path, monkeypatch, capsys, isolated_cli, override):
    target = tmp_path / "backup.zip"
    target.write_bytes(b"12345")
    # macOS temp paths resolve under /private, a built-in system label.
    expected = override if override else classify(target, "file", 5)
    save_config(Config({}, (), {str(target): override} if override else {}))
    calls = []

    def trash(paths):
        assert f"Class: {expected}" in capsys.readouterr().out
        calls.extend(paths)
        return (TrashResult(target, True, "moved"),)

    monkeypatch.setattr("space_scout.cli.trash_many", trash)
    assert main(["trash", str(target), "--yes"]) == 0
    assert calls == [target]


def test_modified_range_includes_nested_entries_and_is_immutable(tmp_path):
    folder = tmp_path / "folder"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    oldest = nested / "old"
    newest = folder / "new"
    oldest.write_text("old")
    newest.write_text("new")
    for path, seconds in ((oldest, 946684800), (newest, 1577836800), (nested, 1262304000), (folder, 1262304000)):
        os.utime(path, (seconds, seconds))
    snapshot = scan(ScanOptions(tmp_path))
    entry = snapshot.entries[0]
    assert entry.modified_min_ns == oldest.stat().st_mtime_ns
    assert entry.modified_max_ns == newest.stat().st_mtime_ns
    with pytest.raises(FrozenInstanceError):
        entry.modified_min_ns = 0
    os.utime(oldest, (1704067200, 1704067200))
    assert entry.modified_min_ns == 946684800 * 10**9
    limited = scan(ScanOptions(tmp_path, max_depth=1)).entries[0]
    assert limited.modified_min_ns == limited.modified_max_ns == folder.stat().st_mtime_ns
    assert limited.warning


@pytest.mark.asyncio
async def test_details_show_snapshot_modified_range_without_live_stat(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child"
    child.write_text("data")
    os.utime(child, (946684800, 946684800))
    os.utime(folder, (1577836800, 1577836800))
    policy = Policy(tmp_path, (), ())
    snapshot = scan(ScanOptions(tmp_path), policy)
    # Subsequent changes must not alter the displayed snapshot's timestamps.
    os.utime(child, (1704067200, 1704067200))
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "Modified range" in details and "UTC" in details
        assert "2000-01-01" in details and "2020-01-01" in details
        assert "2024-01-01" not in details
        assert "scanned" in details
        await pilot.press("q")


@pytest.mark.asyncio
async def test_details_show_modified_range_with_nanosecond_precision(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child"
    child.write_text("data")
    policy = Policy(tmp_path, (), ())
    snapshot = scan(ScanOptions(tmp_path), policy)
    entry = snapshot.entries[0]
    object.__setattr__(entry, "modified_min_ns", 946684800_000_000_123)
    object.__setattr__(entry, "modified_max_ns", 946684800_999_999_987)
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "2000-01-01 00:00:00.000000123 UTC" in details
        assert "2000-01-01 00:00:00.999999987 UTC" in details
        await pilot.press("q")
