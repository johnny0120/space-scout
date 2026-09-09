import json
import os
from pathlib import Path, PureWindowsPath

import pytest
from textual.widgets import Static

from space_scout.cli import main
from space_scout.config import Config, load_config, save_config
from space_scout.models import Entry, ScanSnapshot
from space_scout.output import flatten
from space_scout.policy import Policy, decide, default_policy
from space_scout.tui import BrowseApp


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    config_file = tmp_path / "settings.toml"
    monkeypatch.setattr("space_scout.config.config_path", lambda: config_file)
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), ()))
    return config_file


def test_cli_scan_gates_descendants_and_uses_persisted_config(tmp_path, monkeypatch, capsys, cli_config):
    root = tmp_path / "scan"
    root.mkdir()
    protected, excluded, allowed = (root / name for name in ("protected", "excluded", "allowed"))
    for folder in (protected, excluded, allowed):
        folder.mkdir()
        (folder / "child.py").write_bytes(b"abc")
    save_config(Config({}, (excluded,), {str(allowed / "child.py"): "archive"}), cli_config)
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), (protected,)))
    real_scandir = os.scandir

    def scandir(path):
        assert Path(path) not in (protected, excluded), "blocked directory was traversed"
        return real_scandir(path)

    monkeypatch.setattr("space_scout.scanner.os.scandir", scandir)
    assert main(["scan", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    rows = {row["path"]: row for row in payload["entries"]}
    assert rows[str(protected)]["kind"] == rows[str(excluded)]["kind"] == "skipped"
    assert rows[str(protected)]["logical_bytes"] == rows[str(excluded)]["logical_bytes"] == 0
    assert rows[str(allowed / "child.py")]["classification"] == "archive"
    assert len(rows) == 4


def test_cli_scan_protected_root_does_not_open_it(tmp_path, monkeypatch, capsys, cli_config):
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), (tmp_path,)))
    monkeypatch.setattr("space_scout.scanner.os.scandir", lambda _: pytest.fail("opened protected root"))
    assert main(["scan", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["kind"] == "skipped"


@pytest.mark.parametrize("boundary_kind", ["protected", "excluded"])
def test_cli_trash_rejects_ancestor_without_sizing(tmp_path, monkeypatch, capsys, cli_config, boundary_kind):
    root = tmp_path / "parent"
    boundary = root / "blocked"
    boundary.mkdir(parents=True)
    (boundary / "child").write_text("keep")
    if boundary_kind == "protected":
        monkeypatch.setattr("space_scout.cli.default_policy", lambda path: Policy(path, (), (boundary,)))
    else:
        save_config(Config({}, (boundary,), {}), cli_config)
    monkeypatch.setattr("space_scout.cli._estimated_size", lambda *args: pytest.fail("sized rejected parent"))
    monkeypatch.setattr("space_scout.cli.trash_many", lambda _: pytest.fail("trashed rejected parent"))
    assert main(["trash", str(root), "--yes"]) == 3
    assert "rejected" in capsys.readouterr().out
    assert (boundary / "child").read_text() == "keep"


def test_cli_trash_final_recheck_after_confirmation(tmp_path, monkeypatch, cli_config):
    path = tmp_path / "item"
    path.write_text("keep")
    monkeypatch.setattr("space_scout.cli.sys.stdin.isatty", lambda: True)

    def confirm(_):
        save_config(Config({}, (path,), {}), cli_config)
        return "trash"

    monkeypatch.setattr("builtins.input", confirm)
    monkeypatch.setattr("space_scout.cli.trash_many", lambda _: pytest.fail("ignored changed exclusion"))
    assert main(["trash", str(path)]) == 3
    assert path.exists()


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_scan_symlink_loop_keeps_siblings(tmp_path, capsys, cli_config, json_output, symlink_supported):
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    (tmp_path / "sibling.txt").write_text("safe")
    assert main(["scan", str(tmp_path), *(["--json"] if json_output else [])]) == 2
    output = capsys.readouterr().out
    if json_output:
        payload = json.loads(output)
        assert any(w["path"] == str(loop) for w in payload["warnings"])
        rows = {row["name"]: row for row in payload["entries"]}
        assert rows["loop"]["kind"] == "skipped"
        assert rows["sibling.txt"]["logical_bytes"] == 4
    else:
        assert "SKIPPED" in output
        assert "Cannot evaluate path policy" in output
        assert "sibling.txt" in output


def test_cli_table_surfaces_empty_scan_warning(tmp_path, capsys, cli_config):
    assert main(["scan", str(tmp_path / "missing")]) == 2
    assert "root_error" in capsys.readouterr().out


def test_cli_add_exclusion_is_absolute_after_chdir(tmp_path, monkeypatch, cli_config):
    monkeypatch.chdir(tmp_path)
    assert main(["config", "add-exclusion", "relative"]) == 0
    monkeypatch.chdir(tmp_path.parent)
    config = load_config(cli_config)
    assert config.exclusions == ((tmp_path / "relative").resolve(),)


def test_config_non_bmp_round_trip(tmp_path):
    config = Config({"📁": tmp_path / "🚀"}, (tmp_path / "🛑",), {"📦": "cache🚀\x7f"})
    path = tmp_path / "config.toml"
    save_config(config, path)
    assert load_config(path) == config


def test_windows_policy_actual_locations_and_selected_drive(monkeypatch):
    locations = {"SystemRoot": "C:/WinNT", "ProgramFiles": "E:/Apps",
                 "ProgramFiles(x86)": "F:/Legacy Apps", "ProgramData": "E:/Data"}
    for key, value in locations.items():
        monkeypatch.setenv(key, value)
    policy = default_policy(PureWindowsPath("D:/work"), platform_name="win32")
    for path in (*locations.values(), "D:/Windows", "D:/Program Files", "D:/Program Files (x86)", "D:/ProgramData"):
        assert decide(PureWindowsPath(path) / "child", policy).status == "protected"
    assert decide(PureWindowsPath("D:/work/Windows"), policy).status == "scan"


def test_windows_classification_matches_custom_system_locations(monkeypatch):
    from space_scout.classify import classify
    monkeypatch.setenv("SystemRoot", "C:/WinNT")
    assert classify(PureWindowsPath("C:/WinNT/System32"), "directory", 1) == "system-protected"


@pytest.mark.parametrize("failure", ["policy", "classification"])
def test_output_errors_are_rows_and_json_warnings(tmp_path, monkeypatch, failure):
    from io import StringIO

    from space_scout.models import Entry, ScanSnapshot
    from space_scout.output import flatten, render_json
    from space_scout.policy import PolicyDecision

    bad, good = tmp_path / "bad", tmp_path / "good"
    snapshot = ScanSnapshot(tmp_path, tuple(Entry(p, p.name, "file", 1, None) for p in (bad, good)))
    def evaluate(path, *args):
        if path == bad:
            raise RuntimeError("cannot normalize")
        return PolicyDecision("scan", "eligible") if failure == "policy" else "unknown"
    monkeypatch.setattr("space_scout.output." + ("decide" if failure == "policy" else "classify"), evaluate)
    rows = flatten(snapshot, Policy(tmp_path, (), ()))
    stream = StringIO()
    render_json(snapshot, rows, stream)
    payload = json.loads(stream.getvalue())
    assert len(payload["entries"]) == 2
    assert next(row for row in payload["entries"] if row["name"] == "bad")["status"] == "skipped"
    assert any(w["path"] == str(bad) for w in payload["warnings"])


def test_cli_file_root_reports_a_clear_directory_requirement(tmp_path, capsys, cli_config):
    path = tmp_path / "file.txt"
    path.write_text("data")
    assert main(["scan", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"][0]["code"] == "root_not_directory"
    assert "directory" in payload["warnings"][0]["message"]


def test_cli_loop_root_keeps_valid_json_with_default_policy(tmp_path, monkeypatch, capsys, cli_config, symlink_supported):
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    monkeypatch.setattr("space_scout.cli.default_policy", default_policy)
    assert main(["scan", str(loop), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"][0]["code"] == "policy_error"


def test_cli_trash_final_recheck_rejects_symlink_swap(tmp_path, monkeypatch, cli_config, symlink_supported):
    path = tmp_path / "item"
    path.write_text("previewed")
    monkeypatch.setattr("space_scout.cli.sys.stdin.isatty", lambda: True)

    def confirm(_):
        path.unlink()
        path.symlink_to("item")
        return "trash"

    monkeypatch.setattr("builtins.input", confirm)
    monkeypatch.setattr("space_scout.cli.trash_many", lambda _: pytest.fail("trashed swapped symlink"))
    assert main(["trash", str(path)]) == 3


@pytest.mark.asyncio
async def test_blocked_ancestor_remains_scan_eligible_but_cleanup_rejected(tmp_path):
    root = tmp_path / "tree"
    parent = root / "parent"
    blocked = parent / "blocked"
    policy = Policy(root, (), (blocked,))
    snapshot = ScanSnapshot(root, (Entry(parent, "parent", "directory", 0, None, ()),))
    rows = flatten(snapshot, policy)
    assert rows[0].status == "scan"
    assert "contains protected or excluded path" in (rows[0].reason or "")
    app = BrowseApp(snapshot, policy, Config({}, (), {}))
    async with app.run_test(size=(120, 30)) as pilot:
        details = str(app.query_one("#details", Static).render())
        assert "Status: BLOCKED" in details
        assert "contains protected or excluded path" in details
        await pilot.press("q")
