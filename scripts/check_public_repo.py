from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![\w.-])/(?:Users|home)/[^\s'\"`]+"),
    re.compile(r"(?<![\w.-])[A-Za-z]:\\Users\\[^\s'\"`]+"),
)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("generic secret", re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]")),
)
GENERATED_PATH_PARTS = (
    ".venv/",
    "__pycache__/",
    "dist/",
    "build/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tox/",
    ".nox/",
    ".eggs/",
    ".egg-info/",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check tracked files for public-repo hygiene issues.")
    parser.add_argument("repo", nargs="?", default=Path.cwd(), type=Path, help="repository root to inspect")
    return parser


def _candidate_files(repo: Path) -> list[str]:
    completed = subprocess.run(
        ("git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        check=True,
        capture_output=True,
    )
    return [entry.decode("utf-8") for entry in completed.stdout.split(b"\0") if entry]


def _read_file(repo: Path, path: str) -> str | None:
    file_path = repo / path
    try:
        return file_path.read_bytes().decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _scan_file(path: str, content: str) -> list[str]:
    problems: list[str] = []
    normalized = path.replace("\\", "/")
    if any(part in normalized for part in GENERATED_PATH_PARTS):
        problems.append(f"generated artifact path: {path}")
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            problems.append(f"secret-like token ({label}): {path}")
    for pattern in ABSOLUTE_PATH_PATTERNS:
        if pattern.search(content):
            problems.append(f"tracked absolute path: {path}")
    return problems


def check_repo(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in _candidate_files(repo):
        content = _read_file(repo, path)
        if content is None:
            continue
        problems.extend(_scan_file(path, content))
    return problems


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = args.repo.expanduser().resolve()
    problems = check_repo(repo)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("public repo check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
