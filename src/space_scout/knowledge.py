"""Local cleanup knowledge: rules, tool methods, policy merge, and advice.

The registry stores only normalized, relative patterns and platform facts. It
never stores absolute local paths, usernames, or credentials, and it never
executes shell text. Rules return data only; adapters and the resolver consume
that data in later phases.

# allow: SIZE_OK — frozen public registry (data tables) plus three pure functions
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal, Protocol

from .models import Entry
from .policy import Policy, PolicyDecision, cleanup_rejection

AdviceCategory = Literal[
    "cache",
    "build-artifact",
    "runtime",
    "extension",
    "application-data",
    "credential",
    "other",
]
Risk = Literal["safe", "review", "protected"]
AssessmentStatus = Literal[
    "assessed", "rule_missing", "adapter_missing", "policy_error", "permission_denied"
]
MethodKind = Literal["native", "trash", "manual"]

_PLATFORMS = frozenset({"darwin", "linux", "win32"})
_UNKNOWN_IMPACT = "Unknown path; review the contents before removing."
_UNKNOWN_REASON = "no cleanup rule matched this path; review before removing"
_MANUAL = "Manual review"


@dataclass(frozen=True, slots=True)
class CleanupAdvice:
    category: AdviceCategory
    risk: Risk
    assessment_status: AssessmentStatus
    reclaimable: bool
    method: str
    impact: str
    reason: str
    target: str | None = None
    basis: str = ""
    ttl_seconds: int = 300


@dataclass(frozen=True, slots=True)
class TargetSpec:
    key: str
    default_templates: Mapping[str, str]
    env_vars: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolMethod:
    tool: str
    platforms: frozenset[str]
    query_argv: tuple[str, ...] | None
    targets: tuple[TargetSpec, ...]
    per_name_command: str | None
    whole_command: str | None
    regenerable: bool
    offline_regenerable: bool
    impact: str
    never_bulk_components: frozenset[str]


@dataclass(frozen=True, slots=True)
class KnowledgeRule:
    # ``labels`` and ``target_key`` carry defaults, so they must follow every
    # required field (dataclasses forbid a required field after a defaulted one).
    id: str
    tool: str | None
    platforms: frozenset[str]
    components: tuple[tuple[str, ...], ...]
    category: AdviceCategory
    base_risk: Risk
    method_kind: MethodKind
    method: str
    reclaimable: bool
    impact: str
    reason: str
    labels: frozenset[str] = frozenset()
    target_key: str | None = None


class ResolvedTargetLike(Protocol):
    """Structural shape of a resolver result; the resolver imports this module."""

    path: Path
    key: str
    redirected: bool
    reason: str


TOOL_METHODS: dict[str, ToolMethod] = {
    "uv": ToolMethod(
        tool="uv",
        platforms=_PLATFORMS,
        query_argv=("cache", "dir"),
        targets=(
            TargetSpec(
                key="cache",
                default_templates={
                    "darwin": "{xdg_cache}/uv",
                    "linux": "{xdg_cache}/uv",
                    "win32": "{localappdata}\\uv\\cache",
                },
                env_vars=("UV_CACHE_DIR", "XDG_CACHE_HOME"),
            ),
        ),
        per_name_command="uv cache clean <package>",
        whole_command="uv cache prune",
        regenerable=True,
        offline_regenerable=True,
        impact="uv rebuilds the cache on the next sync; nothing user-authored is removed.",
        never_bulk_components=frozenset(),
    ),
    "npm": ToolMethod(
        tool="npm",
        platforms=_PLATFORMS,
        query_argv=("config", "get", "cache"),
        targets=(
            TargetSpec(
                key="cache",
                default_templates={
                    "darwin": "{home}/.npm",
                    "linux": "{home}/.npm",
                    "win32": "{localappdata}\\npm-cache",
                },
                env_vars=("npm_config_cache",),
            ),
        ),
        per_name_command=None,
        whole_command="npm cache clean --force",
        regenerable=True,
        offline_regenerable=False,
        impact="npm cache; full re-download; unusable offline",
        never_bulk_components=frozenset({".npmrc"}),
    ),
    "bun": ToolMethod(
        tool="bun",
        platforms=_PLATFORMS,
        query_argv=("pm", "cache"),
        targets=(
            TargetSpec(
                key="cache",
                default_templates={
                    "darwin": "{home}/.bun/install/cache",
                    "linux": "{home}/.bun/install/cache",
                    "win32": "{userprofile}\\.bun\\install\\cache",
                },
                env_vars=("BUN_INSTALL_CACHE_DIR", "BUN_INSTALL"),
            ),
        ),
        per_name_command=None,
        whole_command="bun pm cache rm",
        regenerable=True,
        offline_regenerable=False,
        impact="Bun cache; full re-download; unusable offline",
        never_bulk_components=frozenset({"bin", "install/global"}),
    ),
    "go": ToolMethod(
        tool="go",
        platforms=_PLATFORMS,
        query_argv=("env", "GOCACHE", "GOMODCACHE"),
        targets=(
            TargetSpec(
                key="gocache",
                default_templates={
                    "darwin": "{home}/Library/Caches/go-build",
                    "linux": "{xdg_cache}/go-build",
                    "win32": "{localappdata}\\go-build",
                },
                env_vars=("GOCACHE",),
            ),
            TargetSpec(
                key="gomodcache",
                default_templates={
                    "darwin": "{home}/go/pkg/mod",
                    "linux": "{home}/go/pkg/mod",
                    "win32": "{userprofile}\\go\\pkg\\mod",
                },
                env_vars=("GOMODCACHE", "GOPATH"),
            ),
        ),
        per_name_command=None,
        whole_command="go clean -cache",
        regenerable=True,
        offline_regenerable=True,
        impact="Go rebuilds the build cache locally; modules need a re-download.",
        never_bulk_components=frozenset(),
    ),
    "cargo": ToolMethod(
        tool="cargo",
        platforms=_PLATFORMS,
        query_argv=None,
        targets=(
            TargetSpec(
                key="registry",
                default_templates={
                    "darwin": "{home}/.cargo/registry",
                    "linux": "{home}/.cargo/registry",
                    "win32": "{userprofile}\\.cargo\\registry",
                },
                env_vars=("CARGO_HOME",),
            ),
        ),
        per_name_command=None,
        whole_command=None,
        regenerable=True,
        offline_regenerable=False,
        impact="Cargo registry; full re-download; unusable offline",
        never_bulk_components=frozenset(
            {"bin", "credentials.toml", "config.toml", ".crates.toml", ".crates2.json"}
        ),
    ),
    "pyenv": ToolMethod(
        tool="pyenv",
        platforms=_PLATFORMS,
        query_argv=("root",),
        targets=(
            TargetSpec(
                key="versions",
                default_templates={
                    "darwin": "{home}/.pyenv/versions",
                    "linux": "{home}/.pyenv/versions",
                    "win32": "{userprofile}\\.pyenv\\versions",
                },
                env_vars=("PYENV_ROOT",),
            ),
        ),
        per_name_command="pyenv uninstall <version>",
        whole_command=None,
        regenerable=True,
        offline_regenerable=False,
        impact="Reinstalling the runtime requires a download.",
        never_bulk_components=frozenset({"versions"}),
    ),
    "rustup": ToolMethod(
        tool="rustup",
        platforms=_PLATFORMS,
        query_argv=("show", "home"),
        targets=(
            TargetSpec(
                key="toolchains",
                default_templates={
                    "darwin": "{home}/.rustup/toolchains",
                    "linux": "{home}/.rustup/toolchains",
                    "win32": "{userprofile}\\.rustup\\toolchains",
                },
                env_vars=("RUSTUP_HOME",),
            ),
        ),
        per_name_command="rustup toolchain uninstall <toolchain>",
        whole_command=None,
        regenerable=True,
        offline_regenerable=False,
        impact="Reinstalling the toolchain requires a download.",
        never_bulk_components=frozenset({"toolchains"}),
    ),
    "vscode": ToolMethod(
        tool="vscode",
        platforms=_PLATFORMS,
        query_argv=None,
        targets=(
            TargetSpec(
                key="cache",
                default_templates={
                    "darwin": "{home}/Library/Application Support/Code",
                    "linux": "{home}/.config/Code",
                    "win32": "{appdata}/Code",
                },
                env_vars=(),
            ),
            TargetSpec(
                key="extensions",
                default_templates={
                    "darwin": "{home}/.vscode/extensions",
                    "linux": "{home}/.vscode/extensions",
                    "win32": "{userprofile}\\.vscode\\extensions",
                },
                env_vars=(),
            ),
        ),
        per_name_command="code --uninstall-extension <extension>",
        whole_command=None,
        regenerable=True,
        offline_regenerable=True,
        impact="VS Code rebuilds these caches on demand; extensions need a reinstall.",
        never_bulk_components=frozenset({"User"}),
    ),
}

RULES: tuple[KnowledgeRule, ...] = (
    # --- component rules: specific, tool-owned paths -------------------------
    KnowledgeRule(
        id="uv-cache", tool="uv", platforms=_PLATFORMS,
        components=((".cache", "uv"), ("local", "uv", "cache")),
        category="cache", base_risk="safe", method_kind="native", method="uv cache prune",
        reclaimable=True,
        impact="uv rebuilds the cache on the next sync; nothing user-authored is removed.",
        reason="uv-managed cache directory; regenerated by uv",
        target_key="cache",
    ),
    KnowledgeRule(
        id="uv-runtime", tool="uv", platforms=_PLATFORMS,
        components=((".local", "share", "uv", "python"), (".local", "share", "uv", "tools")),
        category="runtime", base_risk="review", method_kind="manual", method=_MANUAL,
        reclaimable=True,
        impact="uv-managed Pythons and tools are persistent data, not cache.",
        reason="uv-managed runtime; reinstall instead of deleting",
    ),
    KnowledgeRule(
        id="npm-cache", tool="npm", platforms=_PLATFORMS,
        components=((".npm",), ("npm-cache",)),
        category="cache", base_risk="review", method_kind="native",
        method="npm cache clean --force", reclaimable=True,
        impact="npm cache; full re-download; unusable offline",
        reason="npm cache; full re-download; unusable offline",
        target_key="cache",
    ),
    KnowledgeRule(
        id="npmrc", tool=None, platforms=_PLATFORMS,
        components=((".npmrc",),),
        category="credential", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="npm credentials and registry configuration; never removed in bulk.",
        reason="npm credentials live outside the cache; protected",
    ),
    KnowledgeRule(
        id="bun-cache", tool="bun", platforms=_PLATFORMS,
        components=((".bun", "install", "cache"),),
        category="cache", base_risk="review", method_kind="native",
        method="bun pm cache rm", reclaimable=True,
        impact="Bun cache; full re-download; unusable offline",
        reason="Bun cache; full re-download; unusable offline",
        target_key="cache",
    ),
    KnowledgeRule(
        id="bun-global", tool="bun", platforms=_PLATFORMS,
        components=((".bun", "install", "global"), (".bun", "bin")),
        category="application-data", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="Installed Bun packages and binaries; reinstall with Bun, never bulk delete.",
        reason="installed Bun data; not regenerable from cache",
    ),
    KnowledgeRule(
        id="go-build-cache", tool="go", platforms=frozenset({"linux", "win32"}),
        components=((".cache", "go-build"), ("local", "go-build")),
        category="cache", base_risk="safe", method_kind="native", method="go clean -cache",
        reclaimable=True,
        impact="Go rebuilds the build cache locally; no user content.",
        reason="Go build cache; cleared with go clean -cache",
        target_key="gocache",
    ),
    KnowledgeRule(
        id="go-build-cache-macos", tool="go", platforms=frozenset({"darwin"}),
        components=(("library", "caches", "go-build"),),
        category="cache", base_risk="safe", method_kind="native", method="go clean -cache",
        reclaimable=True,
        impact="Go rebuilds the build cache locally; no user content.",
        reason="Go build cache; cleared with go clean -cache",
        target_key="gocache",
    ),
    KnowledgeRule(
        id="go-mod-cache", tool="go", platforms=_PLATFORMS,
        components=(("go", "pkg", "mod"),),
        category="cache", base_risk="review", method_kind="native", method="go clean -modcache",
        reclaimable=True,
        impact="Go module cache; full re-download; unusable offline",
        reason="Go module cache; full re-download; unusable offline",
        target_key="gomodcache",
    ),
    KnowledgeRule(
        id="cargo-registry", tool="cargo", platforms=_PLATFORMS,
        components=((".cargo", "registry"), ("cargo", "registry")),
        category="cache", base_risk="review", method_kind="trash", method="Trash",
        reclaimable=True,
        impact="Cargo registry; full re-download; unusable offline",
        reason="Cargo registry; no official cleanup command; full re-download; unusable offline",
        target_key="registry",
    ),
    KnowledgeRule(
        id="cargo-credentials", tool="cargo", platforms=_PLATFORMS,
        components=((".cargo", "credentials.toml"),),
        category="credential", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="Cargo credentials; never removed in bulk.",
        reason="Cargo credentials; protected",
    ),
    KnowledgeRule(
        id="cargo-config", tool="cargo", platforms=_PLATFORMS,
        components=((".cargo", "config.toml"),),
        category="application-data", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="Cargo configuration; never removed in bulk.",
        reason="Cargo configuration; protected",
    ),
    KnowledgeRule(
        id="cargo-bin", tool="cargo", platforms=_PLATFORMS,
        components=((".cargo", "bin"), (".cargo", ".crates.toml"), (".cargo", ".crates2.json")),
        category="application-data", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="Installed Cargo binaries; reinstall with cargo, never bulk delete.",
        reason="installed Cargo binaries; not regenerable from cache",
    ),
    KnowledgeRule(
        id="pyenv-versions", tool="pyenv", platforms=_PLATFORMS,
        components=((".pyenv", "versions"),),
        category="runtime", base_risk="review", method_kind="native",
        method="pyenv uninstall <version>", reclaimable=True,
        impact="Installed Python runtimes; reinstall requires a download.",
        reason="pyenv runtime versions; uninstall one named version",
    ),
    KnowledgeRule(
        id="rustup-toolchains", tool="rustup", platforms=_PLATFORMS,
        components=((".rustup", "toolchains"),),
        category="runtime", base_risk="review", method_kind="native",
        method="rustup toolchain uninstall <toolchain>", reclaimable=True,
        impact="Installed Rust toolchains; reinstall requires a download.",
        reason="rustup toolchains; uninstall one named toolchain",
    ),
    KnowledgeRule(
        id="vscode-cache", tool="vscode", platforms=_PLATFORMS,
        components=(
            ("code", "cache"),
            ("code", "cacheddata"),
            ("code", "gpucache"),
            ("code", "code cache"),
            ("code", "logs"),
        ),
        category="cache", base_risk="safe", method_kind="trash", method="Trash",
        reclaimable=True,
        impact="VS Code rebuilds these caches on demand; no user content.",
        reason="VS Code cache directory; rebuilt on demand",
        target_key="cache",
    ),
    KnowledgeRule(
        id="vscode-extensions", tool="vscode", platforms=_PLATFORMS,
        components=((".vscode", "extensions"),),
        category="extension", base_risk="review", method_kind="native",
        method="code --uninstall-extension <extension>", reclaimable=True,
        impact="Extensions are removed by VS Code; reinstalling requires a download.",
        reason="VS Code extensions; use code --uninstall-extension, never delete directories",
        target_key="extensions",
    ),
    KnowledgeRule(
        id="vscode-user", tool="vscode", platforms=_PLATFORMS,
        components=(("code", "user"),),
        category="application-data", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="VS Code user settings and state; never removed in bulk.",
        reason="VS Code user data; protected",
    ),
    KnowledgeRule(
        id="jetbrains-localhistory", tool=None, platforms=_PLATFORMS,
        components=(("localhistory",),),
        category="application-data", base_risk="review", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="JetBrains local history may hold user-recoverable edits; excluded pending verification.",
        reason="excluded pending verification; LocalHistory may hold user data",
    ),
    # --- label rules: classify fallbacks, after every component rule ---------
    KnowledgeRule(
        id="label-system-protected", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"system-protected"}),
        category="other", base_risk="protected", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="System-protected location; never removed by Space Scout.",
        reason="system-protected location; policy remains the authority",
    ),
    KnowledgeRule(
        id="label-build-artifact", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"build-artifact"}),
        category="build-artifact", base_risk="review", method_kind="trash", method="Trash",
        reclaimable=True,
        impact="Build output; regenerated by rebuilding the project.",
        reason="build artifact; regenerable by rebuilding, review first",
    ),
    KnowledgeRule(
        id="label-dependency-store", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"dependency-store"}),
        category="cache", base_risk="review", method_kind="trash", method="Trash",
        reclaimable=True,
        impact="Dependency store; full re-download; unusable offline",
        reason="dependency store; full re-download; unusable offline",
    ),
    KnowledgeRule(
        id="label-model-cache", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"model-cache"}),
        category="cache", base_risk="review", method_kind="manual", method=_MANUAL,
        reclaimable=True,
        impact="Model data is large to re-download; review before removing.",
        reason="model cache; re-download is large and may be offline-hostile",
    ),
    KnowledgeRule(
        id="label-container-data", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"container-data"}),
        category="application-data", base_risk="review", method_kind="manual", method=_MANUAL,
        reclaimable=False,
        impact="Container data may hold images, volumes, and local state; review first.",
        reason="container data; manage with the container runtime, review first",
    ),
    KnowledgeRule(
        id="label-cache", tool=None, platforms=_PLATFORMS,
        components=(), labels=frozenset({"cache"}),
        category="cache", base_risk="review", method_kind="trash", method="Trash",
        reclaimable=True,
        impact="Generic cache; regenerated on demand but verify the contents first.",
        reason="generic cache; verify contents before removing",
    ),
)

_SUBTREE_EXCLUSIONS: tuple[tuple[str, ...], ...] = (
    ("credentials.toml",),
    ("config.toml",),
    (".npmrc",),
    ("install", "global"),
    ("bin",),
    ("versions",),
    ("toolchains",),
    ("user",),
    ("localhistory",),
)


def _casefold_parts(path: Path | PureWindowsPath) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def _suffix_match(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    return bool(pattern) and len(pattern) <= len(parts) and parts[-len(pattern):] == pattern


def _matches(rule: KnowledgeRule, parts: tuple[str, ...], platform: str, classification: str) -> bool:
    if platform not in rule.platforms:
        return False
    if any(_suffix_match(parts, pattern) for pattern in rule.components):
        return True
    return classification.casefold() in rule.labels


def match_rule(
    path: Path | PureWindowsPath, platform: str, classification: str
) -> KnowledgeRule | None:
    """Return the first specificity-ordered rule that matches, else ``None``."""
    parts = _casefold_parts(path)
    for rule in RULES:
        if _matches(rule, parts, platform, classification):
            return rule
    return None


def _exclusion_patterns(tool: str | None) -> tuple[tuple[str, ...], ...]:
    patterns = list(_SUBTREE_EXCLUSIONS)
    method = TOOL_METHODS.get(tool) if tool is not None else None
    if method is not None:
        patterns.extend(tuple(part.split("/")) for part in method.never_bulk_components)
    return tuple(patterns)


def _subtree_conflicts(entry: Entry, tool: str | None) -> tuple[str, ...]:
    patterns = _exclusion_patterns(tool)
    conflicts: list[str] = []

    def visit(node: Entry, prefix: tuple[str, ...]) -> None:
        for child in node.children:
            parts = prefix + (child.name.casefold(),)
            for pattern in patterns:
                if _suffix_match(parts, pattern):
                    conflicts.append("/".join(parts[-len(pattern):]))
                    break
            else:
                visit(child, parts)

    visit(entry, ())
    return tuple(dict.fromkeys(conflicts))


def parent_subtree_safe(entry: Entry, rule: KnowledgeRule) -> tuple[bool, tuple[str, ...]]:
    """Report whether a bulk action over ``entry`` is conflict-free by name."""
    conflicts = _subtree_conflicts(entry, rule.tool)
    return (not conflicts, conflicts)


def subtree_conflicts(entry: Entry) -> tuple[str, ...]:
    """Names of protected subtree entries under ``entry`` (exclusion semantics)."""
    return _subtree_conflicts(entry, None)


def merge_policy(
    decision: PolicyDecision, rule_risk: Risk | None, assessment: AssessmentStatus
) -> tuple[Risk, bool, AssessmentStatus]:
    """Combine the policy decision with the rule result; policy dominates."""
    if decision.status in {"excluded", "protected"}:
        return ("protected", False, assessment)
    if decision.status == "unlocked":
        return ("review", False, assessment)
    if assessment in {"policy_error", "permission_denied"}:
        return ("protected", False, assessment)
    if assessment in {"rule_missing", "adapter_missing"}:
        return ("review", True, assessment)
    if rule_risk == "safe":
        return ("safe", True, assessment)
    if rule_risk == "protected":
        return ("protected", False, assessment)
    return ("review", True, assessment)


def _path_text(path: Path | PureWindowsPath) -> str:
    if isinstance(path, PureWindowsPath):
        return path.as_posix().casefold()
    return os.path.normpath(str(path))


def _basis_hash(
    platform: str, rule_id: str, policy_status: str, path: str, target: str | None
) -> str:
    material = "|".join((platform, rule_id, policy_status, path, target or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_advice(
    entry: Entry,
    decision: PolicyDecision,
    policy: Policy,
    *,
    platform: str,
    classification: str,
    tool_present: Callable[[str], bool] | None = None,
    resolve_targets: Callable[[str], tuple[ResolvedTargetLike, ...]] | None = None,
) -> CleanupAdvice:
    """Build one deterministic, policy-first advice record for ``entry``."""
    rule = match_rule(entry.path, platform, classification)
    rule_id = rule.id if rule is not None else ""
    rejection = cleanup_rejection(entry.path, policy)
    if rejection is not None:
        # Policy is evaluated first: show the policy reason only, never a command.
        return CleanupAdvice(
            category=rule.category if rule is not None else "other",
            risk="protected",
            assessment_status="assessed",
            reclaimable=False,
            method=_MANUAL,
            impact=rule.impact if rule is not None else _UNKNOWN_IMPACT,
            reason=rejection,
            basis=_basis_hash(platform, rule_id, "protected", _path_text(entry.path), None),
        )

    assessment: AssessmentStatus = "assessed"
    if rule is None:
        category: AdviceCategory = "other"
        rule_risk: Risk | None = None
        impact = _UNKNOWN_IMPACT
        reason = _UNKNOWN_REASON
        reclaimable = False
        method = "Trash"
        target_key: str | None = None
        assessment = "rule_missing"
    else:
        category = rule.category
        rule_risk = rule.base_risk
        impact = rule.impact
        reason = rule.reason
        reclaimable = rule.reclaimable
        method = rule.method
        target_key = rule.target_key
        if rule.tool is not None and tool_present is not None and not tool_present(rule.tool):
            assessment = "adapter_missing"
            method = "Trash"
            reason = f"{rule.tool} executable not found; moving to Trash is the fallback"

    risk, cleanable, assessment = merge_policy(decision, rule_risk, assessment)
    if risk == "protected" or not cleanable:
        method = _MANUAL

    if entry.children:
        conflicts = _subtree_conflicts(entry, rule.tool if rule is not None else None)
        if conflicts:
            method = _MANUAL
            risk = "review" if risk == "safe" else risk
            reclaimable = False
            reason = f"{reason}; subtree contains protected entries: {', '.join(conflicts)}"

    target: str | None = None
    if (
        resolve_targets is not None
        and target_key is not None
        and rule is not None
        and rule.tool is not None
    ):
        resolved = resolve_targets(rule.tool)
        chosen = next((item for item in resolved if item.key == target_key), None)
        if chosen is not None:
            target = str(chosen.path)
            if chosen.redirected:
                risk = "review" if risk == "safe" else risk
                if chosen.reason:
                    reason = f"{reason}; redirected: {chosen.reason}"

    return CleanupAdvice(
        category=category,
        risk=risk,
        assessment_status=assessment,
        reclaimable=reclaimable,
        method=method,
        impact=impact,
        reason=reason,
        target=target,
        basis=_basis_hash(platform, rule_id, decision.status, _path_text(entry.path), target),
    )
