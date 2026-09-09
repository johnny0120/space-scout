"""Package a native executable and write a SHA-256 checksum beside it."""

from __future__ import annotations

import argparse
import hashlib
import platform
import stat
import sys
import zipfile
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package a Space Scout release artifact.")
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--platform", default=sys.platform, help="release platform label")
    parser.add_argument("--arch", default=platform.machine(), help="release architecture label")
    return parser


def _platform_label(value: str) -> str:
    value = value.lower()
    if value.startswith("win"):
        return "windows"
    if value in {"darwin", "mac", "macos"}:
        return "macos"
    if value.startswith("linux"):
        return "linux"
    return value.replace(" ", "-")


def _arch_label(value: str) -> str:
    aliases = {
        "x64": "x86_64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86": "x86",
    }
    return aliases.get(value.lower(), value.lower().replace(" ", "-"))


def package(dist_dir: Path, output_dir: Path, platform_name: str, arch: str) -> Path:
    executable_name = "space-scout.exe" if platform_name.lower().startswith("win") else "space-scout"
    executable = dist_dir.expanduser().resolve() / executable_name
    if not executable.is_file():
        raise FileNotFoundError(f"expected executable was not created: {executable}")

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"space-scout-{_platform_label(platform_name)}-{_arch_label(arch)}"
    archive = output_dir / f"{stem}.zip"
    info = zipfile.ZipInfo(executable.name)
    info.external_attr = (stat.S_IMODE(executable.stat().st_mode) or 0o755) << 16
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(info, executable.read_bytes())

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output_dir / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )
    return archive


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    package(args.dist_dir, args.output_dir, args.platform, args.arch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
