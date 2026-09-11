"""Small, reversible adapter around the platform trash implementation."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from send2trash import send2trash


@dataclass(frozen=True, slots=True)
class TrashResult:
    path: Path
    success: bool
    message: str
    moved_bytes: int | None = None
    freed_bytes: int | None = None


def trash_root(path: Path) -> Path | None:
    """Return the system trash root for the volume holding *path*, or None."""
    if sys.platform == "darwin":
        return Path.home() / ".Trash"
    if sys.platform.startswith("win"):
        return None
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "Trash"
    return Path.home() / ".local" / "share" / "Trash"


def free_bytes(path: Path) -> int | None:
    """Free bytes on the volume containing *path*, or None when unavailable."""
    try:
        stat = os.statvfs(path)
    except (OSError, AttributeError, ValueError):
        return None
    return stat.f_bavail * stat.f_frsize


def _device(path: Path) -> int | None:
    """Device id of *path*, or None when it cannot be determined."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _nearest_existing_ancestor(path: Path) -> Path | None:
    """Nearest existing ancestor of *path* (including itself), or None."""
    ancestor = path
    while not ancestor.exists():
        parent = ancestor.parent
        if parent == ancestor:
            return None
        ancestor = parent
    return ancestor


def _refusal(candidate: Path) -> str | None:
    """Return a refusal reason, or None when the move may proceed."""
    root = trash_root(candidate)
    if root is None:
        return "trash unavailable: no system trash on this platform"
    if not root.parent.exists():
        return "trash unavailable: trash directory parent does not exist"
    ancestor = _nearest_existing_ancestor(root)
    if ancestor is None:
        return "trash unavailable: no existing trash directory"
    trash_dev = _device(ancestor)
    target_dev = _device(candidate)
    if trash_dev is not None and target_dev is not None and trash_dev != target_dev:
        return "refused: target is on a different volume than the trash"
    return None


def trash_one(path: Path, *, moved_bytes: int | None = None) -> TrashResult:
    """Move one path to the operating system trash, reporting failures."""
    candidate = Path(path)
    refusal = _refusal(candidate)
    if refusal is not None:
        return TrashResult(candidate, False, refusal)
    parent = candidate.parent
    before = free_bytes(parent)
    try:
        send2trash(str(candidate))
    except (OSError, PermissionError, ValueError) as exc:
        return TrashResult(candidate, False, str(exc) or exc.__class__.__name__)
    after = free_bytes(parent)
    freed = None
    if before is not None and after is not None:
        freed = max(0, after - before)
    return TrashResult(candidate, True, "moved to trash", moved_bytes, freed)


def trash_many(paths: Sequence[Path], *, sizes: Mapping[Path, int] | None = None) -> tuple[TrashResult, ...]:
    """Move each selected path once and preserve input ordering."""
    if sizes is None:
        sizes = {}
    return tuple(trash_one(path, moved_bytes=sizes.get(path)) for path in paths)


def summarize(results: Sequence[TrashResult]) -> str:
    """Summarize moved and freed bytes across *results*."""
    moved = sum(result.moved_bytes or 0 for result in results if result.success)
    freed = sum(result.freed_bytes or 0 for result in results if result.success)
    return f"moved {moved}; freed {freed} (Trash not emptied / unavailable)"