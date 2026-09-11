import pytest

from space_scout.models import Entry
from space_scout.presentation import format_size, sort_key_for, visible_columns


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (0, ("name", "on_disk")),
        (89, ("name", "on_disk")),
        (90, ("name", "on_disk", "status")),
        (119, ("name", "on_disk", "status")),
        (120, ("name", "on_disk", "logical", "class", "status", "risk")),
        (200, ("name", "on_disk", "logical", "class", "status", "risk")),
    ],
)
def test_visible_columns_follow_fixed_width_tiers(width, expected):
    assert visible_columns(width) == expected


def test_wide_tier_appends_risk_after_status():
    assert visible_columns(140)[-2:] == ("status", "risk")


def test_format_size_uses_shared_units_and_right_aligns():
    assert format_size(0) == "0.0 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(1536, 10) == "   1.5 KiB"


def test_format_size_represents_unknown_bytes():
    assert format_size(None) == "—"
    assert format_size(None, 4) == "   —"


def test_sort_keys_are_deterministic_with_casefolded_name_and_path(tmp_path):
    first = Entry(tmp_path / "b", "same", "file", 20, 30)
    second = Entry(tmp_path / "a", "SAME", "file", 10, None)
    labels = {first.path: "z-class", second.path: "a-class"}
    states = {first.path: "WARNING", second.path: "CLEANABLE"}
    classification = lambda entry: labels[entry.path]
    status = lambda entry: states[entry.path]

    assert sort_key_for("name", classification, status)(first) == ("same", str(first.path))
    assert sort_key_for("class", classification, status)(first) == (
        "z-class", "same", str(first.path)
    )
    assert sort_key_for("status", classification, status)(second) == (
        "CLEANABLE", "same", str(second.path)
    )
    assert sort_key_for("on_disk", classification, status)(first) == (
        0, 30, "same", str(first.path)
    )
    assert sort_key_for("on_disk", classification, status)(second) == (
        1, 0, "same", str(second.path)
    )


def test_sort_key_rejects_unknown_columns(tmp_path):
    entry = Entry(tmp_path / "item", "item", "file", 1, 1)
    with pytest.raises(ValueError):
        sort_key_for("logical", lambda _: "unknown", lambda _: "READY")(entry)
