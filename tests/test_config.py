from pathlib import Path

import pytest

from space_scout.config import Config, load_config, save_config


def test_config_round_trips_as_plain_text(tmp_path: Path):
    path = tmp_path / "config.toml"
    original = Config({"downloads": tmp_path / "Downloads"}, (tmp_path / ".cache",), {"uv": "cache"})
    save_config(original, path)
    assert load_config(path) == original


def test_missing_config_has_no_exclusions(tmp_path: Path):
    config = load_config(tmp_path / "missing.toml")
    assert config.exclusions == ()
    assert config.sort_key == "size"


def test_config_round_trips_special_labels_and_control_characters(tmp_path: Path):
    path = tmp_path / "config.toml"
    original = Config(
        {"foo.bar label\t\"\x01": tmp_path / "a\tb\x02"},
        (),
        {"override.name\n": "cache\tvalue\n\x03"},
    )

    save_config(original, path)

    assert load_config(path) == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shortcuts", {"downloads": "not-a-path-object"}),
        ("shortcuts", {1: Path("downloads")}),
        ("exclusions", ("not-a-path-object",)),
        ("overrides", {"cache": 1}),
        ("overrides", {1: "cache"}),
        ("sort_key", 1),
        ("minimum_bytes", True),
        ("minimum_bytes", "1"),
    ],
)
def test_save_config_rejects_invalid_field_types(tmp_path: Path, field: str, value: object):
    values = {
        "shortcuts": {},
        "exclusions": (),
        "overrides": {},
        "sort_key": "size",
        "minimum_bytes": 0,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        save_config(Config(**values), tmp_path / "config.toml")  # type: ignore[arg-type]
