from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a native Space Scout executable.")
    parser.add_argument("--dist-dir", required=True, type=Path, help="directory that receives the executable")
    return parser


def _validate_dist_dir(path: Path) -> Path:
    dist_dir = path.expanduser().resolve(strict=False)
    home = Path.home().resolve(strict=False)
    if dist_dir == home:
        raise ValueError("refusing to use the user's home directory as the build output target")
    if dist_dir.anchor == str(dist_dir):
        raise ValueError("refusing to use a filesystem root as the build output target")
    return dist_dir


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dist_dir = _validate_dist_dir(args.dist_dir)
    project_root = Path(__file__).resolve().parents[1]
    src_dir = project_root / "src"
    executable = dist_dir / ("space-scout.exe" if sys.platform.startswith("win") else "space-scout")

    env = os.environ.copy()
    existing_python_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(src_dir) if not existing_python_path else os.pathsep.join((str(src_dir), existing_python_path))

    with tempfile.TemporaryDirectory(prefix="space-scout-pyinstaller-") as temp_dir:
        temp_path = Path(temp_dir)
        launcher = temp_path / "space_scout_entry.py"
        launcher.write_text(
            "from space_scout.cli import main\n\nraise SystemExit(main())\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--onefile",
            "--name",
            "space-scout",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(temp_path / "build"),
            "--specpath",
            str(temp_path / "spec"),
            "--paths",
            str(src_dir),
            str(launcher),
        ]
        subprocess.run(command, cwd=project_root, env=env, check=True)

    if not executable.is_file():
        raise FileNotFoundError(f"expected executable was not created: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
