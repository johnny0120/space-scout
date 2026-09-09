"""Filesystem traversal for Space Scout."""

from __future__ import annotations

import os
import stat
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

from .models import Entry, ScanOptions, ScanSnapshot, ScanWarning
from .policy import Policy, safe_decide


class _Scanner:
    def __init__(
        self,
        options: ScanOptions,
        root_device: int,
        root_identity: object,
        root_path: Path,
        policy: Policy | None = None,
    ):
        self.options = options
        self.policy = policy
        self.root_device = root_device
        self.root_path = root_path
        self.warnings: list[ScanWarning] = []
        # Keep identities of directories on the current recursion path.  This
        # catches symlinks back to an ancestor without relying on recursion
        # limits (and still permits the same directory in separate branches).
        self.active_directories: set[object] = {root_identity}

    @staticmethod
    def directory_identity(path: Path, metadata: os.stat_result) -> object:
        if os.name == "nt":
            # Windows inode values are frequently zero or reused. A resolved
            # path gives stable cycle detection for junctions and symlinks.
            return str(path.resolve(strict=False)).casefold()
        return (metadata.st_dev, metadata.st_ino)

    def warn(self, path: Path, code: str, message: str) -> None:
        self.warnings.append(ScanWarning(path, code, message))

    @staticmethod
    def allocated_bytes(metadata: os.stat_result) -> int | None:
        blocks = getattr(metadata, "st_blocks", None)
        return None if blocks is None else blocks * 512

    def skipped(self, path: Path, name: str, code: str, message: str) -> Entry:
        self.warn(path, code, message)
        return Entry(path, name, "skipped", 0, None, warning=message)

    def different_filesystem(self, path: Path, metadata: os.stat_result) -> bool:
        if not self.options.stay_on_filesystem:
            return False
        if os.name == "nt":
            # Windows st_dev values are not stable for temporary junctions and
            # mount points. Follow-symlinks is disabled by default, so policy
            # boundaries remain the effective safety guard on this platform.
            return False
        return metadata.st_dev != self.root_device

    def scan_entry(self, entry: os.DirEntry[str], depth: int) -> Entry:
        path = Path(entry.path)
        if self.policy is not None:
            blocked, warning = _policy_entry(path, self.policy, self.options.unlocked_paths)
            if warning:
                self.warnings.append(warning)
            if blocked:
                return blocked
        try:
            metadata = entry.stat(follow_symlinks=False)
        except (PermissionError, FileNotFoundError, OSError, RecursionError) as exc:
            return self.skipped(path, entry.name, "stat_error", str(exc))

        mode = metadata.st_mode
        allocated = self.allocated_bytes(metadata)
        modified = metadata.st_mtime_ns
        if stat.S_ISLNK(mode):
            if not self.options.follow_symlinks:
                message = None
                # Probe target metadata only to report dangling/unreadable
                # links. Never traverse or count the target in this mode.
                try:
                    entry.stat(follow_symlinks=True)
                except (FileNotFoundError, NotADirectoryError) as exc:
                    message = f"symlink target missing: {exc}"
                    self.warn(path, "broken_symlink", message)
                except (OSError, RecursionError) as exc:
                    message = f"cannot inspect symlink target: {exc}"
                    self.warn(path, "symlink_target_error", message)
                return Entry(path, entry.name, "symlink", metadata.st_size, allocated,
                             warning=message, modified_min_ns=modified, modified_max_ns=modified)
            try:
                target = entry.stat(follow_symlinks=True)
            except (PermissionError, FileNotFoundError, OSError, RecursionError) as exc:
                return self.skipped(path, entry.name, "stat_error", str(exc))
            if self.different_filesystem(path, target):
                return self.skipped(path, entry.name, "different_filesystem", "mount point skipped")
            if stat.S_ISDIR(target.st_mode):
                return self.scan_directory(
                    path, entry.name, depth, allocated,
                    identity=self.directory_identity(path, target), modified_ns=modified, symlink=True,
                )
            return Entry(path, entry.name, "symlink", target.st_size, allocated,
                         modified_min_ns=modified, modified_max_ns=modified)

        if stat.S_ISDIR(mode):
            if self.different_filesystem(path, metadata):
                return self.skipped(path, entry.name, "different_filesystem", "mount point skipped")
            return self.scan_directory(
                path, entry.name, depth, allocated,
                identity=self.directory_identity(path, metadata), modified_ns=modified,
            )
        if stat.S_ISREG(mode):
            return Entry(path, entry.name, "file", metadata.st_size, allocated,
                         modified_min_ns=modified, modified_max_ns=modified)
        message = "special file skipped"
        self.warn(path, "special_file", message)
        return Entry(path, entry.name, "special", metadata.st_size, allocated, warning=message,
                     modified_min_ns=modified, modified_max_ns=modified)

    def scan_directory(
        self,
        path: Path,
        name: str,
        depth: int,
        allocated: int | None,
        *,
        identity: object,
        modified_ns: int,
        symlink: bool = False,
    ) -> Entry:
        kind: Literal["directory", "symlink"] = "symlink" if symlink else "directory"
        status_message: str | None = None
        scan_error = False
        if identity in self.active_directories:
            return self.skipped(path, name, "cycle_detected", "directory cycle skipped")
        if self.options.max_depth is not None and depth >= self.options.max_depth:
            status_message = "maximum scan depth reached"
            self.warn(path, "depth_limited", status_message)
            try:
                direct_size = path.stat(follow_symlinks=True).st_size
            except (PermissionError, FileNotFoundError, OSError):
                direct_size = 0
            return Entry(path, name, kind, direct_size, None, warning=status_message,
                         modified_min_ns=modified_ns, modified_max_ns=modified_ns)
        children: list[Entry] = []
        self.active_directories.add(identity)
        try:
            with os.scandir(path) as directory:
                for scanned_entry in directory:
                    children.append(self.scan_entry(scanned_entry, depth + 1))
        except (PermissionError, FileNotFoundError, OSError, RecursionError) as exc:
            status_message = str(exc)
            scan_error = True
            self.warn(path, "scan_error", status_message)
        finally:
            self.active_directories.remove(identity)
        logical = sum(child.logical_bytes for child in children)
        if allocated is None or scan_error:
            recursive_allocated = None
        else:
            recursive_allocated = allocated
            for child_entry in children:
                if child_entry.allocated_bytes is None:
                    recursive_allocated = None
                    break
                recursive_allocated += child_entry.allocated_bytes
        oldest = min([modified_ns, *(child.modified_min_ns for child in children if child.modified_min_ns is not None)])
        newest = max([modified_ns, *(child.modified_max_ns for child in children if child.modified_max_ns is not None)])
        return Entry(
            path,
            name,
            kind,
            logical,
            recursive_allocated,
            tuple(sorted(children, key=lambda item: item.name)),
            warning=status_message,
            modified_min_ns=oldest,
            modified_max_ns=newest,
        )


