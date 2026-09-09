from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def test_check_script_exists_and_uses_uv_run() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "check.sh"
    text = script.read_text(encoding="utf-8")
    assert "uv run" in text
    assert "ruff check" in text
    assert "mypy" in text
    assert "check_public_repo.py" in text
    assert "pytest -q" in text


def test_check_script_keeps_running_after_a_failed_check(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(Path(__file__).resolve().parents[1], repo, ignore=shutil.ignore_patterns(".git", ".venv", "dist*", "__pycache__", "*.egg-info"))
    script = repo / "scripts" / "check.sh"
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    uv_log = tmp_path / "uv-calls.txt"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{uv_log}"\n'
        'case "$2" in\n'
        '  mypy) exit 2 ;;\n'
        'esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert any("ruff check" in call for call in calls)
    assert any("mypy" in call for call in calls)
    assert any("check_public_repo.py" in call for call in calls)
    assert any("pytest -q" in call for call in calls)
