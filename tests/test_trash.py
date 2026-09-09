from pathlib import Path

from space_scout.trash import trash_many, trash_one


def test_trash_reports_each_item(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("space_scout.trash.send2trash", lambda value: calls.append(value))
    first, second = (tmp_path / "a", tmp_path / "b")
    first.write_text("a")
    second.write_text("b")
    results = trash_many((first, second))
    assert all(result.success for result in results)
    assert calls == [str(first), str(second)]


def test_trash_one_reports_supported_failures(monkeypatch, tmp_path: Path):
    path = tmp_path / "item"
    path.write_text("item")
    monkeypatch.setattr("space_scout.trash.send2trash", lambda _: (_ for _ in ()).throw(OSError("denied")))
    result = trash_one(path)
    assert result.path == path
    assert not result.success
    assert "denied" in result.message
