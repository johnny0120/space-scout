"""Deterministic reports for immutable scan snapshots."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

from .classify import classify
from .models import Entry, ScanSnapshot, ScanWarning
from .policy import Policy, cleanup_rejection, decide


@dataclass(frozen=True, slots=True)
class ReportRow:
    path: str
    name: str
    kind: str
    logical_bytes: int
    allocated_bytes: int | None
    classification: str
    status: str
    reason: str | None


def report_entry(entry: Entry, policy: Policy, overrides: dict[str, str] | None = None,
                 unlocked: frozenset[Path] = frozenset()) -> ReportRow:
    """Annotate one row; filesystem and classification errors stay local."""
    try:
        decision = decide(entry.path, policy, unlocked)
        rejection = cleanup_rejection(entry.path, policy)
        label = (overrides or {}).get(str(entry.path))
        if label is None:
            label = classify(entry.path, entry.kind, entry.logical_bytes)
        reason = rejection or decision.reason
        if entry.warning:
            reason = f"{reason}; {entry.warning}"
        status: str = decision.status
        return ReportRow(str(entry.path), entry.name, entry.kind, entry.logical_bytes,
                         entry.allocated_bytes, label, status, reason)
    except (OSError, RuntimeError, ValueError) as exc:
        reason = entry.warning or f"Cannot evaluate path policy or classification: {exc}"
        return ReportRow(str(entry.path), entry.name, "skipped", 0, None, "skipped", "skipped", reason)


def flatten(snapshot: ScanSnapshot, policy: Policy, overrides: dict[str, str] | None = None) -> tuple[ReportRow, ...]:
    rows: list[ReportRow] = []
    pending = list(snapshot.entries)
    while pending:
        entry = pending.pop()
        rows.append(report_entry(entry, policy, overrides))
        pending.extend(entry.children)
    return tuple(sorted(rows, key=lambda row: (
        -row.logical_bytes, os.path.normcase(os.path.normpath(row.path)), row.path,
    )))


def _escape(value: str) -> str:
    # Escape terminal controls (including Unicode format controls), retaining
    # printable non-ASCII names. Literal backslashes remain distinguishable.
    return "".join(
        "\\\\" if char == "\\" else char if char.isprintable()
        else char.encode("unicode_escape").decode("ascii")
        for char in value
    )


def report_warnings(snapshot: ScanSnapshot, rows: Sequence[ReportRow]) -> tuple[ScanWarning, ...]:
    warnings = list(snapshot.warnings)
    for row in rows:
        if row.status == "skipped" and not any(w.path == Path(row.path) and w.message == row.reason for w in warnings):
            warnings.append(ScanWarning(Path(row.path), "entry_error", row.reason or "entry skipped"))
    return tuple(warnings)


def render_table(rows: Sequence[ReportRow], stream: TextIO, snapshot: ScanSnapshot | None = None) -> None:
    stream.write("SIZE  ON_DISK  TYPE  CLASS  STATUS  PATH\n")
    for row in rows:
        stream.write("  ".join(_escape(value) for value in (
            str(row.logical_bytes), "—" if row.allocated_bytes is None else str(row.allocated_bytes),
            row.kind, row.classification,
            "SKIPPED" if row.kind == "skipped" or row.status in {"protected", "excluded", "skipped"}
            else "BLOCKED" if row.status == "blocked" else row.status,
            row.path,
        )) + "\n")
        if row.reason:
            stream.write(f"  {_escape(row.reason)}\n")
    if snapshot:
        stream.writelines(f"WARNING {_escape(warning.code)}: {_escape(str(warning.path))}: {_escape(warning.message)}\n" for warning in report_warnings(snapshot, rows))


def render_json(snapshot: ScanSnapshot, rows: Sequence[ReportRow], stream: TextIO) -> None:
    payload = {
        "root": str(snapshot.root),
        "entries": [asdict(row) for row in rows],
        "warnings": [
            {"path": str(warning.path), "code": warning.code, "message": warning.message}
            for warning in report_warnings(snapshot, rows)
        ],
    }
    json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
    stream.write("\n")
