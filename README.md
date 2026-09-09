# Space Scout

Space Scout finds the directories that use your disk space, explains what they are, and lets you reclaim space safely.

## Why Space Scout

Finder and most file managers show individual file sizes, but they do not make it easy to compare the **recursive totals** of the folders you actually need to clean. Space Scout starts with an explicit directory and totals every scanned descendant, so large folders rise to the top.

`On disk` reports allocated disk space, which can differ from logical file size because of filesystem blocks and sparse files. Classification labels help distinguish likely caches, archives, source trees, and ordinary files before you act. When you do decide to clean up, Space Scout uses the operating system Trash rather than permanent deletion.

| When you want to… | Scan this explicit directory |
| --- | --- |
| Review recent downloads | `$HOME/Downloads` |
| Find a large project subdirectory | `/path/to/project` |
| Review documents or archives | `$HOME/Documents` or `/path/to/archives` |

## Quick start

```bash
uv sync --extra dev
uv run space-scout scan "$HOME/Downloads" --depth 2
```

For end users, download the native archive for your operating system from the GitHub
Releases page. Native archives do not require Python. Developers can install from a
checkout with `uv sync --extra dev` as shown above, or build a wheel with
`uv build` and install it with any Python 3.11+ environment.

Use `browse` when you want an interactive Explorer + Inspector view:

```bash
uv run space-scout browse /path/to/project
```

`browse PATH` scans every direct child of the explicit `PATH` and recursively totals
each selected directory. To limit the scan to matching direct children, repeat
`--select GLOB`:

```bash
uv run space-scout browse "$HOME" --select '.*'

uv run space-scout browse "$HOME" --select '.*' --select 'Developer'

uv run space-scout browse "$HOME/Downloads" --select '*.zip' --select '*.dmg'
```

The glob is matched against the names immediately below `PATH`; it is not a
recursive content filter. Once a directory matches, all files below it are
scanned, including non-hidden files. Without `--select`, no visible or hidden
child is omitted (subject to the normal safety policy). The TUI `/` filter only
changes which already-scanned rows are displayed and does not reduce scan time.

For stable output that a script can consume:

```bash
uv run space-scout scan /path/to/project --json
```

The command exits with `0` for a successful scan, `1` when a scan completes with warnings,
and `2` for invalid input or an operation that could not be completed. JSON output remains
valid when warnings are present.

## Browse TUI

The Explorer list adapts to terminal width. `Name` and `On disk` are always visible. At medium widths it also shows `Status`; at wide widths it adds `Logical` and `Class`. In compact mode, press `i` to switch between the list and the Inspector; long labels are ellipsized so the list never requires horizontal scrolling. Unknown `On disk` values form a separate sort bucket after entries with known allocation data in the default order (and remain shown as `—`). The Inspector holds the full path, both size measures, classification, status, modified range, warnings, and policy reason.

Common downloads such as PDF, Office documents, data files, fonts, media, and
installers receive explicit advisory classes. A file with an unrecognized
suffix is shown as `extension`; an extensionless file is `file`; and a generic
folder is `directory`. These structural labels keep the list useful without
pretending to know the file's purpose.

- `j` / `k`: move the selection; `Enter`: expand or collapse a directory
- `i`: show or hide the Inspector (in compact mode it switches between the list and Inspector)
- `s`: cycle the four sort fields — `On disk`, `Name`, `Class`, and `Status`; `S`: reverse the direction
- `/`: filter by name, class, or status (and accept `min:<bytes>`); `r`: rescan without discarding list state
- `t`: preview and move the selected eligible path to Trash; `u`: request a scan-only unlock for a protected path

## Safety

Space Scout always scans an explicit directory. It never silently scans your home directory, whole disk, or a mounted volume; select a containing directory rather than a single file root.

Cleanup is reversible: eligible paths are moved to the operating system Trash after an exact-word confirmation. Restore a trashed item through Finder on macOS, File Explorer on Windows, or the desktop Trash on Linux. Space Scout previews the selected path and `On disk` size, then re-checks policy immediately before the move. For the non-interactive CLI, `trash --yes PATH ...` skips the prompt only for those explicit paths; policy checks and the final re-check still apply.

Protected and excluded locations remain skipped by default, including their descendants. Select a protected path and use `u`, then type `unlock`, only when you deliberately need to inspect it. That unlock is limited to the current browse session and scan; it never grants permission to Trash the path.

## Not a file manager

Space Scout is an analysis and reversible-cleanup tool, not a Finder CLI or a replacement for Finder, File Explorer, or a Linux file manager. It deliberately does not copy, rename, arbitrarily move, open, edit permissions, run a shell, or permanently delete files. Use your operating system’s file manager for those jobs and for Trash recovery.

## Configuration

See configured shortcuts, exclusions, and sort preference:

```bash
uv run space-scout config list
```

## Build Native Artifacts

Install the development dependencies and build the native executable for the current operating system:

```bash
uv sync --extra dev
uv run python scripts/build.py --dist-dir dist
```

Space Scout supports Linux, macOS, and Windows. Each operating system needs its own artifact,
and each artifact targets the architecture used by its native build runner. Check the release
notes for the exact OS/architecture matrix. Keep generated artifacts out of commits.

Reports include the scanned paths. Review JSON and terminal output before sharing them,
because absolute paths can reveal usernames or local directory names.

Smoke-check the installed command before distributing a build:

```bash
uv run python scripts/smoke_test.py dist
```

## Developer Workflow

Run the focused documentation check:

```bash
uv run pytest tests/test_docs.py -q
```

Run the full test suite and public-repository check:

```bash
uv run pytest -q
uv run python scripts/check_public_repo.py .
```
