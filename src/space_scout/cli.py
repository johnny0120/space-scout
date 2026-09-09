from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from .config import Config, common_shortcuts, config_path, load_config, save_config
from .models import Entry, ScanOptions
from .output import (
    _escape,
    flatten,
    render_json,
    render_table,
    report_entry,
    report_warnings,
)
from .policy import Policy, cleanup_rejection, default_policy
from .scanner import scan
from .trash import TrashResult, trash_many


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="space-scout")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("path", metavar="PATH", type=Path)
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.add_argument("--depth", type=_depth)
    scan_parser.set_defaults(handler=_scan_command)

    browse_parser = subparsers.add_parser("browse")
    browse_parser.add_argument("path", metavar="PATH", type=Path)
    browse_parser.add_argument(
        "--select",
        dest="select_patterns",
        action="append",
        default=[],
        metavar="GLOB",
        help="select matching direct children as scan roots (repeatable; selected directories are scanned fully)",
    )
    browse_parser.set_defaults(handler=_browse_command)

    trash_parser = subparsers.add_parser("trash")
    trash_parser.add_argument("paths", metavar="PATH", type=Path, nargs="+")
    trash_parser.add_argument("--yes", action="store_true", help="skip confirmation for these explicit paths")
    trash_parser.set_defaults(handler=_trash_command)

    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_list = config_subparsers.add_parser("list")
    config_list.set_defaults(handler=_config_list_command)
    add_exclusion = config_subparsers.add_parser("add-exclusion")
    add_exclusion.add_argument("path", metavar="PATH", type=Path)
    add_exclusion.set_defaults(handler=_config_add_exclusion_command)
    config_parser.set_defaults(handler=_config_list_command)

    return parser


def _depth(value: str) -> int:
    depth = int(value)
    if depth < 0:
        raise argparse.ArgumentTypeError("depth must be non-negative")
    return depth


def _scan_command(args: argparse.Namespace) -> int:
    config = load_config()
    root = args.path.expanduser().absolute()
    policy = _configured_policy(root, config)
    snapshot = scan(ScanOptions(root, max_depth=args.depth, stay_on_filesystem=policy.stay_on_filesystem), policy)
    rows = flatten(snapshot, policy, config.overrides)
    if args.json:
        render_json(snapshot, rows, sys.stdout)
    else:
        render_table(rows, sys.stdout, snapshot)
    return 2 if report_warnings(snapshot, rows) else 0


def _configured_policy(root: Path, config: Config) -> Policy:
    policy = default_policy(root)
    return Policy(root, config.exclusions, policy.protected_roots, policy.stay_on_filesystem)


def _browse_command(args: argparse.Namespace) -> int:
    from .tui import run_browse, scan_for_browse

    config = load_config()
    root = args.path.expanduser().absolute()
    policy = _configured_policy(root, config)
    select_patterns = tuple(args.select_patterns)
    if select_patterns:
        snapshot = scan_for_browse(root, policy, select_patterns=select_patterns)
        return run_browse(snapshot, policy, config, select_patterns=select_patterns)
    else:
        snapshot = scan_for_browse(root, policy)
        return run_browse(snapshot, policy, config)


def _estimated_size(path: Path, policy: Policy) -> int:
    """Estimate bytes selected by a path without following symlinked trees."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
        snapshot = scan(ScanOptions(path, stay_on_filesystem=policy.stay_on_filesystem), policy)
        return sum(entry.logical_bytes for entry in snapshot.entries)
    except OSError:
        return 0


def _trash_command(args: argparse.Namespace) -> int:
    config = load_config()
    candidates = tuple(path.expanduser().absolute() for path in args.paths)
    eligible: list[Path] = []
    failed: list[TrashResult] = []
    for path in candidates:
        policy = _configured_policy(path, config)
        rejection = cleanup_rejection(path, policy)
        if rejection:
            print(f"{path}  size not estimated")
            print(f"rejected: {path} ({rejection})")
            failed.append(TrashResult(path, False, rejection))
            continue
        size = _estimated_size(path, policy)
        kind: Literal["file", "directory", "symlink"] = (
            "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file"
        )
        row = report_entry(Entry(path, path.name, kind, size, None), policy, config.overrides)
        print(f"{path}  {size} logical bytes (estimated)  Class: {_escape(row.classification)}")
        eligible.append(path)

    if not eligible:
        return 3

    if not args.yes:
        if not sys.stdin.isatty():
            print("confirmation required: type trash or use --yes for explicit paths")
            return 3
        try:
            answer = input("Type trash to confirm: ")
        except (EOFError, KeyboardInterrupt):
            print("trash cancelled")
            return 3
        if answer != "trash":
            print("trash cancelled")
            return 3

    results = list(failed)
    for path in eligible:
        # Re-read persisted exclusions and resolve the path again immediately
        # before each adapter call; scan unlocks never enter cleanup policy.
        rejection = cleanup_rejection(path, _configured_policy(path, load_config()))
        if rejection:
            results.append(TrashResult(path, False, f"rejected: {rejection}"))
        else:
            results.extend(trash_many((path,)))
    for result in results:
        print(f"{result.path}: {result.message}")
    return 0 if all(result.success for result in results) else 3


def _config_list_command(_: argparse.Namespace) -> int:
    config = load_config()
    shortcuts = {**common_shortcuts(), **config.shortcuts}
    print(f"config: {config_path()}")
    print("shortcuts:")
    for name, path in shortcuts.items():
        print(f"  {name}: {path}")
    print("exclusions:")
    for path in config.exclusions:
        print(f"  {path}")
    return 0


def _config_add_exclusion_command(args: argparse.Namespace) -> int:
    config = load_config()
    try:
        path = args.path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot normalize exclusion path: {exc}") from exc
    if path not in config.exclusions:
        config = Config(config.shortcuts, (*config.exclusions, path), config.overrides, config.sort_key, config.minimum_bytes)
        save_config(config)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1

    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
