from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from utils_paths import REPORTS_DIR, provisional_folder, official_candidate_folder

console = Console()

def folder_for_target(target: str) -> Path:
    if target == "provisional":
        return provisional_folder()
    if target == "official":
        return official_candidate_folder()
    raise ValueError(target)

def check(folder: Path) -> dict:
    sp = folder / "structures.json"
    mp = folder / "metadata.json"

    if not sp.exists() or not mp.exists():
        return {
            "folder": str(folder),
            "structures_exists": sp.exists(),
            "metadata_exists": mp.exists(),
            "passed": False,
            "error": "Missing structures.json or metadata.json",
        }

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official"], required=True)
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result = check(folder_for_target(args.target))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "result": result,
        "passed": result.get("passed", False),
    }

    suffix = "_" + args.target
    (REPORTS_DIR / f"v27_root_validator_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V27 root validator report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"PASSED: {report['passed']}",
        "",
    ]
    for k, v in result.items():
        lines.append(f"- {k}: {v}")

    text = "\n".join(lines)
    (REPORTS_DIR / f"v27_root_validator_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v27_root_validator_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V27 root validator ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("root_count", str(result.get("root_count")))
    table.add_row("paths start 997", str(result.get("all_paths_start_997")))
    table.add_row("metadata root", str(result.get("metadata_root_id")))
    console.print(table)

    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
