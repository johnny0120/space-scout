from pathlib import Path

from space_scout.cli import main


def test_scan_fixture_json(tmp_path: Path, capsys, isolated_cli):
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "build.bin").write_bytes(b"x" * 32)
    assert main(["scan", str(tmp_path), "--json"]) == 0
    assert '"logical_bytes": 32' in capsys.readouterr().out
