from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()

def folder_for(target: str) -> Path:
    if target == "provisional":
        return provisional_folder()
    if target == "official":
        return official_candidate_folder()
    if target == "installed":
        return Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"
    raise ValueError(target)

def patch(target: str) -> dict:
    folder = folder_for(target)
    metadata_path = folder / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    before = metadata.get("additional_references")
    metadata["additional_references"] = []
    metadata["source_references"] = metadata.get("source_references", [
        "Paxinos G, Watson C. The Rat Brain in Stereotaxic Coordinates, 6th edition. Academic Press, 2007.",
        "BlueBrainHeadModels v1 / Paxinos-Watson atlas digitization, DOI: 10.5281/zenodo.10926947.",
    ])
    metadata["additional_references_note"] = (
        "Set to empty list by V32 because BrainGlobe/ABBA interprets additional_references "
        "as additional TIFF channel names, not literature citations."
    )
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "target": target,
        "folder": str(folder),
        "metadata_path": str(metadata_path),
        "before_additional_references": before,
        "after_additional_references": metadata["additional_references"],
        "passed": metadata.get("additional_references") == [],
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official", "installed"], required=True)
    args = parser.parse_args()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result = patch(args.target)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "result": result, "passed": result["passed"]}
    suffix = "_" + args.target
    (REPORTS_DIR / f"v32_no_additional_refs_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    txt = "\n".join(["V32 no additional_references report", "="*72, f"Generated: {report['generated_at']}", f"Target: {args.target}", f"PASSED: {report['passed']}", ""] + [f"- {k}: {v}" for k,v in result.items()])
    (REPORTS_DIR / f"v32_no_additional_refs_report{suffix}.txt").write_text(txt, encoding="utf-8")
    (REPORTS_DIR / "v32_no_additional_refs_report.txt").write_text(txt, encoding="utf-8")
    table = Table(title=f"V32 additional_references fix ({args.target})")
    table.add_column("Check"); table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("after", str(result["after_additional_references"]))
    console.print(table)
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
