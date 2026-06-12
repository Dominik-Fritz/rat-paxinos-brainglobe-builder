from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from utils_paths import REPORTS_DIR, provisional_folder, official_candidate_folder

console = Console()

def check(folder: Path) -> dict:
    sp = folder / "structures.json"
    mp = folder / "metadata.json"
    structures = json.loads(sp.read_text(encoding="utf-8"))
    metadata = json.loads(mp.read_text(encoding="utf-8"))

    roots = []
    for s in structures:
        try:
            sid = int(s.get("id"))
        except Exception:
            continue
        name = str(s.get("name", "")).strip().lower()
        acronym = str(s.get("acronym", "")).strip().lower()
        path = s.get("structure_id_path", [])
        if sid == 997 and (name == "root" or acronym == "root" or path == [997]):
            roots.append(s)

    all_paths_start_root = all(
        isinstance(s.get("structure_id_path"), list)
        and len(s.get("structure_id_path")) >= 1
        and int(s["structure_id_path"][0]) == 997
        for s in structures
    )

    metadata_root_ok = int(metadata.get("root_id", -1)) == 997

    return {
        "folder": str(folder),
        "structure_count": len(structures),
        "root_count": len(roots),
        "root_ids": [r.get("id") for r in roots],
        "root_preview": roots[:2],
        "all_paths_start_997": all_paths_start_root,
        "metadata_root_id": metadata.get("root_id"),
        "metadata_root_ok": metadata_root_ok,
        "passed": len(roots) == 1 and all_paths_start_root and metadata_root_ok,
    }

def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provisional": check(provisional_folder()),
    }
    official = official_candidate_folder()
    if (official / "structures.json").exists():
        results["official"] = check(official)

    passed = all(v.get("passed", True) for k, v in results.items() if isinstance(v, dict))
    results["passed"] = passed

    (REPORTS_DIR / "v26_root_validator_report.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["V26 root validator report", "=" * 72, f"Generated: {results['generated_at']}", f"PASSED: {passed}", ""]
    for key in ["provisional", "official"]:
        if key in results:
            lines.append(f"{key}:")
            for k, v in results[key].items():
                lines.append(f"- {k}: {v}")
            lines.append("")
    (REPORTS_DIR / "v26_root_validator_report.txt").write_text("\n".join(lines), encoding="utf-8")

    table = Table(title="V26 root validator")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(passed))
    table.add_row("provisional root_count", str(results["provisional"]["root_count"]))
    table.add_row("provisional paths start 997", str(results["provisional"]["all_paths_start_997"]))
    table.add_row("provisional metadata root", str(results["provisional"]["metadata_root_id"]))
    console.print(table)

    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
