
import pytest

from space_scout.policy import Policy


@pytest.fixture
def isolated_cli(tmp_path, monkeypatch):
    """Fixture scans must not depend on host exclusions or temp-directory policy."""
    monkeypatch.setattr("space_scout.config.config_path", lambda: tmp_path / "settings.toml")
    monkeypatch.setattr("space_scout.cli.default_policy", lambda root: Policy(root, (), ()))


@pytest.fixture
def symlink_supported(tmp_path):
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to("absent")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this runner: {exc}")
    probe.unlink()
