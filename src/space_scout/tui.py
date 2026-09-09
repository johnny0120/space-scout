"""Keyboard-driven, immutable-snapshot disk browser."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from .config import Config, load_config
from .models import Entry, ScanOptions, ScanSnapshot
from .output import _escape, report_entry
from .policy import Policy, cleanup_rejection, default_policy
from .policy import safe_decide as _browse_decision
from .presentation import format_size, sort_key_for, visible_columns
from .scanner import scan
from .trash import trash_many


def scan_for_browse(
    root: Path, policy: Policy, unlocked: frozenset[Path] = frozenset(),
    *, select_patterns: tuple[str, ...] = (),
) -> ScanSnapshot:
    """Use the same policy-aware traversal as the public scan command."""
    return scan(ScanOptions(root, stay_on_filesystem=policy.stay_on_filesystem,
                            unlocked_paths=unlocked, select_patterns=select_patterns), policy)


def _format_utc_ns(value: int) -> str:
    seconds, nanoseconds = divmod(value, 10**9)
    dt = datetime.fromtimestamp(seconds, UTC)
    fractional = f".{nanoseconds:09d}" if nanoseconds else ""
    return dt.strftime(f"%Y-%m-%d %H:%M:%S{fractional} %Z")


def _modified_range(entry: Entry) -> str:
    if entry.modified_min_ns is None or entry.modified_max_ns is None:
        return "unavailable (metadata not scanned)"
    try:
        oldest, newest = (_format_utc_ns(value) for value in (entry.modified_min_ns, entry.modified_max_ns))
    except (OverflowError, OSError, ValueError):
        return "unavailable (timestamp outside display range)"
    return f"{oldest} — {newest}"


def _on_disk_cell(entry: Entry) -> str:
    return "—" if entry.allocated_bytes is None else format_size(entry.allocated_bytes)


def _detail_on_disk(entry: Entry) -> str:
    if entry.allocated_bytes is None:
        return "— (unknown)"
    return f"{entry.allocated_bytes} bytes ({format_size(entry.allocated_bytes)})"


class _Prompt(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    _Prompt { align: center middle; }
    #dialog { width: 80%; max-height: 90%; height: auto; padding: 1 2;
              border: round $accent; background: $surface; }
    #prompt { height: auto; margin-bottom: 1; }
    """

    def __init__(self, prompt: str, value: str = ""):
        super().__init__()
        self.prompt = prompt
        self.value = value

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dialog"):
            yield Static(self.prompt, id="prompt", markup=False)
            yield Input(value=self.value, id="answer")
            yield Static("Enter to submit · Escape to cancel", markup=False)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class BrowseApp(App[int]):
    NARROW_WIDTH = 90
    BINDINGS = [
        ("j", "next", "Down"), ("k", "previous", "Up"),
        ("enter", "toggle", "Expand"), ("s", "sort", "Sort"),
        ("S", "reverse_sort", "Reverse sort"),
        ("slash", "filter", "Filter"), ("i", "toggle_inspector", "Inspector"),
        ("r", "rescan", "Rescan"),
        ("t", "trash", "Trash"), ("u", "unlock", "Unlock"), ("q", "quit", "Quit"),
    ]
    CSS = """
    #panes { height: 1fr; }
    #entries { width: 2fr; height: 1fr; }
    #detail-pane { width: 1fr; padding: 1 2; border-left: solid $accent; }
    #details { height: auto; }
    #status { height: auto; max-height: 4; padding: 0 1; }
    """

    def __init__(
        self,
        snapshot: ScanSnapshot,
        policy: Policy,
        config: Config,
        select_patterns: tuple[str, ...] = (),
    ):
        super().__init__()
        self.snapshot = snapshot
        self.policy = policy or default_policy(snapshot.root)
        self.config = config or Config({}, (), {})
        self.select_patterns = select_patterns
        configured_sort = "on_disk" if self.config.sort_key == "size" else self.config.sort_key
        self._sort_column = configured_sort if configured_sort in {"on_disk", "name", "class", "status"} else "on_disk"
        self._sort_reverse = self._sort_column == "on_disk"
        self.filter_text = ""
        self.minimum_bytes = self.config.minimum_bytes
        self._filter_query = ""
        self.expanded: set[Path] = set()
        self.unlocked_paths: frozenset[Path] = frozenset()
        self._visible: list[tuple[Entry, int]] = []
        self._busy = False
        self._trashed: set[Path] = set()
        self._exit_code = 2 if snapshot.warnings else 0
        self._inspector_visible = True
        self._last_width: int | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            yield DataTable(id="entries", cursor_type="row")
            with VerticalScroll(id="detail-pane"):
                yield Static(id="details", markup=False)
        yield Static(id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self._inspector_visible = self.size.width >= self.NARROW_WIDTH
        self._last_width = self.size.width
        self._render_entries()
        self.query_one("#entries", DataTable).focus()
        state = "WARNING" if self.snapshot.warnings else "READY"
        self._status(f"{state} · {_escape(str(self.snapshot.root), escape_backslash=False)} · {len(self.snapshot.warnings)} scan warnings")

    def on_resize(self, event: events.Resize) -> None:
        if self.query("#entries"):
            if event.size.width < self.NARROW_WIDTH and (self._last_width or 0) >= self.NARROW_WIDTH:
                self._inspector_visible = False
            self._last_width = event.size.width
            self._render_entries(width=event.size.width)

    def _columns_for_width(self, width: int) -> tuple[str, ...]:
        """Return the shared presentation tier for a terminal width."""
        return visible_columns(width)

    def _selected(self) -> Entry | None:
        row = self.query_one("#entries", DataTable).cursor_row
        return self._visible[row][0] if 0 <= row < len(self._visible) else None

    def _classification(self, entry: Entry) -> str:
        return report_entry(entry, self.policy, self.config.overrides, self.unlocked_paths).classification

    def _entry_status(self, entry: Entry) -> str:
        if any(entry.path == path or path in entry.path.parents for path in self._trashed):
            return "TRASHED"
        decision, _ = _browse_decision(entry.path, self.policy, self.unlocked_paths)
        if decision.status == "unlocked":
            return "UNLOCKED"
        if entry.kind == "skipped" or decision.status in {"protected", "excluded"}:
            return "SKIPPED"
        if cleanup_rejection(entry.path, self.policy) is not None:
            return "BLOCKED"
        if entry.warning:
            return "WARNING"
        return "CLEANABLE"

    def _render_entries(self, width: int | None = None) -> None:
        table = self.query_one("#entries", DataTable)
        selected = self._selected()
        width = self.size.width if width is None else width
        columns = self._columns_for_width(width)
        compact = width < self.NARROW_WIDTH
        show_inspector = self._inspector_visible
        # In a narrow terminal the Inspector takes over the pane instead of
        # shrinking the table into an unusable sliver. The `i` binding remains
        # available because the table is restored when the Inspector is hidden.
        self.query_one("#entries").display = not (compact and show_inspector)
        self.query_one("#detail-pane").display = show_inspector
        self.query_one("#details").display = show_inspector
        self._visible = []

        def visit(entries: tuple[Entry, ...], depth: int = 0) -> None:
            key = sort_key_for(self._sort_column, self._classification, self._entry_status)
            if self._sort_column == "on_disk" and self._sort_reverse:
                def reverse_on_disk(entry: Entry) -> tuple[int, int, str, str]:
                    allocated = entry.allocated_bytes
                    return (
                        1 if allocated is None else 0,
                        -(allocated or 0),
                        entry.name.casefold(),
                        str(entry.path),
                    )

                ordered_entries = sorted(entries, key=reverse_on_disk)
            else:
                ordered_entries = sorted(entries, key=key, reverse=self._sort_reverse)
            for entry in ordered_entries:
                status = self._entry_status(entry)
                matches = self.filter_text.casefold() in (
                    f"{entry.name} {self._classification(entry)} {status}"
                ).casefold()
                if matches and (entry.logical_bytes >= self.minimum_bytes or status == "SKIPPED"):
                    self._visible.append((entry, depth))
                if status != "SKIPPED" and (entry.path in self.expanded or self.filter_text):
                    visit(entry.children, depth + 1)

        visit(self.snapshot.entries)
        table.clear(columns=True)
        labels = {
            "name": "Name",
            "on_disk": "On disk",
            "logical": "Logical",
            "class": "Class",
            "status": "Status",
        }
        byte_widths = {
            "on_disk": max((
                len(labels["on_disk"]),
                *(len(_on_disk_cell(entry)) for entry, _ in self._visible),
            )),
            "logical": max((
                len(labels["logical"]), *(len(format_size(entry.logical_bytes)) for entry, _ in self._visible)
            )),
        }
        class_width = max((
            len(labels["class"]),
            *(len(_escape(self._classification(entry))) for entry, _ in self._visible),
        ))
        status_width = max((
            len(labels["status"]),
            *(len(self._entry_status(entry)) for entry, _ in self._visible),
        ))
        name_width = max((
            len(labels["name"]),
            *(len(f"{'  ' * depth}{'▾' if entry.kind == 'directory' else '·'} {_escape(entry.name)}")
              for entry, depth in self._visible),
        ))
        # Resize events are delivered before Textual lays out the split, so
        # table.size.width may still describe the previous terminal width.
        # Derive the pane budget from the new terminal width during every
        # render; this keeps the transition in sync with the eventual layout.
        pane_width = width if compact else max(40, (width * 2) // 3)
        # DataTable adds cell padding and a scrollbar edge to its virtual
        # width. Reserve those cells so long labels cannot create horizontal
        # scrolling, including during the first render before layout settles.
        available = max(1, pane_width - 6)
        other_widths = sum({
            "on_disk": byte_widths["on_disk"],
            "logical": byte_widths["logical"],
            "class": min(class_width, 18),
            "status": min(status_width, 10),
        }[column] for column in columns if column != "name")
        gaps = max(0, len(columns) - 1)
        if "name" in columns:
            name_width = min(name_width, max(len(labels["name"]), available - other_widths - gaps, 1), 32)
        widths = {
            "name": name_width,
            "on_disk": byte_widths["on_disk"],
            "logical": byte_widths["logical"],
            "class": min(class_width, 18),
            "status": min(status_width, 10),
        }
        for column in columns:
            table.add_column(labels[column], width=widths[column])
        for entry, depth in self._visible:
            icon = ("▾" if entry.path in self.expanded else "▸") if entry.kind == "directory" else "·"
            name = Text(f"{'  ' * depth}{icon} {_escape(entry.name)}", no_wrap=True, overflow="ellipsis")
            rendered = {
                "name": name,
                "on_disk": format_size(entry.allocated_bytes, byte_widths["on_disk"]),
                "logical": format_size(entry.logical_bytes, byte_widths["logical"]),
                "class": Text(_escape(self._classification(entry)), no_wrap=True, overflow="ellipsis"),
                "status": Text(self._entry_status(entry), no_wrap=True, overflow="ellipsis"),
            }
            cells = [rendered[column] for column in columns]
            table.add_row(*cells, key=str(entry.path))
        if selected:
            index = next((i for i, (entry, _) in enumerate(self._visible) if entry.path == selected.path), 0)
            table.move_cursor(row=index)
        self._show_details()

    def _show_details(self) -> None:
        entry = self._selected()
        if entry is None:
            text = "No matching entries. / to change filter; r to rescan."
        else:
            decision, _ = _browse_decision(entry.path, self.policy, self.unlocked_paths)
            cleanup = cleanup_rejection(entry.path, self.policy)
            text = (f"Path: {_escape(str(entry.path), escape_backslash=False)}\n\n"
                    f"Status: {self._entry_status(entry)}\n"
                    f"Class: {_escape(self._classification(entry))}\nChildren: {len(entry.children)}\n"
                    f"On disk: {_detail_on_disk(entry)}\n"
                    f"Logical: {entry.logical_bytes} bytes ({format_size(entry.logical_bytes)})\n"
                    f"Size basis: logical bytes; directories total scanned children.\n"
                    f"Modified range (UTC; entry + scanned descendants): {_modified_range(entry)}\n\n"
                    f"Policy: {_escape(decision.reason)}")
            if cleanup:
                text += f"\nCleanup: {_escape(cleanup)}"
            if entry.warning:
                text += f"\nWarning: {_escape(entry.warning)}"
        self.query_one("#details", Static).update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_details()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_toggle()

    def _status(self, message: str) -> None:
        states = {
            "SCANNING": "bold yellow", "READY": "bold green", "WARNING": "bold yellow",
            "SKIPPED": "bold yellow", "BLOCKED": "bold red", "CLEANABLE": "bold green", "TRASHED": "bold green",
        }
        state = next((name for name in states if message.upper().startswith(name)), None)
        self.query_one("#status", Static).update(Text(message, style=states[state] if state else ""))

    def action_next(self) -> None:
        self.query_one("#entries", DataTable).action_cursor_down()

    def action_previous(self) -> None:
        self.query_one("#entries", DataTable).action_cursor_up()

    def action_toggle(self) -> None:  # type: ignore[override]
        entry = self._selected()
        if entry and entry.kind == "directory" and self._entry_status(entry) not in {"SKIPPED", "TRASHED"}:
            if entry.path in self.expanded:
                self.expanded.remove(entry.path)
            else:
                self.expanded.add(entry.path)
            self._render_entries()

    def action_sort(self) -> None:
        sort_columns = ("on_disk", "name", "class", "status")
        index = sort_columns.index(self._sort_column)
        self._sort_column = sort_columns[(index + 1) % len(sort_columns)]
        self._sort_reverse = False
        self._render_entries()
        self._status(f"Sorted by {self._sort_column}")

    def action_reverse_sort(self) -> None:
        self._sort_reverse = not self._sort_reverse
        self._render_entries()
        self._status(f"Sorted by {self._sort_column} {'descending' if self._sort_reverse else 'ascending'}")

    def action_filter(self) -> None:
        def apply(answer: str | None) -> None:
            if answer is not None:
                minimum = self.config.minimum_bytes
                words = []
                for word in answer.split():
                    if word.startswith("min:"):
                        try:
                            minimum = int(word[4:])
                            if minimum < 0:
                                raise ValueError
                        except ValueError:
                            self._status("Invalid minimum: use min: followed by non-negative bytes.")
                            return
                    else:
                        words.append(word)
                self.minimum_bytes = minimum
                self.filter_text = " ".join(words)
                self._filter_query = answer
                self._render_entries()
        self.push_screen(_Prompt("Filter by name, class, or status; min:1024 sets minimum bytes.\n"
                                 "Empty restores configured defaults:", self._filter_query), apply)

    def action_toggle_inspector(self) -> None:
        """Show or hide the wide-terminal Inspector without changing the snapshot."""
        self._inspector_visible = not self._inspector_visible
        self._render_entries()
        self._status(f"Inspector {'shown' if self._inspector_visible else 'hidden'}")

    def action_rescan(self) -> None:
        if not self._busy:
            self._rescan()

    @work
    async def _rescan(self) -> None:
        self._busy = True
        self._status("SCANNING · Scanning…")
        try:
            if self.select_patterns:
                snapshot = await asyncio.to_thread(
                    scan_for_browse,
                    self.snapshot.root,
                    self.policy,
                    self.unlocked_paths,
                    select_patterns=self.select_patterns,
                )
            else:
                snapshot = await asyncio.to_thread(
                    scan_for_browse, self.snapshot.root, self.policy, self.unlocked_paths
                )
            self.snapshot = snapshot
            self._trashed.clear()
            self._render_entries()
            if snapshot.warnings:
                self._exit_code = max(self._exit_code, 2)
                self._status(f"WARNING · Scan complete · {len(snapshot.warnings)} warnings · r to rescan")
            else:
                self._exit_code = 0
                self._status("READY · Scan complete · 0 warnings")
        except (OSError, RuntimeError, ValueError) as exc:
            self._exit_code = 3
            self._status(f"WARNING · Scan failed: {_escape(str(exc))} · r to retry")
        finally:
            self._busy = False

    def action_unlock(self) -> None:
        entry = self._selected()
        if self._busy or entry is None:
            return
        decision, _ = _browse_decision(entry.path, self.policy, self.unlocked_paths)
        if decision.status != "protected":
            if decision.status == "excluded":
                self._status("SKIPPED · Unlock unavailable: excluded paths remain blocked.")
            else:
                self._status("WARNING · Unlock unavailable: select a protected path.")
            return
        def confirm(answer: str | None) -> None:
            if answer == "unlock":
                self.unlocked_paths = self.unlocked_paths | {entry.path}
                self.action_rescan()
            else:
                self._status("Unlock cancelled.")
        self.push_screen(_Prompt(f"Unlock scan for exact path:\n{_escape(str(entry.path), escape_backslash=False)}\n\n"
                                 "This allows reading protected contents for this session. "
                                 "Cleanup remains blocked. Type unlock to confirm:"), confirm)

    def action_trash(self) -> None:
        entry = self._selected()
        if self._busy or entry is None:
            return
        cleanup_reason = cleanup_rejection(entry.path, self.policy)
        status = self._entry_status(entry)
        if status in {"SKIPPED", "BLOCKED", "TRASHED"} or cleanup_reason is not None:
            message = cleanup_reason or "protected, excluded, skipped, or already trashed path"
            self._status(f"SKIPPED · Trash rejected: {message}")
            return
        def confirm(answer: str | None) -> None:
            if answer == "trash":
                self._trash(entry)
            else:
                self._status("Trash cancelled.")
        self.push_screen(_Prompt(f"Move exact path to system trash:\n{_escape(str(entry.path), escape_backslash=False)}\n\n"
                                 f"Class: {_escape(self._classification(entry))}\n"
                                 f"On disk: {_detail_on_disk(entry)}\n"
                                 f"Estimated size: {entry.logical_bytes} logical bytes; "
                                 "actual reclaimed space may differ.\nType trash to confirm:"), confirm)

    def _trash_blocked(self, entry: Entry) -> bool:
        return (self._entry_status(entry) in {"SKIPPED", "BLOCKED", "TRASHED"}
                or cleanup_rejection(entry.path, self.policy) is not None)

    @work
    async def _trash(self, entry: Entry) -> None:
        self._busy = True
        self._status("CLEANABLE · Moving to system trash…")
        try:
            try:
                current_config = load_config()
            except (OSError, RuntimeError, ValueError) as exc:
                self._exit_code = 3
                self._status(f"WARNING · Trash rejected: cannot reload configuration: {_escape(str(exc))}")
                return
            # Preserve the session's protected roots and exclusions while
            # applying newly persisted exclusions at the final boundary.
            self.policy = replace(self.policy, exclusions=tuple(dict.fromkeys(
                (*self.policy.exclusions, *current_config.exclusions))))
            if self._trash_blocked(entry):
                self._status("SKIPPED · Trash rejected: path is now protected or excluded.")
                return
            results = await asyncio.to_thread(trash_many, (entry.path,))
            for result in results:
                if result.success:
                    self._trashed.add(result.path)
                else:
                    self._exit_code = 3
            self._render_entries()
            state = "TRASHED" if all(result.success for result in results) else "WARNING"
            self._status(state + " · " + "; ".join(
                f"{_escape(str(result.path), escape_backslash=False)}: {_escape(result.message)}" for result in results
            ) + " · r to rescan")
        finally:
            self._busy = False

    def action_quit(self) -> None:  # type: ignore[override]
        self.exit(self._exit_code or (2 if self.snapshot.warnings else 0))


def run_browse(
    snapshot: ScanSnapshot,
    policy: Policy,
    config: Config,
    select_patterns: tuple[str, ...] = (),
) -> int:
    return BrowseApp(snapshot, policy, config, select_patterns=select_patterns).run() or 0
