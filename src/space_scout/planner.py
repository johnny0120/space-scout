"""Build conservative, non-overlapping cleanup plans from report rows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .output import ReportRow


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    """A preview-only set of cleanup candidates and its size estimate."""

    items: tuple[ReportRow, ...]
    estimated_bytes: int
    target_bytes: int | None
    complete: bool


def estimated_reclaimable_bytes(row: ReportRow) -> int:
    """Return the best available estimate for one reclaimable report row."""
    if row.advice is None or not row.advice.reclaimable:
        return 0
    # Allocated bytes are the useful answer for disk-pressure decisions. A
    # filesystem may not expose them, so logical bytes remain an explicit
    # fallback rather than silently dropping the candidate.
    return row.allocated_bytes if row.allocated_bytes is not None else row.logical_bytes


def _candidate_rows(rows: Iterable[ReportRow], *, include_review: bool) -> list[tuple[ReportRow, int]]:
    candidates: list[tuple[ReportRow, int]] = []
    for row in rows:
        advice = row.advice
        if advice is None or not advice.reclaimable:
            continue
        if advice.risk == "protected" or (advice.risk == "review" and not include_review):
            continue
        amount = estimated_reclaimable_bytes(row)
        if amount > 0:
            candidates.append((row, amount))
    return sorted(candidates, key=lambda item: (-item[1], Path(item[0].path).as_posix()))


def build_cleanup_plan(
    rows: Iterable[ReportRow],
    *,
    target_bytes: int | None = None,
    include_review: bool = False,
) -> CleanupPlan:
    """Select the largest safe candidates without parent/child duplication.

    The planner never executes an action. A directory and one of its
    descendants cannot both be selected, preventing the plan total from
    counting the same bytes twice. ``REVIEW`` rows are excluded unless the
    caller explicitly opts in.
    """
    if target_bytes is not None and target_bytes < 0:
        raise ValueError("target bytes must be non-negative")
    if target_bytes == 0:
        return CleanupPlan((), 0, target_bytes, True)

    selected: list[tuple[ReportRow, int]] = []
    total = 0
    for row, amount in _candidate_rows(rows, include_review=include_review):
        path = Path(row.path)
        selected_paths = [Path(selected_row.path) for selected_row, _ in selected]
        if any(selected_path == path or selected_path in path.parents for selected_path in selected_paths):
            continue
        # Prefer a directory over already selected descendants if an unusual
        # filesystem report makes a parent smaller than its child.
        descendants = [item for item in selected if path in Path(item[0].path).parents]
        if descendants:
            selected = [item for item in selected if item not in descendants]
            total -= sum(item[1] for item in descendants)
        selected.append((row, amount))
        total += amount
        if target_bytes is not None and total >= target_bytes:
            break

    selected.sort(key=lambda item: (-item[1], Path(item[0].path).as_posix()))
    complete = target_bytes is None or total >= target_bytes
    return CleanupPlan(tuple(row for row, _ in selected), total, target_bytes, complete)
