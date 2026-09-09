# Space Scout design

Space Scout is a cross-platform disk-usage explorer and reversible cleanup assistant.
It scans only a directory explicitly supplied by the user, builds a recursive snapshot,
and presents the same data through the CLI, JSON output, and the Explorer + Inspector TUI.

## Safety boundaries

- The scanner never expands an omitted path into the home directory or whole disk.
- Protected and excluded locations are skipped by default and reported with a reason.
- Cleanup moves an explicitly selected path to the operating-system Trash after confirmation.
- A scan-only unlock is temporary and never grants permission to trash a protected path.

## Size model

Every entry has a logical byte count. Filesystems that expose allocation metadata also
provide an `On disk` count; directory totals include descendants. If any descendant's
allocation is unavailable, the directory's `On disk` value is shown as unavailable rather
than guessed.

## Classification

Classification is advisory. Known document, archive, media, source, cache, and installer
patterns receive useful labels. Unrecognized files use structural labels such as
`extension`, `file`, or `directory`; the UI does not present an opaque `unknown` class.

## Distribution

Python users can install the package with `uv` or a standard wheel. Release artifacts are
built natively for Linux, macOS, and Windows because PyInstaller bundles a platform-specific
executable. Each release should document its runner architecture and publish checksums.
