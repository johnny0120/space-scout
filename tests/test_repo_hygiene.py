from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_check_public_repo_rejects_tracked_paths_and_secrets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    malicious_path = "/" "Users/" + "alice/private/space-scout"
    _write(repo, "tracked.txt", f"home={malicious_path}\n")
    secret_token = "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"
    _write(repo, "secret.txt", f"{'to' + 'ken'}={secret_token}\n")
    _write(repo, "dist/space-scout", "binary output\n")
    _write(repo, "uv.lock", "# keep me\n")
    _git(repo, "add", "tracked.txt", "secret.txt", "dist/space-scout", "uv.lock")
    _git(repo, "commit", "-m", "test fixture")

    result = subprocess.run(
        (sys.executable, "scripts/check_public_repo.py", str(repo)),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "tracked absolute path" in result.stdout
    assert "secret-like token" in result.stdout
    assert "generated artifact path" in result.stdout
    assert "uv.lock" not in result.stdout


def test_check_public_repo_accepts_clean_tracked_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    _write(repo, "README.md", "public docs only\n")
    _write(repo, "uv.lock", "# lockfile stays tracked\n")
    _git(repo, "add", "README.md", "uv.lock")
    _git(repo, "commit", "-m", "test fixture")

    result = subprocess.run(
        (sys.executable, "scripts/check_public_repo.py", str(repo)),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "public repo check passed"


def test_check_public_repo_reads_worktree_changes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    tracked = repo / "README.md"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "test fixture")
    tracked.write_text("path=/" + "Users/bob/private\n", encoding="utf-8")

    result = subprocess.run(
        (sys.executable, "scripts/check_public_repo.py", str(repo)),
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "tracked absolute path" in result.stdout
