from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from scripts.package_release import package


def test_package_release_writes_archive_and_checksum(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    output = tmp_path / "release"
    dist.mkdir()
    executable = dist / "space-scout"
    executable.write_bytes(b"native executable")

    archive = package(dist, output, "Darwin", "ARM64")

    assert archive.name == "space-scout-macos-arm64.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("space-scout") == b"native executable"
    checksum = output / f"{archive.name}.sha256"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum.read_text(encoding="ascii") == f"{digest}  {archive.name}\n"


def test_package_release_uses_windows_executable_name(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "space-scout.exe").write_bytes(b"windows executable")

    archive = package(dist, tmp_path / "release", "Windows", "X64")

    assert archive.name == "space-scout-windows-x86_64.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("space-scout.exe") == b"windows executable"
