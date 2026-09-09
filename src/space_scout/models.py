"""Immutable data models used by the filesystem scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class ScanOptions:
    root: Path
    follow_symlinks: bool = False
    stay_on_filesystem: bool = True
    unlocked_paths: frozenset[Path] = frozenset()
    max_depth: int | None = None


@dataclass(frozen=True, slots=True)
class ScanWarning:
    path: Path
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Entry:
    path: Path
    name: str
    kind: Literal["file", "directory", "symlink", "special", "skipped"]
    logical_bytes: int
    allocated_bytes: int | None
    children: tuple[Entry, ...] = ()
    warning: str | None = None
    # Nanoseconds since the Unix epoch; includes this entry and scanned
    # descendants only. Unknown/skipped metadata is not part of the range.
    modified_min_ns: int | None = None
    modified_max_ns: int | None = None


@dataclass(frozen=True, slots=True)
class ScanSnapshot:
    root: Path
    entries: tuple[Entry, ...]
    warnings: tuple[ScanWarning, ...] = field(default_factory=tuple)
