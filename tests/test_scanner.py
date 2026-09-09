import os
import stat
from pathlib import Path

import pytest

import space_scout.scanner as scanner_module
from space_scout.models import ScanOptions
from space_scout.scanner import scan


def test_directory_size_is_recursive(tmp_path: Path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "a.bin").write_bytes(b"a" * 11)
    (tmp_path / "b.bin").write_bytes(b"b" * 7)
    snapshot = scan(ScanOptions(tmp_path))
    assert sum(entry.logical_bytes for entry in snapshot.entries) == 18


def test_directory_allocated_bytes_include_descendants_recursively(tmp_path: Path, monkeypatch):
    parent = tmp_path / "parent"
    nested = parent / "nested"
    nested.mkdir(parents=True)
    (nested / "a.bin").write_bytes(b"a" * 7)
    (parent / "b.bin").write_bytes(b"b" * 3)

    def fake_allocated(metadata):
        return 5 if stat.S_ISDIR(metadata.st_mode) else metadata.st_size

    monkeypatch.setattr(scanner_module._Scanner, "allocated_bytes", staticmethod(fake_allocated))

    snapshot = scan(ScanOptions(tmp_path))

    parent_entry = next(entry for entry in snapshot.entries if entry.name == "parent")
    nested_entry = next(entry for entry in parent_entry.children if entry.name == "nested")
    assert nested_entry.allocated_bytes == 12
    assert parent_entry.allocated_bytes == 20


def test_directory_allocated_bytes_become_unknown_when_descendant_is_unknown(tmp_path: Path, monkeypatch):
    parent = tmp_path / "parent"
    nested = parent / "nested"
    nested.mkdir(parents=True)
    (nested / "known.bin").write_bytes(b"k" * 4)
    (parent / "mystery.bin").write_bytes(b"m" * 13)

    def fake_allocated(metadata):
        if stat.S_ISDIR(metadata.st_mode):
            return 5
        return None if metadata.st_size == 13 else metadata.st_size

    monkeypatch.setattr(scanner_module._Scanner, "allocated_bytes", staticmethod(fake_allocated))

    snapshot = scan(ScanOptions(tmp_path))

    parent_entry = next(entry for entry in snapshot.entries if entry.name == "parent")
    assert parent_entry.allocated_bytes is None


def test_symlink_is_not_followed(tmp_path: Path, symlink_supported):
    target = tmp_path / "target"
    target.write_bytes(b"x" * 20)
    (tmp_path / "link").symlink_to(target)
    snapshot = scan(ScanOptions(tmp_path))
    link = next(entry for entry in snapshot.entries if entry.name == "link")
    assert link.kind == "symlink"
    assert link.children == ()


def test_followed_symlink_cycle_is_skipped_and_siblings_continue(tmp_path: Path, symlink_supported):
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "loop").symlink_to(directory, target_is_directory=True)
    (tmp_path / "sibling.bin").write_bytes(b"sibling")

    snapshot = scan(ScanOptions(tmp_path, follow_symlinks=True))

    sibling = next(entry for entry in snapshot.entries if entry.name == "sibling.bin")
    assert sibling.kind == "file"
    loop = next(entry for entry in snapshot.entries if entry.name == "directory").children[0]
    assert loop.kind == "skipped"
    assert loop.warning
    assert any(warning.code == "cycle_detected" for warning in snapshot.warnings)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes require os.mkfifo")
def test_special_file_has_warning(tmp_path: Path):
    fifo = tmp_path / "named-pipe"
    os.mkfifo(fifo)

    snapshot = scan(ScanOptions(tmp_path))

    entry = snapshot.entries[0]
    assert entry.kind == "special"
    assert entry.warning
    assert any(warning.path == fifo for warning in snapshot.warnings)


def test_directory_iteration_error_clears_allocated_total_before_yielding_children(tmp_path: Path, monkeypatch):
    directory = tmp_path / "directory"
    directory.mkdir()
    original_scandir = scanner_module.os.scandir

    class FailingScan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("iteration failed")

    def scandir(path):
        if Path(path) == directory:
            return FailingScan()
        return original_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", scandir)
    snapshot = scan(ScanOptions(tmp_path))

    entry = snapshot.entries[0]
    assert entry.allocated_bytes is None
    assert entry.logical_bytes == 0
    assert any(warning.path == directory and warning.code == "scan_error" for warning in snapshot.warnings)


def test_directory_iteration_error_clears_allocated_total_after_yielding_children(tmp_path: Path, monkeypatch):
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "first.bin").write_bytes(b"12345")
    original_scandir = scanner_module.os.scandir

    class FailingScan:
        def __init__(self, path):
            self._iterator = original_scandir(path)
            self._yielded = False

        def __enter__(self):
            self._iterator.__enter__()
            return self

        def __exit__(self, *args):
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            if self._yielded:
                raise OSError("iteration failed")
            item = next(self._iterator)
            self._yielded = True
            return item

    def scandir(path):
        if Path(path) == directory:
            return FailingScan(path)
        return original_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", scandir)
    snapshot = scan(ScanOptions(tmp_path))

    entry = snapshot.entries[0]
    assert entry.allocated_bytes is None
    assert entry.logical_bytes == 5
    assert entry.children[0].name == "first.bin"
    assert any(warning.path == directory and warning.code == "scan_error" for warning in snapshot.warnings)
