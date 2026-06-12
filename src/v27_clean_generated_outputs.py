from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table

from utils_paths import OUTPUT_DIR, REPORTS_DIR

console = Console()

SAFE_OUTPUT_SUBDIRS = [
    "brainglobe_provisional",
    "brainglobe_official_candidate",
    "brainglobe_local_cache",
]

def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    actions = []
    for name in SAFE_OUTPUT_SUBDIRS:
        path = OUTPUT_DIR / name
        if path.exists():
            shutil.rmtree(path)
            actions.append({"path": str(path), "removed": True})
        else:
            actions.append({"path": str(path), "removed": False})

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "safe_output_subdirs": SAFE_OUTPUT_SUBDIRS,
        "actions": actions,
        "important": "Only generated output folders were removed. data/raw was not touched.",
        "passed": True,
    }

    (REPORTS_DIR / "v27_output_cleanup_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "V27 safe output cleanup report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        "Only generated output folders were removed. data/raw was not touched.",
        "",
    ]
    for a in actions:
        lines.append(f"- {a['path']}: removed={a['removed']}")
    (REPORTS_DIR / "v27_output_cleanup_report.txt").write_text("\n".join(lines), encoding="utf-8")

    table = Table(title="V27 safe output cleanup")
    table.add_column("Folder")
    table.add_column("Removed")
    for a in actions:
        table.add_row(a["path"], str(a["removed"]))
    console.print(table)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
