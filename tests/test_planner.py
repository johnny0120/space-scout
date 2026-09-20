from pathlib import Path

from space_scout.knowledge import CleanupAdvice
from space_scout.output import ReportRow
from space_scout.planner import build_cleanup_plan


def _row(path: Path, size: int, *, risk: str = "safe", reclaimable: bool = True) -> ReportRow:
    advice = CleanupAdvice(
        category="cache",
        risk=risk,  # type: ignore[arg-type]
        assessment_status="assessed",
        reclaimable=reclaimable,
        method="Trash",
        impact="regenerable",
        reason="test rule",
    )
    return ReportRow(str(path), path.name, "directory", size, size, "cache", "scan", None, advice)


def test_plan_uses_on_disk_bytes_and_deduplicates_nested_targets(tmp_path: Path):
    parent = _row(tmp_path / "cache", 80)
    child = _row(tmp_path / "cache" / "uv", 70)
    sibling = _row(tmp_path / "downloads", 40)

    plan = build_cleanup_plan((child, sibling, parent), target_bytes=100)

    assert [row.path for row in plan.items] == [parent.path, sibling.path]
    assert plan.estimated_bytes == 120
    assert plan.target_bytes == 100
    assert plan.complete


def test_plan_excludes_review_items_unless_explicitly_requested(tmp_path: Path):
    safe = _row(tmp_path / "cache", 80)
    review = _row(tmp_path / "old-build", 70, risk="review")

    assert build_cleanup_plan((safe, review), target_bytes=100).items == (safe,)
    plan = build_cleanup_plan((safe, review), target_bytes=100, include_review=True)
    assert plan.items == (safe, review)
    assert plan.estimated_bytes == 150


def test_plan_marks_unmet_target_without_inventing_bytes(tmp_path: Path):
    safe = _row(tmp_path / "cache", 80)

    plan = build_cleanup_plan((safe,), target_bytes=100)

    assert plan.items == (safe,)
    assert plan.estimated_bytes == 80
    assert not plan.complete
