from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LabelEntry:
    id: int
    name: str
    acronym: str | None = None
    r: int | None = None
    g: int | None = None
    b: int | None = None
    raw_line: str | None = None


LABEL_RE = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+(\d+)\s+(\d+)\s+"(.*)"\s*$')
CORTEX_RE = re.compile(r'^\s*(\d+)\s+([^\s]+)\s+"(.*)"\s*$')


def parse_itksnap_labels(path: Path) -> list[LabelEntry]:
    """Parse ITK-SNAP label description files.

    Expected format:
    IDX R G B A VIS MSH "LABEL"
    """
    entries: list[LabelEntry] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = LABEL_RE.match(line)
        if not match:
            continue

        idx, r, g, b, _alpha, _vis, _mesh, label = match.groups()
        entries.append(
            LabelEntry(
                id=int(idx),
                name=label.strip(),
                r=int(r),
                g=int(g),
                b=int(b),
                raw_line=line,
            )
        )
    return entries


def parse_cortex_labels(path: Path) -> list[LabelEntry]:
    """Parse the compact cortex label table: ID ACRONYM "name"."""
    entries: list[LabelEntry] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = CORTEX_RE.match(line)
        if not match:
            continue

        idx, acronym, label = match.groups()
        entries.append(
            LabelEntry(
                id=int(idx),
                acronym=acronym.strip(),
                name=label.strip(),
                raw_line=line,
            )
        )
    return entries


def summarize_labels(entries: list[LabelEntry]) -> dict[str, object]:
    ids = [entry.id for entry in entries]
    names = [entry.name for entry in entries]

    duplicate_ids = sorted({idx for idx in ids if ids.count(idx) > 1})
    empty_or_placeholder = [
        entry for entry in entries
        if not entry.name or entry.name.strip("- ") == ""
    ]

    return {
        "count": len(entries),
        "min_id": min(ids) if ids else None,
        "max_id": max(ids) if ids else None,
        "duplicate_ids": duplicate_ids,
        "placeholder_count": len(empty_or_placeholder),
        "sample_names": names[:20],
    }


def entries_by_id(entries: list[LabelEntry]) -> dict[int, LabelEntry]:
    return {entry.id: entry for entry in entries}
