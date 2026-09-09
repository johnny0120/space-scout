"""Small, reversible adapter around the platform trash implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from send2trash import send2trash


@dataclass(frozen=True, slots=True)
class TrashResult:
    path: Path
    success: bool
    message: str


def trash_one(path: Path) -> TrashResult:
    """Move one path to the operating system trash, reporting failures."""
    candidate = Path(path)
    try:
        send2trash(str(candidate))
    except (OSError, PermissionError, ValueError) as exc:
        return TrashResult(candidate, False, str(exc) or exc.__class__.__name__)
    return TrashResult(candidate, True, "moved to trash")


def trash_many(paths: Sequence[Path]) -> tuple[TrashResult, ...]:
    """Move each selected path once and preserve input ordering."""
    return tuple(trash_one(path) for path in paths)
