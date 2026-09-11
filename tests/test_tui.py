from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, Static

from space_scout.config import Config
from space_scout.models import Entry, ScanOptions, ScanSnapshot, ScanWarning
from space_scout.policy import Policy
from space_scout.scanner import scan
from space_scout.tui import BrowseApp


def table_text(app):
    table = app.query_one("#entries", DataTable)
    return "\n".join(" ".join(str(cell) for cell in table.get_row_at(i)) for i in range(table.row_count))


def column_labels(app):
    table = app.query_one("#entries", DataTable)
    return tuple(str(column.label) for column in table.columns.values())


@pytest.mark.asyncio
async def test_browse_shows_skipped_status():
    snapshot = ScanSnapshot(Path("/root"), (
        Entry(Path("/root/Applications"), "Applications", "skipped", 0, 0, (), "system directory"),
    ), ())
    app = BrowseApp(snapshot, None, None)
    async with app.run_test(size=(100, 30)) as pilot:
        assert "SKIPPED" in table_text(app)
        assert "system directory" in str(app.query_one("#details", Static).render())
        await pilot.press("q")
    assert app.return_value == 0


@pytest.mark.asyncio
async def test_initial_warning_snapshot_shows_warning_and_clean_rescan_clears_exit_code(tmp_path, monkeypatch):
    warning = ScanWarning(tmp_path, "scan_error", "initial issue")
    clean = ScanSnapshot(tmp_path, (), ())
    monkeypatch.setattr("space_scout.tui.scan_for_browse", lambda *args: clean)
    app = BrowseApp(ScanSnapshot(tmp_path, (), (warning,)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        assert str(app.query_one("#status", Static).render()).startswith("WARNING ·")
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert str(app.query_one("#status", Static).render()).startswith("READY ·")
        await pilot.press("q")
    assert app.return_value == 0


@pytest.mark.asyncio
async def test_rescan_completion_surfaces_scan_warnings(monkeypatch, tmp_path):
    from space_scout.models import ScanWarning

    warning = ScanWarning(tmp_path, "scan_error", "iteration failed")
    replacement = ScanSnapshot(tmp_path, (), (warning,))
    monkeypatch.setattr("space_scout.tui.scan_for_browse", lambda *args: replacement)
    app = BrowseApp(ScanSnapshot(tmp_path, ()), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#status", Static).render())
        assert status.startswith("WARNING · Scan complete · 1 warnings")
        await pilot.press("q")
    assert app.return_value == 2


@pytest.mark.asyncio
async def test_inspector_shows_snapshot_sizes_class_status_and_policy_reason(tmp_path):
    cleanable = Entry(tmp_path / "cache.bin", "cache.bin", "file", 2048, 4096)
    skipped = Entry(tmp_path / "private", "private", "skipped", 0, 0, (), "policy: protected")
    app = BrowseApp(
        ScanSnapshot(tmp_path, (cleanable, skipped)),
        Policy(tmp_path, (), (tmp_path / "private",)),
        Config({}, (), {str(cleanable.path): "cache"}),
    )
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert str(cleanable.path) in details
        assert "On disk: 4096 bytes" in details
        assert "Logical: 2048 bytes" in details
        assert "Class: cache" in details
        assert "Status: CLEANABLE" in details
        await pilot.press("j")
        details = str(app.query_one("#details", Static).render())
        assert str(skipped.path) in details
        assert "Status: SKIPPED" in details
        assert "policy: protected" in details
        await pilot.press("q")


@pytest.mark.asyncio
async def test_filter_matches_name_class_and_status(tmp_path):
    archive = Entry(tmp_path / "old.zip", "old.zip", "file", 100, 100)
    skipped = Entry(tmp_path / "private", "private", "skipped", 0, 0, (), "protected")
    app = BrowseApp(
        ScanSnapshot(tmp_path, (archive, skipped)), Policy(tmp_path, (), (tmp_path / "private",)),
        Config({}, (), {str(archive.path): "archive"}),
    )
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("/")
        app.screen.query_one(Input).value = "archive"
        await pilot.press("enter")
        assert "old.zip" in table_text(app) and "private" not in table_text(app)
        await pilot.press("/")
        app.screen.query_one(Input).value = "skipped"
        await pilot.press("enter")
        assert "private" in table_text(app) and "old.zip" not in table_text(app)
        await pilot.press("q")


def tree(root):
    child = Entry(root / "z-dir" / "child.py", "child.py", "file", 2048, 4096)
    directory = Entry(root / "z-dir", "z-dir", "directory", 2048, 4096, (child,))
    small = Entry(root / "a.zip", "a.zip", "file", 10, None)
    return ScanSnapshot(root, (small, directory))


@pytest.mark.asyncio
async def test_keyboard_navigation_expand_sort_filter_preserves_snapshot(tmp_path):
    snapshot = tree(tmp_path)
    app = BrowseApp(snapshot, Policy(tmp_path, (), ()), Config({}, (), {str(tmp_path / "a.zip"): "archive"}))
    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one("#entries", DataTable)
        assert "z-dir" in str(table.get_row_at(0))
        await pilot.press("enter")
        assert table.row_count == 3
        await pilot.press("j")
        assert "child.py" in str(app.query_one("#details", Static).render())
        await pilot.press("k", "enter")
        assert table.row_count == 2
        await pilot.press("s")
        assert "a.zip" in str(table.get_row_at(0))
        await pilot.press("up", "down")
        assert table.cursor_row == 1
        await pilot.press("/")
        app.screen.query_one(Input).value = "archive"
        await pilot.press("enter")
        assert table.row_count == 1
        assert "a.zip" in table_text(app)
        assert app.snapshot is snapshot
        await pilot.press("q")


@pytest.mark.asyncio
async def test_compact_resize_and_bounded_bars(tmp_path):
    app = BrowseApp(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        assert app.query_one("#details").display
        table = app.query_one("#entries", DataTable)
        assert "KiB" in table_text(app)
        assert "100.0%" not in table_text(app)
        assert all(str(cell).count("█") <= 20 for i in range(table.row_count) for cell in table.get_row_at(i))
        await pilot.resize_terminal(app.NARROW_WIDTH - 1, 24)
        assert not app.query_one("#details").display
        await pilot.press("j", "t")
        assert str(tmp_path / "a.zip") in str(app.screen.query_one("#prompt", Static).render())
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_inspector_shortcut_toggles_the_wide_pane_and_compact_layout(tmp_path):
    app = BrowseApp(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = app.query_one("#details", Static)
        assert details.display
        await pilot.press("i")
        assert not details.display
        await pilot.press("i")
        assert details.display
        await pilot.resize_terminal(app.NARROW_WIDTH - 1, 24)
        assert not details.display
        assert app.query_one("#entries", DataTable).display
        await pilot.press("i")
        assert details.display
        assert "Path:" in str(details.render())
        assert not app.query_one("#entries", DataTable).display
        await pilot.press("i")
        assert not details.display
        assert app.query_one("#entries", DataTable).display
        await pilot.press("q")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("width", "labels"),
    [
        (80, ("Name", "On disk")),
        (100, ("Name", "On disk", "Status")),
        (140, ("Name", "On disk", "Logical", "Class", "Status", "Risk")),
    ],
)
async def test_adaptive_list_uses_exact_columns_and_aligned_byte_cells(tmp_path, width, labels):
    first = Entry(tmp_path / "very-long-name-that-should-be-truncated", "very-long-name-that-should-be-truncated", "file", 2048, 4096)
    second = Entry(tmp_path / "tiny", "tiny", "file", 1, None)
    app = BrowseApp(ScanSnapshot(tmp_path, (first, second)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(width, 30)) as pilot:
        table = app.query_one("#entries", DataTable)
        details = app.query_one("#details", Static)
        assert column_labels(app) == labels
        assert "Name" in column_labels(app)
        assert details.display == (width >= app.NARROW_WIDTH)
        name_column = next(column for column in table.columns.values() if str(column.label) == "Name")
        on_disk_column = next(column for column in table.columns.values() if str(column.label) == "On disk")
        assert name_column.width < len(first.name)
        assert name_column.width <= 32
        on_disk_cells = [str(table.get_row_at(index)[1]).strip() for index in range(table.row_count)]
        assert len({len(str(table.get_row_at(index)[1])) for index in range(table.row_count)}) == 1
        assert on_disk_cells == ["4.0 KiB", "—"]
        assert on_disk_column.width >= len("On disk")
        if "Logical" in labels:
            logical_cells = [str(table.get_row_at(index)[2]).strip() for index in range(table.row_count)]
            assert logical_cells == ["2.0 KiB", "1.0 B"]
            assert len({len(str(table.get_row_at(index)[2])) for index in range(table.row_count)}) == 1
        await pilot.press("q")


@pytest.mark.asyncio
async def test_risk_column_shows_safe_review_protected(tmp_path, monkeypatch):
    monkeypatch.setattr("space_scout.tui.default_tool_present", lambda tool: True)
    uv = Entry(tmp_path / ".cache" / "uv", "uv", "directory", 2048, 4096)
    npm = Entry(tmp_path / ".npm", ".npm", "directory", 1024, 2048)
    npmrc = Entry(tmp_path / ".npmrc", ".npmrc", "file", 100, 100)
    app = BrowseApp(ScanSnapshot(tmp_path, (uv, npm, npmrc)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(140, 30)) as pilot:
        assert column_labels(app) == ("Name", "On disk", "Logical", "Class", "Status", "Risk")
        table = app.query_one("#entries", DataTable)
        by_name = {
            str(table.get_row_at(i)[0]).split()[-1]: str(table.get_row_at(i)[5]).strip()
            for i in range(table.row_count)
        }
        assert by_name["uv"] == "SAFE"
        assert by_name[".npm"] == "REVIEW"
        assert by_name[".npmrc"] == "PROTECTED"
        await pilot.press("q")


@pytest.mark.asyncio
async def test_inspector_shows_cleanup_advice_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("space_scout.tui.default_tool_present", lambda tool: True)
    uv = Entry(tmp_path / ".cache" / "uv", "uv", "directory", 2048, 4096)
    app = BrowseApp(ScanSnapshot(tmp_path, (uv,)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "Cleanup: cache" in details
        assert "Risk: SAFE" in details
        assert "Assessment: assessed" in details
        assert "Method: uv cache prune" in details
        assert "Impact:" in details
        assert "Reason:" in details
        assert "Target: —" in details
        assert "Estimated reclaimable: 2.0 KiB (scan estimate)" in details
        await pilot.press("q")


@pytest.mark.asyncio
async def test_ready_status_includes_safe_tier_metric(tmp_path, monkeypatch):
    monkeypatch.setattr("space_scout.tui.default_tool_present", lambda tool: True)
    uv = Entry(tmp_path / ".cache" / "uv", "uv", "directory", 2048, 4096)
    npm = Entry(tmp_path / ".npm", ".npm", "directory", 1024, 2048)
    app = BrowseApp(ScanSnapshot(tmp_path, (uv, npm)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        status = str(app.query_one("#status", Static).render())
        assert "safe-tier 2.0 KiB of 3.0 KiB reclaimable" in status
        await pilot.press("q")


@pytest.mark.asyncio
async def test_resolver_results_memoized_per_tool(tmp_path):
    calls = []

    def fake_resolver(tool):
        calls.append(tool)
        return ()

    uv_one = Entry(tmp_path / ".cache" / "uv", "uv", "directory", 2048, 4096)
    uv_two = Entry(tmp_path / "local" / "uv" / "cache", "cache", "directory", 1024, 2048)
    app = BrowseApp(
        ScanSnapshot(tmp_path, (uv_one, uv_two)),
        Policy(tmp_path, (), ()),
        Config({}, (), {}),
        tool_present=lambda tool: True,
        resolve_targets=fake_resolver,
    )
    async with app.run_test(size=(140, 30)) as pilot:
        assert calls == ["uv"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_wide_table_fits_its_pane_at_exact_120_columns(tmp_path):
    app = BrowseApp(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one("#entries", DataTable)
        assert sum(column.width for column in table.columns.values()) + len(table.columns) - 1 <= table.size.width
        await pilot.press("q")


@pytest.mark.asyncio
async def test_resize_to_narrow_wide_pane_rebudgets_long_labels(tmp_path):
    entry = Entry(tmp_path / ("n" * 180), "n" * 180, "file", 1, 1)
    config = Config({}, (), {str(entry.path): "c" * 180})
    app = BrowseApp(ScanSnapshot(tmp_path, (entry,)), Policy(tmp_path, (), ()), config)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.resize_terminal(120, 30)
        table = app.query_one("#entries", DataTable)
        assert table.virtual_size.width <= table.size.width
        assert table.max_scroll_x == 0
        assert "Name" in column_labels(app)
        assert "On disk" in column_labels(app)
        await pilot.press("q")


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [90, 100, 119, 120, 140])
async def test_pathological_labels_never_overflow_datatable_pane(tmp_path, width):
    entries = (
        Entry(tmp_path / ("n" * 180), "n" * 180, "file", 1, 1, (), "warning"),
    )
    config = Config({}, (), {str(entries[0].path): "c" * 180})
    app = BrowseApp(ScanSnapshot(tmp_path, entries), Policy(tmp_path, (), ()), config)
    async with app.run_test(size=(width, 30)) as pilot:
        table = app.query_one("#entries", DataTable)
        assert table.virtual_size.width <= table.size.width
        assert table.max_scroll_x == 0
        assert "Name" in column_labels(app)
        assert "On disk" in column_labels(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_sort_cycles_all_adaptive_columns_and_reverses_current_direction(tmp_path):
    entries = (
        Entry(tmp_path / "beta", "beta", "file", 10, 200, warning="warning"),
        Entry(tmp_path / "alpha", "alpha", "file", 30, 100),
        Entry(tmp_path / "gamma", "gamma", "file", 20, None),
    )
    config = Config({}, (), {str(entries[0].path): "z-class", str(entries[1].path): "a-class", str(entries[2].path): "m-class"})
    app = BrowseApp(ScanSnapshot(tmp_path, entries), Policy(tmp_path, (), ()), config)
    async with app.run_test(size=(140, 30)) as pilot:
        assert app._sort_column == "on_disk"
        assert [entry.name for entry, _ in app._visible] == ["beta", "alpha", "gamma"]
        await pilot.press("s")
        assert app._sort_column == "name"
        assert [entry.name for entry, _ in app._visible] == ["alpha", "beta", "gamma"]
        await pilot.press("s")
        assert app._sort_column == "class"
        assert [entry.name for entry, _ in app._visible] == ["alpha", "gamma", "beta"]
        await pilot.press("s")
        assert app._sort_column == "status"
        assert [entry.name for entry, _ in app._visible] == ["alpha", "gamma", "beta"]
        await pilot.press("S")
        assert app._sort_reverse
        assert [entry.name for entry, _ in app._visible] == ["beta", "gamma", "alpha"]
        await pilot.press("q")


@pytest.mark.asyncio
async def test_unlock_requires_exact_word_and_never_grants_trash(tmp_path, monkeypatch):
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "secret").write_text("data")
    policy = Policy(tmp_path, (), (protected,))
    snapshot = ScanSnapshot(tmp_path, (Entry(protected, protected.name, "skipped", 0, None, warning="protected"),))
    calls = []
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: calls.append(paths))
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("enter")
        assert "secret" not in table_text(app)
        await pilot.press("u")
        assert str(protected) in str(app.screen.query_one("#prompt", Static).render())
        app.screen.query_one(Input).value = "UNLOCK"
        await pilot.press("enter")
        assert "SKIPPED" in table_text(app)
        await pilot.press("u")
        app.screen.query_one(Input).value = "unlock"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "UNLOCKED" in table_text(app)
        assert app.snapshot.entries[0].children[0].name == "secret"
        await pilot.press("t")
        assert not app.screen.query(Input)
        assert calls == []
        await pilot.press("q")


@pytest.mark.asyncio
async def test_trash_confirmation_cancel_failure_and_no_automatic_rescan(tmp_path, monkeypatch):
    from space_scout.trash import TrashResult
    snapshot = tree(tmp_path)
    calls = []

    def trash(paths, **kwargs):
        calls.extend(paths)
        return (TrashResult(paths[0], False, "permission denied"),)

    monkeypatch.setattr("space_scout.tui.trash_many", trash)
    app = BrowseApp(snapshot, Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("t")
        prompt = str(app.screen.query_one("#prompt", Static).render())
        assert str(tmp_path / "z-dir") in prompt
        assert "logical" in prompt and "2048" in prompt and "On disk:" in prompt and "4096" in prompt and "Class:" in prompt
        await pilot.press("escape")
        assert not calls
        await pilot.press("t")
        app.screen.query_one(Input).value = "yes"
        await pilot.press("enter")
        assert not calls
        await pilot.press("t")
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [tmp_path / "z-dir"]
        status = str(app.query_one("#status", Static).render())
        assert "permission denied" in status and "r to rescan" in status
        assert app.snapshot is snapshot
        await pilot.press("q")


@pytest.mark.asyncio
async def test_trash_rejects_protected_credential_row_without_prompt(tmp_path):
    npmrc = Entry(tmp_path / ".npmrc", ".npmrc", "file", 100, 100)
    app = BrowseApp(ScanSnapshot(tmp_path, (npmrc,)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("t")
        assert not app.screen.query(Input)
        status = str(app.query_one("#status", Static).render())
        assert "SKIPPED · Trash rejected" in status
        assert "npm credentials" in status
        await pilot.press("q")


@pytest.mark.asyncio
async def test_trash_status_includes_moved_and_freed_summary(tmp_path, monkeypatch):
    from space_scout.trash import TrashResult

    def trash(paths, **kwargs):
        return (TrashResult(paths[0], True, "moved to trash", moved_bytes=2048, freed_bytes=1024),)

    monkeypatch.setattr("space_scout.tui.trash_many", trash)
    snapshot = tree(tmp_path)
    app = BrowseApp(snapshot, Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("t")
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#status", Static).render())
        assert "moved" in status and "freed" in status
        await pilot.press("q")


@pytest.mark.asyncio
async def test_unknown_on_disk_shows_as_unknown_in_details_and_trash_prompt(tmp_path):
    entry = Entry(tmp_path / "mystery.bin", "mystery.bin", "file", 512, None)
    app = BrowseApp(ScanSnapshot(tmp_path, (entry,)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "On disk: — (unknown)" in details
        await pilot.press("t")
        prompt = str(app.screen.query_one("#prompt", Static).render())
        assert "On disk: — (unknown)" in prompt
        assert "Estimated size" in prompt
        await pilot.press("escape", "q")


@pytest.mark.asyncio
async def test_rescan_replaces_snapshot_only_on_r(tmp_path):
    from space_scout.tui import scan_for_browse
    policy = Policy(tmp_path, (), ())
    original = scan_for_browse(tmp_path, policy)
    app = BrowseApp(original, policy, Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        (tmp_path / "new.txt").write_text("new")
        assert "new.txt" not in table_text(app)
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "new.txt" in table_text(app)
        assert app.snapshot is not original
        assert not original.entries
        await pilot.press("q")




@pytest.mark.asyncio
async def test_rescan_preserves_sort_filter_and_expanded_state(tmp_path, monkeypatch):
    original = tree(tmp_path)
    replacement = tree(tmp_path)
    monkeypatch.setattr("space_scout.tui.scan_for_browse", lambda *_args: replacement)
    app = BrowseApp(original, Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("enter", "s", "/")
        app.screen.query_one(Input).value = "z-dir"
        await pilot.press("enter", "r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.snapshot is replacement
        assert app._sort_column == "name"
        assert app.filter_text == "z-dir"
        assert tmp_path / "z-dir" in app.expanded
        assert "Scan complete" in str(app.query_one("#status", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_rescan_failure_shows_recovery_action(tmp_path, monkeypatch):
    app = BrowseApp(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {}))
    monkeypatch.setattr("space_scout.tui.scan_for_browse", lambda *_args: (_ for _ in ()).throw(OSError("disk unavailable")))
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        status = str(app.query_one("#status", Static).render())
        assert "Scan failed" in status and "disk unavailable" in status and "r to retry" in status
        assert not app._busy
        await pilot.press("q")


def test_browse_scan_never_traverses_protected_or_excluded(tmp_path, monkeypatch):
    import os

    from space_scout.tui import scan_for_browse
    protected = tmp_path / "protected"
    excluded = tmp_path / "excluded"
    allowed = tmp_path / "allowed"
    for folder in (protected, excluded, allowed):
        folder.mkdir()
        (folder / "child").write_text("abc")
    visited = []
    real_scandir = os.scandir

    def scandir(path):
        visited.append(Path(path))
        assert Path(path) not in (protected, excluded)
        return real_scandir(path)

    monkeypatch.setattr("space_scout.scanner.os.scandir", scandir)
    snapshot = scan_for_browse(tmp_path, Policy(tmp_path, (excluded,), (protected,)))
    by_name = {entry.name: entry for entry in snapshot.entries}
    assert by_name["allowed"].logical_bytes == 3
    assert by_name["protected"].kind == by_name["excluded"].kind == "skipped"
    assert allowed in visited
    assert not any(w.code == "depth_limited" for w in snapshot.warnings)


def test_browse_scan_blocks_protected_root(tmp_path, monkeypatch):
    from space_scout.tui import scan_for_browse
    monkeypatch.setattr("space_scout.scanner.os.scandir", lambda path: pytest.fail("blocked root was scanned"))
    snapshot = scan_for_browse(tmp_path, Policy(tmp_path, (), (tmp_path,)))
    assert snapshot.entries[0].kind == "skipped"


@pytest.mark.asyncio
async def test_parent_with_blocked_descendant_stays_expandable_and_reports_cleanup_rejection(tmp_path):
    root = tmp_path / "tree"
    parent = root / "parent"
    blocked = parent / "blocked"
    blocked.mkdir(parents=True)
    (parent / "allowed.txt").write_text("safe")
    (blocked / "secret.txt").write_text("keep")
    policy = Policy(root, (), (blocked,))
    snapshot = scan(ScanOptions(root), policy)
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "Status: BLOCKED" in details
        assert "contains protected or excluded path" in details
        await pilot.press("enter")
        assert "allowed.txt" in table_text(app)
        assert "blocked" in table_text(app)
        await pilot.press("t")
        assert not app.screen.query(Input)
        status = str(app.query_one("#status", Static).render())
        assert "rejected" in status
        assert "contains protected or excluded path" in status
        await pilot.press("/")
        app.screen.query_one(Input).value = "allowed"
        await pilot.press("enter")
        assert "allowed.txt" in table_text(app)
        assert "secret.txt" not in table_text(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_config_minimum_and_classification_override(tmp_path):
    snapshot = tree(tmp_path)
    config = Config({}, (), {str(tmp_path / "z-dir"): "cache"}, "name", 20)
    app = BrowseApp(snapshot, Policy(tmp_path, (), ()), config)
    async with app.run_test(size=(120, 30)) as pilot:
        assert "a.zip" not in table_text(app)
        assert "cache" in table_text(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_trash_rejects_ancestor_of_excluded_or_protected_path(tmp_path, monkeypatch):
    snapshot = tree(tmp_path)
    nested = tmp_path / "z-dir" / "child.py"
    policy = Policy(tmp_path, (nested,), ())
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: pytest.fail("excluded descendant trashed"))
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test() as pilot:
        await pilot.press("t")
        assert not app.screen.query(Input)
        assert "rejected" in str(app.query_one("#status", Static).render())
        await pilot.press("q")


@pytest.mark.asyncio
async def test_successful_trash_cannot_repeat_or_trash_snapshot_descendants(tmp_path, monkeypatch):
    from space_scout.trash import TrashResult
    calls = []
    def trash(paths, **kwargs):
        calls.extend(paths)
        return (TrashResult(paths[0], True, "moved to trash"),)
    monkeypatch.setattr("space_scout.tui.trash_many", trash)
    snapshot = tree(tmp_path)
    app = BrowseApp(snapshot, Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("enter", "t")
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "TRASHED" in table_text(app)
        await pilot.press("t")
        assert not app.screen.query(Input)
        await pilot.press("j", "t")
        assert not app.screen.query(Input)
        assert calls == [tmp_path / "z-dir"]
        assert app.snapshot is snapshot
        await pilot.press("q")


@pytest.mark.asyncio
async def test_filter_minimum_size_and_clear(tmp_path):
    app = BrowseApp(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test() as pilot:
        await pilot.press("/")
        app.screen.query_one(Input).value = "min:100"
        await pilot.press("enter")
        assert "z-dir" in table_text(app)
        assert "a.zip" not in table_text(app)
        await pilot.press("/")
        app.screen.query_one(Input).value = ""
        await pilot.press("enter")
        assert "a.zip" in table_text(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_details_escape_untrusted_filename_and_markup(tmp_path):
    name = "[bold]file\nline\x1b.txt"
    entry = Entry(tmp_path / name, name, "file", 1, None)
    app = BrowseApp(ScanSnapshot(tmp_path, (entry,)), Policy(tmp_path, (), ()), Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "file\\nline\\x1b.txt" in details
        assert "[bold]" in table_text(app)
        await pilot.press("q")


@pytest.mark.asyncio
async def test_excluded_row_cannot_unlock_or_expand(tmp_path):
    snapshot = tree(tmp_path)
    app = BrowseApp(snapshot, Policy(tmp_path, (tmp_path / "z-dir",), ()), Config({}, (), {}, minimum_bytes=10000))
    async with app.run_test(size=(100, 30)) as pilot:
        assert "SKIPPED" in table_text(app)
        await pilot.press("enter", "u")
        assert "child.py" not in table_text(app)
        assert not app.screen.query(Input)
        await pilot.press("q")


def test_run_browse_returns_app_exit_code(tmp_path, monkeypatch):
    from space_scout.tui import run_browse
    monkeypatch.setattr(BrowseApp, "run", lambda self: 2)
    assert run_browse(tree(tmp_path), Policy(tmp_path, (), ()), Config({}, (), {})) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("browse_scan", [True, False])
async def test_initial_browse_keeps_symlink_loop_and_siblings(tmp_path, monkeypatch, browse_scan, symlink_supported):
    from space_scout.models import ScanOptions
    from space_scout.scanner import scan
    from space_scout.tui import scan_for_browse

    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    (tmp_path / "sibling.txt").write_text("safe")
    policy = Policy(tmp_path, (), ())
    snapshot = scan_for_browse(tmp_path, policy) if browse_scan else scan(ScanOptions(tmp_path))
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: pytest.fail("unsafe path trashed"))
    app = BrowseApp(snapshot, policy, Config({}, (), {}, sort_key="name"))
    async with app.run_test(size=(120, 30)) as pilot:
        assert "loop" in table_text(app) and "sibling.txt" in table_text(app)
        assert "SKIPPED" in table_text(app)
        assert "policy" in str(app.query_one("#details", Static).render()).lower()
        await pilot.press("t", "u", "enter", "s")
        assert not app.screen.query(Input)
        assert app.snapshot is snapshot
        await pilot.press("q")
    if browse_scan:
        entry = next(entry for entry in snapshot.entries if entry.path == loop)
        assert entry.kind == "skipped" and entry.warning
        assert any(w.path == loop and w.code == "policy_error" for w in snapshot.warnings)
        assert app.return_value == 2


@pytest.mark.asyncio
async def test_rescan_keeps_new_symlink_loop_and_scans_siblings(tmp_path, symlink_supported):
    from space_scout.tui import scan_for_browse

    policy = Policy(tmp_path, (), ())
    original = scan_for_browse(tmp_path, policy)
    app = BrowseApp(original, policy, Config({}, (), {}, sort_key="name"))
    async with app.run_test(size=(100, 30)) as pilot:
        loop = tmp_path / "loop"
        loop.symlink_to("loop")
        (tmp_path / "sibling.txt").write_text("safe")
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "loop" in table_text(app) and "sibling.txt" in table_text(app)
        assert "SKIPPED" in table_text(app)
        assert any(w.path == loop and w.code == "policy_error" for w in app.snapshot.warnings)
        assert app.snapshot is not original and original.entries == ()
        await pilot.press("q")
    assert app.return_value == 2


@pytest.mark.asyncio
async def test_trash_recheck_rejects_path_replaced_by_symlink_loop(tmp_path, monkeypatch, symlink_supported):
    from space_scout.tui import scan_for_browse

    path = tmp_path / "item"
    path.write_text("safe")
    policy = Policy(tmp_path, (), ())
    snapshot = scan_for_browse(tmp_path, policy)
    monkeypatch.setattr("space_scout.tui.trash_many", lambda paths: pytest.fail("unsafe path trashed"))
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test() as pilot:
        await pilot.press("t")
        path.unlink()
        path.symlink_to("item")
        app.screen.query_one(Input).value = "trash"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "rejected" in str(app.query_one("#status", Static).render())
        assert app.snapshot is snapshot
        await pilot.press("q")
