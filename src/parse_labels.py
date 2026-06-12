from __future__ import annotations
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

    @property
    def is_placeholder(self) -> bool:
        cleaned = self.name.strip()
        return cleaned == "" or cleaned.strip("- ") == "" or cleaned == "-------"

LABEL_RE = re.compile(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9.]+)\s+(\d+)\s+(\d+)\s+"(.*)"\s*$')
CORTEX_RE = re.compile(r'^\s*(\d+)\s+([^\s]+)\s+"(.*)"\s*$')

def parse_itksnap_labels(path: Path) -> list[LabelEntry]:
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = LABEL_RE.match(line)
        if not match:
            continue
        idx, r, g, b, _alpha, _vis, _mesh, label = match.groups()
        entries.append(LabelEntry(int(idx), label.strip(), None, int(r), int(g), int(b), line))
    return entries

def parse_cortex_labels(path: Path) -> list[LabelEntry]:
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = CORTEX_RE.match(line)
        if not match:
            continue
        idx, acronym, label = match.groups()
        entries.append(LabelEntry(int(idx), label.strip(), acronym.strip(), None, None, None, line))
    return entries

def entries_by_id(entries: list[LabelEntry]) -> dict[int, LabelEntry]:
    return {entry.id: entry for entry in entries}
