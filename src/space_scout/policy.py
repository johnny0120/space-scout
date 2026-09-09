"""Conservative, read-only path policy for Space Scout."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from .models import ScanWarning


@dataclass(frozen=True, slots=True)
class Policy:
    root: Path
    exclusions: tuple[Path, ...]
    protected_roots: tuple[Path, ...]
    stay_on_filesystem: bool = True


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    status: Literal["scan", "excluded", "protected", "unlocked"]
    reason: str


def _normalized(path: Path) -> Path:
    if isinstance(path, PureWindowsPath) and os.name != "nt":
        return path
    return Path(path).resolve(strict=False)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def decide(
    path: Path,
    policy: Policy,
    unlocked: frozenset[Path] = frozenset(),
) -> PolicyDecision:
    """Return the applicable policy status for *path* and its descendants."""
    candidate = _normalized(path)
    exclusions = tuple(_normalized(item) for item in policy.exclusions)
    if any(_contains(item, candidate) for item in exclusions):
        return PolicyDecision("excluded", "path matches a configured exclusion")

    unlocks = tuple(_normalized(item) for item in unlocked)
    protected = tuple(_normalized(item) for item in policy.protected_roots)
    if any(_contains(item, candidate) for item in protected):
        if any(_contains(item, candidate) for item in unlocks):
            return PolicyDecision("unlocked", "protected path explicitly unlocked for this scan")
        matched = next(item for item in protected if _contains(item, candidate))
        return PolicyDecision("protected", f"path is in protected system location {matched}")
    return PolicyDecision("scan", "path is eligible for scanning")


def safe_decide(path: Path, policy: Policy, unlocked: frozenset[Path] = frozenset()) -> tuple[PolicyDecision, ScanWarning | None]:
    """Fail closed when a path cannot be normalized, retaining the error."""
    try:
        return decide(path, policy, unlocked), None
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Cannot evaluate path policy: {exc}"
        return PolicyDecision("excluded", message), ScanWarning(path, "policy_error", message)


def cleanup_rejection(path: Path, policy: Policy) -> str | None:
    """Reject both policy boundaries and ancestors that would move them."""
    decision, _ = safe_decide(path, policy)
    if decision.status in {"protected", "excluded"}:
        return f"{decision.status}: {decision.reason}"
    try:
        candidate = _normalized(path)
        for boundary in (*policy.exclusions, *policy.protected_roots):
            if _contains(candidate, _normalized(boundary)):
                return f"contains protected or excluded path: {boundary}"
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Cannot evaluate cleanup policy: {exc}"
    return None


def windows_protected_roots(root: Path) -> tuple[PureWindowsPath, ...]:
    """Use actual system locations plus conservative roots on the selected drive."""
    names = ("Windows", "Program Files", "Program Files (x86)", "ProgramData")
    system_drive = PureWindowsPath(os.environ.get("SystemDrive", "C:") + "/")
    selected = PureWindowsPath(root)
    selected_anchor = PureWindowsPath(selected.anchor) if selected.drive and selected.root else system_drive
    locations = [PureWindowsPath(os.environ.get(key, str(system_drive / name)))
                 for key, name in zip(("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"), names)]
    return tuple(dict.fromkeys([*(path for path in locations if path.is_absolute()),
                                *(selected_anchor / name for name in names)]))


def default_policy(root: Path, platform_name: str | None = None) -> Policy:
    """Build the conservative built-in policy for the selected platform."""
    platform = platform_name or sys.platform
    try:
        normalized_root = _normalized(root)
    except (OSError, RuntimeError, ValueError):
        # Retain the requested root so traversal can report normalization
        # failures as skipped rows instead of failing before rendering JSON.
        normalized_root = Path(root).absolute()
    protected_names: tuple[str, ...]
    if platform == "darwin":
        protected_names = ("/Applications", "/System", "/Library", "/private")
    elif platform.startswith("win"):
        return Policy(
            normalized_root,
            (),
            tuple(_normalized(path) for path in windows_protected_roots(root)),  # type: ignore[arg-type]
        )
    else:
        protected_names = ("/bin", "/usr", "/etc", "/var")
    return Policy(
        root=normalized_root,
        exclusions=(),
        protected_roots=tuple(Path(item).resolve(strict=False) for item in protected_names),
    )
