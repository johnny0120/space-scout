import os
from pathlib import Path

from space_scout.trash import TrashResult, summarize, trash_many, trash_one


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


def _trash_in_tmp(monkeypatch, tmp_path: Path) -> Path:
    """Point trash_root at a directory inside tmp_path so moves stay same-volume."""
    trash = tmp_path / "Trash"
    trash.mkdir()
    monkeypatch.setattr("space_scout.trash.trash_root", lambda path: trash)
    return trash


def test_trash_records_moved_bytes(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("space_scout.trash.send2trash", lambda value: calls.append(value))
    _trash_in_tmp(monkeypatch, tmp_path)
    target = tmp_path / "item"
    target.write_text("item")
    results = trash_many((target,), sizes={target: 42})
    assert results[0].success
    assert results[0].moved_bytes == 42


def test_freed_bytes_from_statvfs_delta(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("space_scout.trash.send2trash", lambda value: calls.append(value))
    _trash_in_tmp(monkeypatch, tmp_path)
    target = tmp_path / "item"
    target.write_text("item")

    class FakeStatvfs:
        def __init__(self, bavail: int, frsize: int) -> None:
            self.f_bavail = bavail
            self.f_frsize = frsize

    state = {"calls": 0}

    def fake_statvfs(_path: object) -> FakeStatvfs:
        state["calls"] += 1
        return FakeStatvfs(100, 4096) if state["calls"] == 1 else FakeStatvfs(110, 4096)

    monkeypatch.setattr("space_scout.trash.os.statvfs", fake_statvfs)
    result = trash_one(target)
    assert result.success
    assert result.freed_bytes == (110 - 100) * 4096


def test_same_volume_move_frees_nothing_until_emptied(monkeypatch, tmp_path: Path):
    trash = _trash_in_tmp(monkeypatch, tmp_path)

    def fake_send2trash(value: str) -> None:
        Path(value).rename(trash / Path(value).name)

    monkeypatch.setattr("space_scout.trash.send2trash", fake_send2trash)
    target = tmp_path / "item"
    target.write_text("item")
    result = trash_one(target, moved_bytes=123)
    assert result.success
    assert result.moved_bytes == 123
    assert result.freed_bytes == 0


def test_trash_refuses_cross_volume_move(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("space_scout.trash.send2trash", lambda value: calls.append(value))
    _trash_in_tmp(monkeypatch, tmp_path)
    target = tmp_path / "item"
    target.write_text("item")
    real_device = os.stat(target).st_dev
    monkeypatch.setattr(
        "space_scout.trash._device",
        lambda path: real_device + 1 if Path(path) == target else real_device,
    )
    result = trash_one(target)
    assert not result.success
    assert "volume" in result.message
    assert calls == []


def test_trash_reports_unavailable_without_trash_root(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("space_scout.trash.send2trash", lambda value: calls.append(value))
    target = tmp_path / "item"
    target.write_text("item")
    monkeypatch.setattr("space_scout.trash.trash_root", lambda path: None)
    result = trash_one(target)
    assert not result.success
    assert "unavailable" in result.message
    assert calls == []
    missing_parent = tmp_path / "missing" / "Trash"
    monkeypatch.setattr("space_scout.trash.trash_root", lambda path: missing_parent)
    result = trash_one(target)
    assert not result.success
    assert "unavailable" in result.message
    assert calls == []


def test_trash_many_partial_failure_counts_only_successes(monkeypatch, tmp_path: Path):
    _trash_in_tmp(monkeypatch, tmp_path)
    calls = []

    def fake_send2trash(value: str) -> None:
        calls.append(value)
        if value.endswith("bad"):
            raise OSError("denied")

    monkeypatch.setattr("space_scout.trash.send2trash", fake_send2trash)
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.write_text("g")
    bad.write_text("b")
    results = trash_many((good, bad), sizes={good: 10, bad: 20})
    assert [result.success for result in results] == [True, False]
    assert results[0].moved_bytes == 10
    assert results[1].moved_bytes is None
    assert sum(result.moved_bytes or 0 for result in results if result.success) == 10


def test_summarize_reports_moved_and_freed():
    results = (
        TrashResult(Path("/a"), True, "moved to trash", moved_bytes=100, freed_bytes=0),
        TrashResult(Path("/b"), True, "moved to trash", moved_bytes=50, freed_bytes=25),
        TrashResult(Path("/c"), False, "denied"),
    )
    summary = summarize(results)
    assert "moved 150" in summary
    assert "freed 25" in summary
    assert "Trash not emptied" in summary