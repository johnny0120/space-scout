import json
from pathlib import Path

import pytest

from space_scout.cli import main
from space_scout.config import Config
from space_scout.policy import Policy
from space_scout.trash import TrashResult


def test_trash_preview_reports_unestimated_size_before_rejection(monkeypatch, capsys, tmp_path: Path):
    path = tmp_path / "Applications"
    path.write_text("blocked")

    monkeypatch.setattr("space_scout.cli.load_config", lambda: Config({}, (), {}))
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), (path,)))
    monkeypatch.setattr("space_scout.cli._preview", lambda *args: pytest.fail("sized protected path"))

    called = []

    def fake_trash_many(paths, *, sizes=None):
        called.append(tuple(paths))
        return ()

    monkeypatch.setattr("space_scout.cli.trash_many", fake_trash_many)

    assert main(["trash", str(path), "--yes"]) == 3
    assert called == []

    output = capsys.readouterr().out.splitlines()
    assert output == [
        f"{path}  size not estimated",
        f"rejected: {path} (protected: path is in protected system location {path})",
    ]


def test_help_is_successful(capsys):
    assert main(["--help"]) == 0
    assert "scan" in capsys.readouterr().out


def test_browse_requires_explicit_path(capsys):
    assert main(["browse"]) == 2
    assert "PATH" in capsys.readouterr().err


def test_browse_wires_snapshot_policy_config_and_exit_code(tmp_path, monkeypatch):
    from space_scout.models import ScanSnapshot
    config = Config({}, (tmp_path / "excluded",), {})
    snapshot = ScanSnapshot(tmp_path, ())
    monkeypatch.setattr("space_scout.cli.load_config", lambda: config)

    def scan(root, policy):
        assert root == tmp_path
        assert policy.exclusions == config.exclusions
        return snapshot

    def browse(actual_snapshot, policy, actual_config):
        assert actual_snapshot is snapshot
        assert actual_config is config
        assert policy.root == tmp_path
        return 3

    monkeypatch.setattr("space_scout.tui.scan_for_browse", scan)
    monkeypatch.setattr("space_scout.tui.run_browse", browse)
    assert main(["browse", str(tmp_path)]) == 3


def test_browse_select_passes_patterns_to_scanner_and_tui(tmp_path, monkeypatch):
    from space_scout.models import ScanSnapshot

    config = Config({}, (), {})
    snapshot = ScanSnapshot(tmp_path, ())
    monkeypatch.setattr("space_scout.cli.load_config", lambda: config)
    observed: dict[str, object] = {}

    def scan(root, policy, *, select_patterns):
        observed["scan"] = (root, select_patterns)
        return snapshot

    def browse(actual_snapshot, policy, actual_config, *, select_patterns):
        observed["browse"] = (actual_snapshot, select_patterns)
        return 0

    monkeypatch.setattr("space_scout.tui.scan_for_browse", scan)
    monkeypatch.setattr("space_scout.tui.run_browse", browse)

    assert main(["browse", str(tmp_path), "--select", ".*", "--select", "Developer"]) == 0
    assert observed["scan"] == (tmp_path, (".*", "Developer"))
    assert observed["browse"] == (snapshot, (".*", "Developer"))


def test_browse_initial_scan_handles_symlink_loop(tmp_path, monkeypatch, symlink_supported):
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    (tmp_path / "sibling.txt").write_text("safe")
    monkeypatch.setattr("space_scout.cli.load_config", lambda: Config({}, (), {}))
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), ()))

    def browse(snapshot, policy, config):
        entries = {entry.name: entry for entry in snapshot.entries}
        assert entries["loop"].kind == "skipped"
        assert entries["sibling.txt"].logical_bytes == 4
        assert any(w.path == loop and w.code == "policy_error" for w in snapshot.warnings)
        return 2

    monkeypatch.setattr("space_scout.tui.run_browse", browse)
    assert main(["browse", str(tmp_path)]) == 2


def test_trash_rejects_knowledge_protected_path(tmp_path, monkeypatch, capsys, isolated_cli):
    npmrc = tmp_path / ".npmrc"
    npmrc.write_text("registry=https://example.com")
    called = []

    def fake_trash_many(paths, *, sizes=None):
        called.append(tuple(paths))
        return ()

    monkeypatch.setattr("space_scout.cli.trash_many", fake_trash_many)
    assert main(["trash", str(npmrc), "--yes"]) == 3
    assert called == []
    output = capsys.readouterr().out
    assert "rejected:" in output
    assert "protected" in output


def test_trash_rejects_directory_containing_credentials(tmp_path, monkeypatch, capsys, isolated_cli):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".npmrc").write_text("registry=https://example.com")
    called = []

    def fake_trash_many(paths, *, sizes=None):
        called.append(tuple(paths))
        return ()

    monkeypatch.setattr("space_scout.cli.trash_many", fake_trash_many)
    assert main(["trash", str(project), "--yes"]) == 3
    assert called == []
    output = capsys.readouterr().out
    assert "rejected:" in output
    assert ".npmrc" in output


def test_trash_prints_session_summary(tmp_path, monkeypatch, capsys, isolated_cli):
    target = tmp_path / "cache"
    target.write_bytes(b"12345")

    def fake_trash_many(paths, *, sizes=None):
        return (TrashResult(paths[0], True, "moved to trash", (sizes or {}).get(paths[0]), 4096),)

    monkeypatch.setattr("space_scout.cli.trash_many", fake_trash_many)
    assert main(["trash", str(target), "--yes"]) == 0
    output = capsys.readouterr().out
    assert "moved 5" in output
    assert "freed 4096" in output


def test_scan_json_includes_advice(tmp_path, capsys, isolated_cli):
    (tmp_path / "file.txt").write_text("data")
    assert main(["scan", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"]
    assert all("advice" in entry for entry in payload["entries"])