def _policy_entry(path: Path, policy: Policy, unlocked: frozenset[Path]) -> tuple[Entry | None, ScanWarning | None]:
    decision, warning = safe_decide(path, policy, unlocked)
    if decision.status in {"protected", "excluded"}:
        return Entry(path, path.name or str(path), "skipped", 0, None, warning=decision.reason), warning
    return None, warning


def scan(options: ScanOptions, policy: Policy | None = None) -> ScanSnapshot:
    """Return an immutable snapshot of the direct children under ``options.root``."""
    root = Path(options.root)
    if policy is not None:
        blocked, warning = _policy_entry(root, policy, options.unlocked_paths)
        if blocked:
            return ScanSnapshot(root, (blocked,), (warning,) if warning else ())
    try:
        root_metadata = root.stat(follow_symlinks=True)
    except (PermissionError, FileNotFoundError, OSError, RecursionError) as exc:
        warning = ScanWarning(root, "root_error", str(exc))
        return ScanSnapshot(root, (), (warning,))
    if not stat.S_ISDIR(root_metadata.st_mode):
        return ScanSnapshot(root, (), (ScanWarning(root, "root_not_directory", "scan root must be a directory"),))
    scanner = _Scanner(
        options,
        root_metadata.st_dev,
        _Scanner.directory_identity(root, root_metadata),
        root,
        policy,
    )
    entries: list[Entry] = []
    try:
        with os.scandir(root) as directory:
            for child in directory:
                if options.select_patterns and not any(
                    fnmatchcase(child.name, pattern) for pattern in options.select_patterns
                ):
                    continue
                entries.append(scanner.scan_entry(child, 1))
    except (PermissionError, FileNotFoundError, OSError, RecursionError) as exc:
        scanner.warn(root, "scan_error", str(exc))
    entries.sort(key=lambda entry: entry.name)
    return ScanSnapshot(root, tuple(entries), tuple(scanner.warnings))
