
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


@pytest.fixture(autouse=True)
def _hermetic_toolchain(monkeypatch):
    """Keep TUI advice deterministic: no host toolchain, no resolver subprocesses.

    Tests that need a tool present override ``space_scout.tui.default_tool_present``
    inside the test; the app resolves the default at call time.
    """
    monkeypatch.setattr("space_scout.tui.default_tool_present", lambda tool: False)
    monkeypatch.setattr("space_scout.tui.default_resolve_targets", lambda tool, **kwargs: ())
