"""Pure presentation rules shared by the interactive disk browser."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from .models import Entry

Column = Literal["on_disk", "name", "logical", "class", "status"]

_NARROW_COLUMNS = ("name", "on_disk")
_MEDIUM_COLUMNS = ("name", "on_disk", "status")
_WIDE_COLUMNS = ("name", "on_disk", "logical", "class", "status")


def visible_columns(width: int) -> tuple[str, ...]:
    """Return columns suitable for a terminal of *width* characters."""
    if width < 90:
        return _NARROW_COLUMNS
    if width < 120:
        return _MEDIUM_COLUMNS
    return _WIDE_COLUMNS


def sort_key_for(
    column: str,
    classification: Callable[[Entry], str],
    status: Callable[[Entry], str],
) -> Callable[[Entry], tuple[int | str, ...]]:
    """Build a deterministic sort key for a supported browser column.

    On-disk sorting treats entries with unknown allocation metadata as a
    separate bucket instead of borrowing logical bytes.  Every key includes
    name and path tie-breakers so equal values retain a stable order without
    consulting the filesystem.
    """
    if column == "on_disk":
        def on_disk(entry: Entry) -> tuple[int, int, str, str]:
            unknown = 1 if entry.allocated_bytes is None else 0
            value = entry.allocated_bytes if entry.allocated_bytes is not None else 0
            return unknown, value, entry.name.casefold(), str(entry.path)

        return on_disk
    if column == "name":
        return lambda entry: (entry.name.casefold(), str(entry.path))
    if column == "class":
        return lambda entry: (classification(entry), entry.name.casefold(), str(entry.path))
    if column == "status":
        return lambda entry: (status(entry), entry.name.casefold(), str(entry.path))
    raise ValueError(f"unsupported sort column: {column}")


def format_size(value: int | None, width: int | None = None) -> str:
    """Format bytes with binary units, optionally right-aligning the result."""
    if value is None:
        rendered = "—"
    else:
        amount = float(value)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
            if amount < 1024 or unit == "PiB":
                rendered = f"{amount:.1f} {unit}"
                break
            amount /= 1024
    return rendered.rjust(width) if width is not None else rendered
