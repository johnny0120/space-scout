from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
PERSONAL_ABSOLUTE_PATH = re.compile(
    r"(?<![\w.-])/(?:Users|home)/[^\s'\"`]+|(?<![\w.-])[A-Za-z]:\\Users\\[^\s'\"`]+"
)


def _tracked_markdown() -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "*.md"),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        REPO_ROOT / path
        for path in result.stdout.splitlines()
        if (REPO_ROOT / path).is_file()
    )


def test_readme_states_the_product_promise_and_essential_sections() -> None:
    text = README.read_text(encoding="utf-8")

    assert "Space Scout finds the directories that use your disk space" in text
    for heading in ("## Why Space Scout", "## Quick start", "## Safety", "## Not a file manager"):
        assert heading in text


def test_readme_uses_uv_and_documents_the_adaptive_list_basics() -> None:
    text = README.read_text(encoding="utf-8")
    command_blocks = re.findall(r"```(?:bash|powershell)\n(.*?)```", text, flags=re.DOTALL)
    commands = [line for block in command_blocks for line in block.splitlines() if line.strip()]

    assert "uv sync --extra dev" in text
    assert 'uv run space-scout scan "$HOME/Downloads" --depth 2' in text
    assert "`Name` and `On disk` are always visible" in text
    assert commands
    assert all(command.startswith(("uv sync ", "uv run ")) for command in commands)


def test_public_release_guidance_is_present() -> None:
    readme = README.read_text(encoding="utf-8")
    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "GitHub" in readme and "Releases page" in readme
    assert "exits with `0`" in readme
    assert "security/advisories/new" in security
    assert 'tags:\n      - "v*"' in workflow
    assert "package_release.py" in workflow


def test_internal_planning_documents_are_not_tracked() -> None:
    tracked = {path.relative_to(REPO_ROOT).as_posix() for path in _tracked_markdown()}
    assert not any(path.startswith("docs/superpowers/") for path in tracked)


def test_tracked_docs_do_not_include_personal_absolute_paths() -> None:
    for path in _tracked_markdown():
        assert not PERSONAL_ABSOLUTE_PATH.search(path.read_text(encoding="utf-8")), path
