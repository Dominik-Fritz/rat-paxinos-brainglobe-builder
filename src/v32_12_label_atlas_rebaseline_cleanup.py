#!/usr/bin/env python3
"""
V32.12 LabelAtlas Rebaseline Cleanup

Non-destructive cleanup for the rat Paxinos BrainGlobe/ABBA project.
Moves experimental Paxinos test atlases out of the active BrainGlobe cache,
patches last_versions.conf, and restores the main paxinos_watson_rat_40um
cache from the project LabelAtlas candidate.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple

STABLE_ATLAS = "paxinos_watson_rat_40um"
STABLE_CACHE_FOLDER = f"{STABLE_ATLAS}_v1.0"
REPORT_NAME = "v32_12_label_atlas_rebaseline_cleanup"

# Test/diagnostic names intentionally broad but constrained to the Paxinos prefix.
# Anything that starts with paxinos_watson_rat_40um_ is treated as a non-main test atlas.
TEST_PREFIX = STABLE_ATLAS + "_"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_json(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [safe_json(v) for v in obj]
    return obj


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {"__read_error__": str(exc)}


def is_main_cache_folder_name(name: str) -> bool:
    return name == STABLE_CACHE_FOLDER


def is_paxinos_test_cache_folder_name(name: str) -> bool:
    """Return True for top-level BrainGlobe cache folders that are Paxinos test atlases."""
    if is_main_cache_folder_name(name):
        return False
    if not name.startswith(TEST_PREFIX):
        return False
    # Require BrainGlobe-style version suffix or old backup naming under top level.
    # This catches *_test_v1.0, *_debug_v1.0, *_backup_..., etc., but not the main atlas.
    return True


def is_paxinos_test_project_folder_name(name: str) -> bool:
    if name == STABLE_ATLAS:
        return False
    return name.startswith(TEST_PREFIX)


def ensure_dir(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    path.mkdir(parents=True, exist_ok=True)


def move_to_quarantine(src: Path, quarantine_root: Path, dry_run: bool) -> Dict[str, Any]:
    dst = quarantine_root / src.name
    record = {"src": str(src), "dst": str(dst), "status": "planned" if dry_run else "moved"}
    if dry_run:
        return record
    quarantine_root.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst = quarantine_root / f"{src.name}__dup_{now_stamp()}"
        record["dst"] = str(dst)
    shutil.move(str(src), str(dst))
    return record


def copytree_fresh(src: Path, dst: Path, dry_run: bool) -> Dict[str, Any]:
    rec = {"src": str(src), "dst": str(dst), "status": "planned" if dry_run else "copied"}
    if dry_run:
        return rec
    if not src.exists():
        rec["status"] = "source_missing"
        return rec
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return rec


def patch_last_versions(cache_root: Path, dry_run: bool, stamp: str) -> Dict[str, Any]:
    conf = cache_root / "last_versions.conf"
    result: Dict[str, Any] = {
        "path": str(conf),
        "exists_before": conf.exists(),
        "backup": None,
        "removed_lines": [],
        "kept_lines": [],
        "stable_entry_present_before": False,
        "stable_entry_added": False,
        "status": "planned" if dry_run else "patched",
    }
    if not conf.exists():
        result["status"] = "missing_created" if not dry_run else "missing_would_create"
        if not dry_run:
            cache_root.mkdir(parents=True, exist_ok=True)
            conf.write_text(f"{STABLE_ATLAS}=1.0\n", encoding="utf-8")
        result["stable_entry_added"] = True
        return result

    lines = conf.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    new_lines: List[str] = []
    stable_present = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            result["kept_lines"].append(line.rstrip("\n"))
            continue
        key = stripped.split("=", 1)[0].strip()
        if key == STABLE_ATLAS:
            stable_present = True
            new_lines.append(line)
            result["kept_lines"].append(line.rstrip("\n"))
            continue
        if key.startswith(TEST_PREFIX):
            result["removed_lines"].append(line.rstrip("\n"))
            continue
        # Defensive: remove any line that directly mentions the explicit test prefix as an atlas name.
        if TEST_PREFIX in stripped and STABLE_CACHE_FOLDER not in stripped:
            result["removed_lines"].append(line.rstrip("\n"))
            continue
        new_lines.append(line)
        result["kept_lines"].append(line.rstrip("\n"))

    result["stable_entry_present_before"] = stable_present
    if not stable_present:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"{STABLE_ATLAS}=1.0\n")
        result["stable_entry_added"] = True

    if not dry_run:
        backup = cache_root / f"last_versions.conf.backup_v32_12_{stamp}"
        shutil.copy2(conf, backup)
        conf.write_text("".join(new_lines), encoding="utf-8")
        result["backup"] = str(backup)
    else:
        result["backup"] = str(cache_root / f"last_versions.conf.backup_v32_12_{stamp}")
    return result


def find_stable_source(project_root: Path) -> Tuple[Path | None, List[str]]:
    candidates = [
        project_root / "data" / "output" / "brainglobe_official_candidate" / STABLE_ATLAS,
        project_root / "data" / "output" / "brainglobe_provisional" / STABLE_ATLAS,
    ]
    notes: List[str] = []
    required_any = ["annotation.tiff", "annotation.nii.gz", "structures.json", "metadata.json"]
    for cand in candidates:
        if not cand.exists():
            notes.append(f"missing: {cand}")
            continue
        present = [name for name in required_any if (cand / name).exists()]
        if len(present) >= 3:
            notes.append(f"selected: {cand}")
            return cand, notes
        notes.append(f"exists_but_incomplete: {cand} present={present}")
    return None, notes


def scan_cache(cache_root: Path) -> Dict[str, Any]:
    folders = []
    if cache_root.exists():
        for p in sorted(cache_root.iterdir(), key=lambda x: x.name.lower()):
            if p.is_dir():
                folders.append(p.name)
    return {
        "cache_root": str(cache_root),
        "exists": cache_root.exists(),
        "all_top_level_folders": folders,
        "paxinos_main_present": STABLE_CACHE_FOLDER in folders,
        "paxinos_test_folders": [name for name in folders if is_paxinos_test_cache_folder_name(name)],
    }


def collect_project_test_dirs(project_root: Path) -> List[Path]:
    bases = [
        project_root / "data" / "output" / "brainglobe_official_candidate",
        project_root / "data" / "output" / "brainglobe_provisional",
    ]
    found: List[Path] = []
    for base in bases:
        if not base.exists():
            continue
        for p in sorted(base.iterdir(), key=lambda x: str(x).lower()):
            if p.is_dir() and is_paxinos_test_project_folder_name(p.name):
                found.append(p)
    return found


def validate_main_cache(cache_dir: Path) -> Dict[str, Any]:
    res: Dict[str, Any] = {
        "path": str(cache_dir),
        "exists": cache_dir.exists(),
        "required_files": {},
        "metadata": None,
        "warnings": [],
    }
    required = ["metadata.json", "structures.json", "annotation.tiff", "reference.tiff", "hemispheres.tiff"]
    for name in required:
        res["required_files"][name] = (cache_dir / name).exists()
    metadata_path = cache_dir / "metadata.json"
    if metadata_path.exists():
        meta = read_json(metadata_path)
        res["metadata"] = {
            "name": meta.get("name") or meta.get("atlas_name"),
            "orientation": meta.get("orientation"),
            "shape": meta.get("shape"),
            "additional_references": meta.get("additional_references"),
            "reference_strategy": meta.get("reference_strategy"),
            "title": meta.get("title"),
        }
        # Warn if obvious MRI/reference experiment wording is present.
        meta_text = json.dumps(meta, default=str).lower()
        for bad in ["waxholm", "sigma", "neurorat", "mri", "affine"]:
            if bad in meta_text:
                res["warnings"].append(f"metadata_mentions_{bad}")
    return res


def write_reports(report_dir: Path, report: Dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        # Still write reports for dry-run; it is useful and harmless.
        pass
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{REPORT_NAME}_report.json"
    txt_path = report_dir / f"{REPORT_NAME}_summary.txt"
    json_path.write_text(json.dumps(safe_json(report), indent=2, ensure_ascii=False), encoding="utf-8")

    lines: List[str] = []
    lines.append("V32.12 LabelAtlas Rebaseline Cleanup")
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"PASSED: {report['passed']}")
    lines.append(f"Mode: {'DRY RUN' if report['dry_run'] else 'APPLY'}")
    lines.append(f"Project root: {report['project_root']}")
    lines.append(f"BrainGlobe cache: {report['cache_root']}")
    lines.append("")
    lines.append("Goal:")
    lines.append("- Keep only paxinos_watson_rat_40um as the active Paxinos atlas.")
    lines.append("- Restore LabelAtlas/label-only main atlas cache from project output.")
    lines.append("- Move experimental reference/MRI/test atlases into quarantine folders.")
    lines.append("")
    lines.append("Moved/quarantined BrainGlobe cache test atlases:")
    if report["cache_test_moves"]:
        for rec in report["cache_test_moves"]:
            lines.append(f"- {rec['src']} -> {rec['dst']} [{rec['status']}]")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Moved/quarantined project output test atlases:")
    if report["project_test_moves"]:
        for rec in report["project_test_moves"]:
            lines.append(f"- {rec['src']} -> {rec['dst']} [{rec['status']}]")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Main atlas cache restore:")
    for rec in report.get("main_cache_restore", []):
        lines.append(f"- {rec}")
    lines.append("")
    lines.append("last_versions.conf patch:")
    lv = report.get("last_versions_patch", {})
    lines.append(f"- path: {lv.get('path')}")
    lines.append(f"- backup: {lv.get('backup')}")
    lines.append(f"- removed lines: {len(lv.get('removed_lines', []))}")
    for line in lv.get("removed_lines", []):
        lines.append(f"  - {line}")
    lines.append(f"- stable entry added: {lv.get('stable_entry_added')}")
    lines.append("")
    lines.append("Final cache scan:")
    final_scan = report.get("final_cache_scan", {})
    lines.append(f"- main present: {final_scan.get('paxinos_main_present')}")
    lines.append(f"- remaining paxinos test folders: {final_scan.get('paxinos_test_folders')}")
    lines.append("")
    lines.append("Main cache validation:")
    val = report.get("main_cache_validation", {})
    lines.append(f"- exists: {val.get('exists')}")
    lines.append(f"- metadata: {val.get('metadata')}")
    lines.append(f"- warnings: {val.get('warnings')}")
    lines.append("")
    lines.append("Important:")
    lines.append("- Raw source data were not deleted.")
    lines.append("- Experimental V32.4-V32.11 reference-channel work is not promoted.")
    lines.append("- Restart Fiji/ABBA completely before checking the atlas list.")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="V32.12 LabelAtlas Rebaseline Cleanup")
    parser.add_argument("--project-root", required=True, help="Project root, e.g. G:\\rat-paxinos-brainglobe-builder")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true", help="Apply cleanup")
    mode.add_argument("--dry-run", action="store_true", help="Report only")
    parser.add_argument("--restore-main-cache", action="store_true", help="Restore main BrainGlobe cache from project LabelAtlas candidate")
    parser.add_argument("--cache-root", default=None, help="Override BrainGlobe cache root. Default: %%USERPROFILE%%\\.brainglobe")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    dry_run = args.dry_run
    stamp = now_stamp()
    cache_root = Path(args.cache_root).resolve() if args.cache_root else Path.home() / ".brainglobe"
    report_dir = project_root / "reports" / REPORT_NAME

    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": False,
        "dry_run": dry_run,
        "project_root": str(project_root),
        "cache_root": str(cache_root),
        "stable_atlas": STABLE_ATLAS,
        "stable_cache_folder": STABLE_CACHE_FOLDER,
        "does_delete_raw_data": False,
        "does_promote_mri_reference": False,
        "errors": [],
        "warnings": [],
        "initial_cache_scan": {},
        "final_cache_scan": {},
        "cache_test_moves": [],
        "project_test_moves": [],
        "main_cache_restore": [],
        "last_versions_patch": {},
        "main_cache_validation": {},
        "stable_source_notes": [],
    }

    try:
        if not project_root.exists():
            raise FileNotFoundError(f"Project root not found: {project_root}")
        if not cache_root.exists():
            report["warnings"].append(f"BrainGlobe cache root does not exist yet: {cache_root}")
            if not dry_run:
                cache_root.mkdir(parents=True, exist_ok=True)

        report["initial_cache_scan"] = scan_cache(cache_root)

        # Move cache test atlases.
        cache_quarantine = cache_root / f"_paxinos_v32_12_removed_test_atlases_{stamp}"
        for name in report["initial_cache_scan"].get("paxinos_test_folders", []):
            src = cache_root / name
            if src.exists() and src.is_dir():
                report["cache_test_moves"].append(move_to_quarantine(src, cache_quarantine, dry_run))

        # Move project output test atlases.
        project_test_dirs = collect_project_test_dirs(project_root)
        project_quarantine = project_root / "data" / "output" / f"_v32_12_removed_test_atlases_{stamp}"
        for src in project_test_dirs:
            report["project_test_moves"].append(move_to_quarantine(src, project_quarantine, dry_run))

        # Patch BrainGlobe last_versions.conf.
        report["last_versions_patch"] = patch_last_versions(cache_root, dry_run, stamp)

        # Restore main cache from stable LabelAtlas candidate.
        stable_source, source_notes = find_stable_source(project_root)
        report["stable_source_notes"] = source_notes
        stable_cache = cache_root / STABLE_CACHE_FOLDER
        if args.restore_main_cache:
            if stable_source is None:
                report["warnings"].append("No usable project stable source found; main cache was not restored from project output.")
                report["main_cache_restore"].append("restore skipped: no usable stable source")
            else:
                # Backup current main cache first.
                if stable_cache.exists():
                    backup_root = cache_root / f"_paxinos_v32_12_main_cache_backup_{stamp}"
                    backup_rec = move_to_quarantine(stable_cache, backup_root, dry_run)
                    report["main_cache_restore"].append(f"main cache backup: {backup_rec}")
                copy_rec = copytree_fresh(stable_source, stable_cache, dry_run)
                report["main_cache_restore"].append(f"main cache restored from project source: {copy_rec}")
        else:
            report["main_cache_restore"].append("restore-main-cache not requested")

        report["final_cache_scan"] = scan_cache(cache_root)
        report["main_cache_validation"] = validate_main_cache(cache_root / STABLE_CACHE_FOLDER)

        remaining = report["final_cache_scan"].get("paxinos_test_folders", [])
        if remaining:
            report["warnings"].append(f"Remaining Paxinos test folders in cache: {remaining}")
        if not report["final_cache_scan"].get("paxinos_main_present") and not dry_run:
            report["errors"].append("Main Paxinos cache folder is missing after cleanup.")

        # For dry-run, passed means the dry-run completed, not that changes were applied.
        report["passed"] = len(report["errors"]) == 0
        write_reports(report_dir, report, dry_run)

        print("\nV32.12 LabelAtlas Rebaseline Cleanup")
        print("=" * 72)
        print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
        print(f"PASSED: {report['passed']}")
        print(f"Report dir: {report_dir}")
        print(f"Cache test atlases moved/planned: {len(report['cache_test_moves'])}")
        print(f"Project test atlases moved/planned: {len(report['project_test_moves'])}")
        print(f"Remaining Paxinos test cache folders: {report['final_cache_scan'].get('paxinos_test_folders')}")
        print(f"Main cache present: {report['final_cache_scan'].get('paxinos_main_present')}")
        if report["warnings"]:
            print("\nWarnings:")
            for w in report["warnings"]:
                print(f"- {w}")
        if report["errors"]:
            print("\nErrors:")
            for e in report["errors"]:
                print(f"- {e}")
        print("\nRestart Fiji/ABBA completely before checking the atlas list.")
        return 0 if report["passed"] else 2
    except Exception as exc:
        report["errors"].append(str(exc))
        report["passed"] = False
        try:
            write_reports(report_dir, report, dry_run)
        except Exception:
            pass
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
