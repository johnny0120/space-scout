from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 1:
        raise SystemExit("usage: smoke_test.py DIST_DIR")
    dist_dir = Path(args[0]).expanduser().resolve()
    executable = dist_dir / ("space-scout.exe" if sys.platform.startswith("win") else "space-scout")

    subprocess.run([str(executable), "--help"], check=True)

    with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
        root = Path(temp_dir)
        target = root / "target"
        target.mkdir()
        (target / "build.bin").write_bytes(b"x" * 32)
        result = subprocess.run(
            [str(executable), "scan", str(root), "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        entry = next(row for row in payload["entries"] if row["name"] == "build.bin")
        assert entry["logical_bytes"] == 32
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
