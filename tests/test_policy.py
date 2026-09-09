from pathlib import Path

from space_scout.policy import Policy, decide, default_policy


def test_macos_applications_is_protected():
    policy = default_policy(Path("/"), platform_name="darwin")
    decision = decide(Path("/Applications"), policy)
    assert decision.status == "protected"
    assert "application" in decision.reason.lower()


def test_unlock_changes_scan_status_only_for_exact_path():
    policy = default_policy(Path("/"), platform_name="darwin")
    decision = decide(Path("/Applications"), policy, frozenset({Path("/Applications")}))
    assert decision.status == "unlocked"


def test_exclusions_are_reported_separately_from_protected_paths(tmp_path: Path):
    excluded = tmp_path / "excluded"
    policy = Policy(tmp_path, (excluded,), ())
    assert decide(excluded / "child", policy).status == "excluded"


def test_unlock_applies_to_protected_descendants_but_not_siblings():
    policy = default_policy(Path("/"), platform_name="darwin")
    unlocked = frozenset({Path("/Applications")})
    assert decide(Path("/Applications/tool"), policy, unlocked).status == "unlocked"
    assert decide(Path("/ApplicationStore"), policy, unlocked).status != "unlocked"

