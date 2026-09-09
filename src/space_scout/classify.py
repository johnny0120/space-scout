"""Deterministic advisory classifications for filesystem entries."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from typing import cast

from .policy import windows_protected_roots

_SYSTEM_ROOTS = {
    "/applications", "/system", "/library", "/private", "/windows",
    "/program files", "/programdata", "/bin", "/usr", "/etc", "/var",
}
_WINDOWS_SYSTEM_ROOT_NAMES = {
    "windows", "program files", "program files (x86)", "programdata",
}
_BUILD_NAMES = {"target", "build", "dist"}
_DEPENDENCY_NAMES = {".m2", ".gradle", ".cargo", ".npm", ".pnpm-store", ".pyenv", ".nodenv", ".rustup"}
_MODEL_NAMES = {"huggingface", "models", "checkpoints"}
_CACHE_NAMES = {".cache", "cache", "uv"}
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz", ".br"}
_MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".mp3", ".wav", ".flac", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".tif", ".tiff", ".ico", ".xcf", ".svg"}
_DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".numbers", ".key", ".rtf", ".ofd", ".md", ".txt", ".tsv", ".log", ".eml", ".odt", ".ods", ".odp", ".pages", ".tex", ".sketch", ".drawio", ".mht", ".mhtml", ".webarchive"}
_DATA_SUFFIXES = {".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml", ".toml", ".ini", ".properties", ".csv", ".har"}
_FONT_SUFFIXES = {".ttf", ".ttc", ".otf"}
_INSTALLER_SUFFIXES = {".dmg", ".pkg", ".msi", ".exe", ".deb", ".rpm", ".jar", ".war", ".vsix", ".apk", ".appimage", ".difypkg"}
_SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".cxx", ".swift", ".rb", ".php", ".sh", ".fish", ".zsh", ".css", ".scss", ".html", ".sql"}


def classify(path: Path, kind: str, size_bytes: int) -> str:
    """Return a stable advisory label based on normalized names and suffix."""
    del size_bytes  # Reserved for future size-sensitive heuristics.
    # PureWindowsPath is useful to callers inspecting Windows paths from a
    # non-Windows host, where Path.resolve() would otherwise reinterpret the
    # drive path as a relative POSIX path. Native Windows paths still resolve
    # through Path so filesystem normalization is retained there.
    normalized: Path | PureWindowsPath
    if isinstance(path, PureWindowsPath) and os.name != "nt":
        normalized = cast(Path | PureWindowsPath, path)
    else:
        normalized = cast(Path | PureWindowsPath, Path(path).resolve(strict=False))
    parts = tuple(part.casefold() for part in normalized.parts)
    names = set(parts)
    if _is_system_protected(normalized, parts):
        return "system-protected"
    if _BUILD_NAMES & names or {"node_modules", ".cache"}.issubset(names):
        return "build-artifact"
    if _DEPENDENCY_NAMES & names:
        return "dependency-store"
    if _MODEL_NAMES & names:
        return "model-cache"
    if _CACHE_NAMES & names:
        return "cache"
    if ".docker" in names:
        return "container-data"
    suffixes = normalized.name.casefold()
    if any(suffixes.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
        return "archive"
    if normalized.suffix.casefold() in _INSTALLER_SUFFIXES:
        return "installer"
    if normalized.suffix.casefold() in _DOCUMENT_SUFFIXES:
        return "document"
    if normalized.suffix.casefold() in _DATA_SUFFIXES:
        return "data"
    if normalized.suffix.casefold() in _FONT_SUFFIXES:
        return "font"
    if normalized.suffix.casefold() in _MEDIA_SUFFIXES:
        return "media"
    if normalized.suffix.casefold() in _SOURCE_SUFFIXES:
        return "source"
    # Keep the UI actionable even when a suffix is outside the advisory map:
    # the extension itself is still useful context, while directories and
    # extensionless files have clear structural classes.
    if kind == "directory":
        return "directory"
    if kind in {"file", "symlink"}:
        return "extension" if normalized.suffix else "file"
    if kind == "special":
        return "special"
    return "skipped"


def _is_system_protected(normalized: Path | PureWindowsPath, parts: tuple[str, ...]) -> bool:
    """Match protected roots without confusing drive letters for components."""
    if isinstance(normalized, PureWindowsPath):
        # WindowsPath.parts starts with an anchor such as ``C:\\``; requiring
        # that anchor prevents ``/work/windows`` from becoming protected.
        windows_root = cast(Path, normalized)
        return any(normalized == root or root in normalized.parents
                   for root in windows_protected_roots(windows_root))

    # Also recognize a drive-form path represented as a POSIX Path (for
    # example when a Windows path was serialized before reaching this host).
    if len(parts) > 1 and len(parts[0]) == 2 and parts[0][1] == ":":
        return parts[1] in _WINDOWS_SYSTEM_ROOT_NAMES

    normalized_text = normalized.as_posix().casefold()
    return any(normalized_text == root or normalized_text.startswith(root + "/") for root in _SYSTEM_ROOTS)
