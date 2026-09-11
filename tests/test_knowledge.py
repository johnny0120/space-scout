"""Tests for the cleanup knowledge registry, merge truth table, and advice builder."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Literal, cast

import pytest

from space_scout import knowledge
from space_scout.knowledge import (
    AssessmentStatus,
    Risk,
    build_advice,
    match_rule,
    merge_policy,
    parent_subtree_safe,
)
from space_scout.models import Entry
from space_scout.policy import Policy, PolicyDecision, cleanup_rejection

EntryKind = Literal["file", "directory", "symlink", "special", "skipped"]
PolicyStatus = Literal["scan", "excluded", "protected", "unlocked"]


def _entry(
    path: Path,
    name: str,
    kind: EntryKind = "directory",
    children: tuple[Entry, ...] = (),
) -> Entry:
    return Entry(
        path=path,
        name=name,
        kind=kind,
        logical_bytes=100,
        allocated_bytes=100,
        children=children,
    )


def _decision(status: PolicyStatus = "scan", reason: str = "path is eligible for scanning") -> PolicyDecision:
    return PolicyDecision(status, reason)


def _policy(root: Path) -> Policy:
    return Policy(root, (), ())


@pytest.mark.parametrize(
    ("status", "rule_risk", "assessment", "expected_risk", "expected_cleanable"),
    [
        ("excluded", "safe", "assessed", "protected", False),
        ("protected", "safe", "assessed", "protected", False),
        ("unlocked", "safe", "assessed", "review", False),
        ("scan", "safe", "assessed", "safe", True),
        ("scan", "review", "assessed", "review", True),
        ("scan", "protected", "assessed", "protected", False),
        ("scan", None, "rule_missing", "review", True),
        ("scan", None, "adapter_missing", "review", True),
        ("scan", "safe", "policy_error", "protected", False),
        ("scan", "safe", "permission_denied", "protected", False),
    ],
)
def test_merge_policy_truth_table(
    status: PolicyStatus,
    rule_risk: Risk | None,
    assessment: AssessmentStatus,
    expected_risk: Risk,
    expected_cleanable: bool,
) -> None:
    decision = PolicyDecision(status, "reason")
    assert merge_policy(decision, rule_risk, assessment) == (
        expected_risk,
        expected_cleanable,
        assessment,
    )


def test_unlocked_merges_to_review_not_cleanable() -> None:
    decision = PolicyDecision("unlocked", "protected path explicitly unlocked for this scan")
    assert merge_policy(decision, "safe", "assessed") == ("review", False, "assessed")


def test_excluded_and_protected_merge_to_protected() -> None:
    for status in ("excluded", "protected"):
        for rule_risk in ("safe", "review", "protected", None):
            merged = merge_policy(
                PolicyDecision(cast(PolicyStatus, status), "reason"),
                cast(Risk | None, rule_risk),
                "assessed",
            )
            assert merged == ("protected", False, "assessed")


def test_rule_missing_and_adapter_missing_are_review_cleanable() -> None:
    for assessment in ("rule_missing", "adapter_missing"):
        assert merge_policy(
            PolicyDecision("scan", "reason"),
            None,
            cast(AssessmentStatus, assessment),
        ) == ("review", True, assessment)


def test_policy_error_and_permission_denied_are_protected() -> None:
    for assessment in ("policy_error", "permission_denied"):
        assert merge_policy(
            PolicyDecision("scan", "reason"),
            "safe",
            cast(AssessmentStatus, assessment),
        ) == ("protected", False, assessment)


def test_uv_cache_rule_matches_posix_components() -> None:
    rule = match_rule(Path("/opt/u/.cache/uv"), "linux", "cache")
    assert rule is not None
    assert rule.id == "uv-cache"
    assert rule.category == "cache"
    assert rule.base_risk == "safe"


def test_uv_cache_rule_matches_windows_components() -> None:
    rule = match_rule(PureWindowsPath("C:/w/u/AppData/Local/uv/cache"), "win32", "cache")
    assert rule is not None
    assert rule.id == "uv-cache"
    folded = match_rule(PureWindowsPath("C:/W/U/APPDATA/LOCAL/UV/CACHE"), "win32", "cache")
    assert folded is not None
    assert folded.id == "uv-cache"


def test_rules_are_platform_gated() -> None:
    assert match_rule(Path("/opt/u/Library/Caches/go-build"), "win32", "directory") is None
    rule = match_rule(Path("/opt/u/Library/Caches/go-build"), "darwin", "directory")
    assert rule is not None
    assert rule.id == "go-build-cache-macos"


def test_safe_tier_tightening(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    review_cases = (
        (".npm", "dependency-store"),
        ("go/pkg/mod", "dependency-store"),
        (".bun/install/cache", "cache"),
        (".cargo/registry", "dependency-store"),
    )
    for relative, classification in review_cases:
        path = tmp_path / relative
        advice = build_advice(
            _entry(path, path.name), _decision(), policy,
            platform="linux", classification=classification,
        )
        assert advice.risk == "review", relative
        assert "full re-download; unusable offline" in advice.reason, relative

    safe_cases = ((".cache/uv", "cache"), (".cache/go-build", "cache"))
    for relative, classification in safe_cases:
        path = tmp_path / relative
        advice = build_advice(
            _entry(path, path.name), _decision(), policy,
            platform="linux", classification=classification,
        )
        assert advice.risk == "safe", relative


def test_credentials_are_protected(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    cases = (
        (".npmrc", "file", "credential", "protected", "Manual review"),
        (".cargo/credentials.toml", "file", "data", "protected", "Manual review"),
        (".cargo/config.toml", "file", "data", "protected", "Manual review"),
        (".bun/install/global", "directory", "directory", "protected", "Manual review"),
        (".pyenv/versions", "directory", "dependency-store", "review",
         "pyenv uninstall <version>"),
        (".rustup/toolchains", "directory", "dependency-store", "review",
         "rustup toolchain uninstall <toolchain>"),
        ("Library/Application Support/Code/User", "directory", "directory", "protected",
         "Manual review"),
    )
    for relative, kind, classification, expected_risk, expected_method in cases:
        path = tmp_path / relative
        advice = build_advice(
            _entry(path, path.name, kind=cast(EntryKind, kind)), _decision(), policy,
            platform="darwin", classification=classification,
        )
        assert advice.risk == expected_risk, relative
        assert advice.method == expected_method, relative

    history = tmp_path / ".cache" / "JetBrains" / "GoLand" / "LocalHistory"
    advice = build_advice(
        _entry(history, "LocalHistory"), _decision(), policy,
        platform="linux", classification="cache",
    )
    assert advice.risk == "review"
    assert "excluded pending verification" in advice.reason
    assert advice.method == "Manual review"


def test_parent_subtree_rule_blocks_bulk(tmp_path: Path) -> None:
    child = _entry(tmp_path / ".cargo" / "credentials.toml", "credentials.toml", kind="file")
    parent = _entry(tmp_path / ".cargo", ".cargo", children=(child,))
    advice = build_advice(
        parent, _decision(), _policy(tmp_path), platform="linux", classification="dependency-store"
    )
    assert advice.method == "Manual review"
    assert "credentials.toml" in advice.reason
    assert advice.risk != "safe"


def test_advice_generation_passes_policy_first(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    entry = _entry(protected / "cache", "cache")
    policy = Policy(tmp_path, (), (protected,))
    decision = PolicyDecision("protected", "path is in protected system location")
    rejection = cleanup_rejection(entry.path, policy)
    assert rejection is not None
    advice = build_advice(entry, decision, policy, platform="linux", classification="cache")
    assert advice.method == "Manual review"
    assert advice.reason == rejection
    assert advice.risk == "protected"


def test_unknown_path_is_rule_missing_review(tmp_path: Path) -> None:
    entry = _entry(tmp_path / "mystery.bin", "mystery.bin", kind="file")
    advice = build_advice(
        entry, _decision(), _policy(tmp_path), platform="linux", classification="extension"
    )
    assert advice.assessment_status == "rule_missing"
    assert advice.risk == "review"
    assert advice.method.strip() != ""


def test_method_never_empty_for_scanned_entries(tmp_path: Path) -> None:
    cases = (
        (".cache/uv", "cache"),
        (".npm", "dependency-store"),
        (".cargo/registry", "dependency-store"),
        (".bun/install/global", "directory"),
        (".pyenv/versions", "dependency-store"),
        (".rustup/toolchains", "dependency-store"),
        ("Library/Application Support/Code/User", "directory"),
        ("mystery.bin", "extension"),
    )
    policy = _policy(tmp_path)
    for relative, classification in cases:
        path = tmp_path / relative
        advice = build_advice(
            _entry(path, path.name, kind="file"), _decision(), policy,
            platform="linux", classification=classification,
        )
        assert advice.method.strip() != "", relative


def test_adapter_missing_when_tool_absent(tmp_path: Path) -> None:
    path = tmp_path / ".cache" / "uv"
    entry = _entry(path, "uv")
    absent = build_advice(
        entry, _decision(), _policy(tmp_path), platform="linux", classification="cache",
        tool_present=lambda tool: False,
    )
    assert absent.assessment_status == "adapter_missing"
    assert absent.method == "Trash"
    assert absent.risk == "review"

    present = build_advice(
        entry, _decision(), _policy(tmp_path), platform="linux", classification="cache",
        tool_present=lambda tool: True,
    )
    assert present.assessment_status == "assessed"
    assert present.method == "uv cache prune"


def test_advice_target_from_resolver(tmp_path: Path) -> None:
    path = tmp_path / ".cache" / "uv"
    entry = _entry(path, "uv")
    resolved = tmp_path / "resolved-uv-cache"
    seen: list[str] = []

    class FakeTarget:
        key = "cache"
        redirected = False
        reason = "uv reports its cache directory"

        def __init__(self, target: Path) -> None:
            self.path = target

    def fake_resolve(tool: str) -> tuple[FakeTarget, ...]:
        seen.append(tool)
        return (FakeTarget(resolved),)

    advice = build_advice(
        entry, _decision(), _policy(tmp_path), platform="linux", classification="cache",
        resolve_targets=fake_resolve,
    )
    assert seen == ["uv"]
    assert advice.target == str(resolved)


def test_advice_target_selects_by_key(tmp_path: Path) -> None:
    path = tmp_path / "go" / "pkg" / "mod"
    entry = _entry(path, "mod")
    path_a = tmp_path / "resolved-gocache"
    path_b = tmp_path / "resolved-gomodcache"

    class FakeTarget:
        def __init__(self, key: str, target: Path) -> None:
            self.key = key
            self.path = target
            self.redirected = False
            self.reason = "resolver report"

    def fake_resolve(tool: str) -> tuple[FakeTarget, FakeTarget]:
        assert tool == "go"
        return (FakeTarget("gocache", path_a), FakeTarget("gomodcache", path_b))

    advice = build_advice(
        entry, _decision(), _policy(tmp_path), platform="linux", classification="cache",
        resolve_targets=fake_resolve,
    )
    assert advice.target == str(path_b)


def test_subtree_exclusions_include_npmrc(tmp_path: Path) -> None:
    npmrc = _entry(tmp_path / ".npmrc", ".npmrc", kind="file")
    entry = _entry(tmp_path, "project", children=(npmrc,))
    rule = match_rule(Path("/somewhere/.npmrc"), "linux", "credential")
    assert rule is not None
    assert rule.tool is None
    safe, conflicts = parent_subtree_safe(entry, rule)
    assert safe is False
    assert ".npmrc" in conflicts


def test_subtree_conflicts_reports_exclusion_names(tmp_path: Path) -> None:
    npmrc = _entry(tmp_path / ".npmrc", ".npmrc", kind="file")
    entry = _entry(tmp_path, "project", children=(npmrc,))
    assert knowledge.subtree_conflicts(entry) == (".npmrc",)


def test_basis_hash_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / ".cache" / "uv"
    entry = _entry(path, "uv")
    policy = _policy(tmp_path)
    first = build_advice(entry, _decision(), policy, platform="darwin", classification="cache")
    second = build_advice(entry, _decision(), policy, platform="darwin", classification="cache")
    other = build_advice(entry, _decision(), policy, platform="linux", classification="cache")
    assert first.basis
    assert first.basis == second.basis
    assert other.basis != first.basis


def test_registry_contains_no_absolute_home_paths() -> None:
    source = Path(knowledge.__file__).read_text(encoding="utf-8")
    for literal in ("/Users/", "/home/", "C:\\Users", "C:/Users"):
        assert literal not in source, literal
