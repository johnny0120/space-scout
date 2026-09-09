import json
from io import StringIO
from pathlib import Path

from space_scout.models import Entry, ScanSnapshot, ScanWarning
from space_scout.output import flatten, render_json, render_table
from space_scout.policy import default_policy


def test_json_is_valid_and_contains_warnings():
    snapshot = ScanSnapshot(Path("/tmp/root"), (
        Entry(Path("/tmp/root/a"), "a", "file", 10, 10, ()),
    ), ())
    stream = StringIO()
    render_json(snapshot, flatten(snapshot, default_policy(Path("/tmp/root"))), stream)
    payload = json.loads(stream.getvalue())
    assert payload["root"] == str(Path("/tmp/root"))
    assert payload["entries"][0]["logical_bytes"] == 10
    assert payload["warnings"] == []


def test_flatten_includes_nested_entries_in_size_order():
    child = Entry(Path("/tmp/root/z.txt"), "z.txt", "file", 30, 40, ())
    parent = Entry(Path("/tmp/root/dir"), "dir", "directory", 30, 50, (child,))
    small = Entry(Path("/tmp/root/a"), "a", "file", 5, None, ())
    rows = flatten(ScanSnapshot(Path("/tmp/root"), (small, parent)), default_policy(Path("/tmp/root")))
    assert [row.path for row in rows] == [str(Path(path)) for path in ("/tmp/root/dir", "/tmp/root/z.txt", "/tmp/root/a")]


def test_table_has_fixed_header_and_escapes_values():
    row = flatten(ScanSnapshot(Path("/tmp/root"), (
        Entry(Path("/tmp/root/a\nfile"), "a\nfile", "file", 10, 10, ()),
    )), default_policy(Path("/tmp/root")))[0]
    stream = StringIO()
    render_table((row,), stream)
    output = stream.getvalue()
    assert output.splitlines()[0].split() == ["SIZE", "ON_DISK", "TYPE", "CLASS", "STATUS", "PATH"]
    assert "a\\nfile" in output


def test_json_includes_warnings_when_entries_are_empty():
    warning = ScanWarning(Path("/tmp/root"), "root_error", "permission denied")
    stream = StringIO()
    render_json(ScanSnapshot(Path("/tmp/root"), (), (warning,)), (), stream)
    assert json.loads(stream.getvalue())["warnings"] == [{
        "path": str(Path("/tmp/root")), "code": "root_error", "message": "permission denied",
    }]


def test_flatten_applies_policy_classification_and_preserves_warning():
    from space_scout.policy import Policy

    tmp_path = Path("/workspace/project")
    entry = Entry(tmp_path / "a.py", "a.py", "file", 7, None, warning="unreadable")
    row, = flatten(ScanSnapshot(tmp_path, (entry,)), Policy(tmp_path, (), (tmp_path,)))
    assert row.classification == "source"
    assert row.status == "protected"
    assert "unreadable" in row.reason
    assert row.allocated_bytes is None


def test_flatten_keeps_scanned_parent_eligible_but_reports_cleanup_rejection():
    from space_scout.policy import Policy

    root = Path("/workspace/project")
    parent = Entry(root / "parent", "parent", "directory", 12, 24, ())
    policy = Policy(root, (), (root / "parent" / "blocked",))

    row, = flatten(ScanSnapshot(root, (parent,)), policy)

    assert row.status == "scan"
    assert "contains protected or excluded path" in row.reason


def test_json_preserves_unicode_and_has_stable_keys(tmp_path):
    entry = Entry(tmp_path / "中文", "中文", "file", 1, None)
    stream = StringIO()
    snapshot = ScanSnapshot(tmp_path, (entry,))
    render_json(snapshot, flatten(snapshot, default_policy(tmp_path)), stream)
    assert "中文" in stream.getvalue()
    payload = json.loads(stream.getvalue())
    assert list(payload) == sorted(payload)
    assert list(payload["entries"][0]) == sorted(payload["entries"][0])


def test_table_escapes_control_characters_in_all_values():
    from space_scout.output import ReportRow

    stream = StringIO()
    render_table((ReportRow("a\x1b[31m\t\r\u202e", "a", "f\n", 1, None, "c\x00", "s\x85", None),), stream)
    output = stream.getvalue()
    assert len(output.splitlines()) == 2
    for control in ("\x1b", "\t", "\r", "\u202e", "\x00", "\x85"):
        assert control not in output


def test_table_marks_blocked_status():
    from space_scout.output import ReportRow

    stream = StringIO()
    render_table((ReportRow("/workspace/download.bin", "download.bin", "file", 1, 1,
                            "extension", "blocked", "cleanup policy"),), stream)
    assert "BLOCKED" in stream.getvalue()


def test_scan_cli_json_and_depth(tmp_path, capsys, isolated_cli):
    from space_scout.cli import main

    (tmp_path / "a.py").write_text("hello")
    assert main(["scan", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"][0]["logical_bytes"] == 5
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "hidden").write_text("content")
    assert main(["scan", str(tmp_path), "--depth", "1", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert any(w["code"] == "depth_limited" for w in payload["warnings"])
    assert not any(row["name"] == "hidden" for row in payload["entries"])


def test_scan_cli_table_and_missing_root(tmp_path, capsys, isolated_cli):
    from space_scout.cli import main

    assert main(["scan", str(tmp_path)]) == 0
    assert capsys.readouterr().out.split() == ["SIZE", "ON_DISK", "TYPE", "CLASS", "STATUS", "PATH"]
    assert main(["scan", str(tmp_path / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["warnings"]
    assert main(["scan"]) == 2
    assert main(["scan", str(tmp_path), "--depth", "-1"]) == 2
