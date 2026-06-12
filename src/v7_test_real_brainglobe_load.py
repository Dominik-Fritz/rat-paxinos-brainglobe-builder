from __future__ import annotations
import argparse
import json
import os
import traceback
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, ATLAS_NAME, project_local_cache_folder

console = Console()

def try_real_brainglobe_load():
    result = {
        "attempted": True,
        "success": False,
        "atlas_name": ATLAS_NAME,
        "exception": None,
        "traceback": None,
        "object_summary": {},
    }
    try:
        from brainglobe_atlasapi import BrainGlobeAtlas
        atlas = BrainGlobeAtlas(ATLAS_NAME)
        result["success"] = True
        result["object_summary"] = {
            "class": atlas.__class__.__name__,
            "repr": repr(atlas),
            "has_reference": hasattr(atlas, "reference"),
            "has_annotation": hasattr(atlas, "annotation"),
            "has_structures": hasattr(atlas, "structures"),
        }
        for attr in ["resolution", "orientation", "root_dir", "atlas_dir", "name"]:
            try:
                result["object_summary"][attr] = str(getattr(atlas, attr))
            except Exception:
                pass
    except Exception as exc:
        result["exception"] = repr(exc)
        result["traceback"] = traceback.format_exc()
    return result

def try_project_local_sanity():
    folder = project_local_cache_folder()
    return {
        "folder": str(folder),
        "exists": folder.exists(),
        "required_files": {name: (folder / name).exists() for name in ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json"]},
    }

def write_final_status(real_load, local_sanity):
    passed = bool(real_load.get("success"))
    lines = [
        "V7 FINAL STATUS",
        "=" * 72,
        f"PASSED: {passed}",
        f"Real BrainGlobeAtlas load attempted: {real_load.get('attempted')}",
        f"Real BrainGlobeAtlas load success: {real_load.get('success')}",
        "",
        "Project-local cache sanity:",
        f"- folder: {local_sanity.get('folder')}",
        f"- exists: {local_sanity.get('exists')}",
    ]
    for k, v in local_sanity.get("required_files", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    if passed:
        lines.append("Result: BrainGlobeAtlas accepted the atlas name.")
        lines.append("Next step: ABBA visibility/loading test.")
    else:
        lines.append("Result: BrainGlobeAtlas did not yet accept the atlas name.")
        lines.append("This is expected unless the atlas is registered in the exact BrainGlobe global cache/index format.")
        lines.append("Next step: inspect exception and implement exact atlasgen/cache registration.")
        lines.append("")
        lines.append(f"Exception: {real_load.get('exception')}")
    (REPORTS_DIR / "v7_final_status.txt").write_text("\\n".join(lines), encoding="utf-8")
    return passed

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-load", action="store_true", help="Attempt BrainGlobeAtlas(atlas_name).")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    local_sanity = try_project_local_sanity()

    if args.real_load:
        real_load = try_real_brainglobe_load()
    else:
        real_load = {
            "attempted": False,
            "success": False,
            "atlas_name": ATLAS_NAME,
            "exception": "Real load test skipped. Use --real-load.",
            "traceback": None,
            "object_summary": {},
        }

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "real_brainglobe_load": real_load,
        "project_local_cache_sanity": local_sanity,
        "passed": bool(real_load.get("success")),
    }
    (REPORTS_DIR / "v7_real_load_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V7 real BrainGlobeAtlas load report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Atlas: {ATLAS_NAME}",
        "",
        "Real load:",
        f"- attempted: {real_load.get('attempted')}",
        f"- success: {real_load.get('success')}",
        f"- exception: {real_load.get('exception')}",
        "",
        "Object summary:",
    ]
    for k, v in real_load.get("object_summary", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Project-local cache sanity:")
    lines.append(f"- folder: {local_sanity.get('folder')}")
    lines.append(f"- exists: {local_sanity.get('exists')}")
    for k, v in local_sanity.get("required_files", {}).items():
        lines.append(f"- {k}: {v}")
    if real_load.get("traceback"):
        lines.append("")
        lines.append("Traceback:")
        lines.append(real_load.get("traceback"))
    (REPORTS_DIR / "v7_real_load_report.txt").write_text("\\n".join(lines), encoding="utf-8")

    passed = write_final_status(real_load, local_sanity)

    table = Table(title="V7 real BrainGlobeAtlas load")
    table.add_column("Check"); table.add_column("Value")
    table.add_row("Attempted", str(real_load.get("attempted")))
    table.add_row("Success", str(real_load.get("success")))
    table.add_row("Project cache exists", str(local_sanity.get("exists")))
    table.add_row("Exception", str(real_load.get("exception"))[:80])
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v7_real_load_report.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v7_final_status.txt'}")

    # If skipped, do not hard fail. If attempted and failed, fail so the user notices.
    if not args.real_load:
        return 0
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
