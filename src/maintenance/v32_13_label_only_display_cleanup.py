#!/usr/bin/env python3
"""
V32.13 Strict LabelAtlas display cleanup

Goal:
- keep the Paxinos annotation/structures as the only meaningful atlas data source
- hide/remove synthetic display helper sources that create confusing filled duplicate views in ABBA
- quarantine experimental/test atlases from the BrainGlobe cache and project output
- keep backups so the operation can be reversed manually or via the restore helper

This script intentionally does NOT touch raw data.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def to_str(p: Path) -> str:
    return str(p.resolve()) if p.exists() else str(p)


def project_root_from_script() -> Path:
    # src/maintenance/script.py -> project root is two parents above src
    here = Path(__file__).resolve()
    for parent in [here.parents[2], here.parents[1], Path.cwd()]:
        if (parent / "data").exists() or (parent / ".venv").exists():
            return parent
    return Path(r"G:\rat-paxinos-brainglobe-builder")


TEST_ATLAS_PATTERNS = [
    # Waxholm/MRI/reference experiments
    r"paxinos_watson_rat_40um_waxholm",
    r"paxinos_watson_rat_40um_.*waxholm",
    r"paxinos_watson_rat_40um_.*affine",
    r"paxinos_watson_rat_40um_.*multires",
    r"paxinos_watson_rat_40um_.*reference_test",
    # earlier diagnostics
    r"paxinos_watson_rat_40um_sigma_reference_test",
    r"paxinos_watson_rat_40um_null_reference_debug",
    r"paxinos_watson_rat_40um_abba_coronal_upright_test",
    r"paxinos_watson_rat_40um_abba_buttons_test",
    r"paxinos_watson_rat_40um_sag_ap_lr_test",
    r"paxinos_watson_rat_40um_.*orientation",
    r"paxinos_watson_rat_40um_.*debug",
    r"paxinos_watson_rat_40um_.*test",
]
TEST_ATLAS_RE = [re.compile(p, re.IGNORECASE) for p in TEST_ATLAS_PATTERNS]
STABLE_ATLAS_NAME = "paxinos_watson_rat_40um"
STABLE_CACHE_DIR_NAME = "paxinos_watson_rat_40um_v1.0"


def is_test_atlas_name(name: str) -> bool:
    # Keep exact stable atlas only. Quarantine paxinos variants with suffixes.
    if name == STABLE_ATLAS_NAME or name == STABLE_CACHE_DIR_NAME:
        return False
    return any(rx.search(name) for rx in TEST_ATLAS_RE)


def safe_backup_path(base_backup_dir: Path, original: Path, label: str) -> Path:
    name = original.name
    return base_backup_dir / label / name


def copy_or_move_dir(src: Path, dst: Path, action: str, dry_run: bool, log: List[Dict[str, Any]]) -> None:
    log.append({"action": action, "src": str(src), "dst": str(dst), "dry_run": dry_run})
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # keep previous backups rather than overwriting them
        dst = dst.with_name(dst.name + "__dup_" + now_stamp())
    if action == "copytree":
        shutil.copytree(src, dst)
    elif action == "move":
        shutil.move(str(src), str(dst))
    else:
        raise ValueError(action)


def backup_file(src: Path, dst: Path, dry_run: bool, log: List[Dict[str, Any]]) -> None:
    log.append({"action": "backup_file", "src": str(src), "dst": str(dst), "dry_run": dry_run})
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dst)


def import_optional_modules() -> Tuple[Any, Any, Any]:
    try:
        import numpy as np
    except Exception as e:
        raise RuntimeError("numpy is required for this cleanup") from e
    try:
        import nibabel as nib
    except Exception as e:
        raise RuntimeError("nibabel is required for NIfTI reference patching") from e
    try:
        import tifffile
    except Exception as e:
        raise RuntimeError("tifffile is required for TIFF reference patching") from e
    return np, nib, tifffile


def read_metadata_shape(atlas_dir: Path) -> Optional[Tuple[int, int, int]]:
    meta_path = atlas_dir / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        shape = meta.get("shape") or meta.get("resolution_shape")
        if isinstance(shape, list) and len(shape) == 3:
            return tuple(int(x) for x in shape)
    except Exception:
        return None
    return None


def get_reference_shape_and_affine(atlas_dir: Path, np: Any, nib: Any) -> Tuple[Tuple[int, int, int], Any, Any]:
    # Prefer annotation because this is the meaningful label grid.
    for candidate in [atlas_dir / "annotation.nii.gz", atlas_dir / "reference.nii.gz"]:
        if candidate.exists():
            img = nib.load(str(candidate))
            return tuple(int(x) for x in img.shape[:3]), img.affine, img.header.copy()
    shape = read_metadata_shape(atlas_dir)
    if shape is None:
        raise RuntimeError(f"Cannot infer atlas shape for {atlas_dir}")
    return shape, np.eye(4), None


def patch_metadata(atlas_dir: Path, mode: str, zero_hemi: bool, dry_run: bool, log: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    result: Dict[str, Any] = {"path": str(meta_path), "exists": meta_path.exists(), "patched": False}
    if not meta_path.exists():
        return result
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        result["error"] = repr(e)
        return result
    before_keys = {k: meta.get(k) for k in ["reference_strategy", "additional_references", "name", "orientation", "shape"]}
    meta["reference_strategy"] = "strict_label_only_zero_reference_no_mri"
    meta["additional_references"] = []
    meta["v32_13_label_only_display_cleanup"] = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "zero_reference": True,
        "zero_hemispheres": bool(zero_hemi),
        "purpose": "hide synthetic/reference/helper display sources in ABBA and keep the Paxinos label atlas as the active baseline",
        "stable_promote": False,
        "mri_reference_channels_postponed": True,
    }
    result["before"] = before_keys
    result["after"] = {k: meta.get(k) for k in ["reference_strategy", "additional_references", "name", "orientation", "shape"]}
    result["patched"] = True
    log.append({"action": "patch_metadata", "path": str(meta_path), "dry_run": dry_run})
    if not dry_run:
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def write_zero_nifti(path: Path, shape: Tuple[int, int, int], affine: Any, header: Any, dtype: str, np: Any, nib: Any, dry_run: bool, log: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = {"path": str(path), "shape": list(shape), "dtype": dtype, "written": False, "dry_run": dry_run}
    log.append({"action": "write_zero_nifti", "path": str(path), "shape": list(shape), "dtype": dtype, "dry_run": dry_run})
    if dry_run:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros(shape, dtype=np.dtype(dtype))
    img = nib.Nifti1Image(arr, affine)
    if header is not None:
        try:
            img.header.set_data_dtype(np.dtype(dtype))
        except Exception:
            pass
    nib.save(img, str(path))
    result["written"] = True
    return result


def write_zero_tiff(path: Path, shape: Tuple[int, int, int], dtype: str, np: Any, tifffile: Any, dry_run: bool, log: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = {"path": str(path), "shape": list(shape), "dtype": dtype, "written": False, "dry_run": dry_run}
    log.append({"action": "write_zero_tiff", "path": str(path), "shape": list(shape), "dtype": dtype, "dry_run": dry_run})
    if dry_run:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.zeros(shape, dtype=np.dtype(dtype))
    # BigTIFF keeps us safe if uncompressed output crosses classic TIFF limits.
    tifffile.imwrite(str(path), arr, bigtiff=True)
    result["written"] = True
    return result


def patch_label_only_sources(atlas_dir: Path, mode: str, zero_hemi: bool, dry_run: bool, backup_root: Path, log: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"atlas_dir": str(atlas_dir), "exists": atlas_dir.exists(), "patched": False}
    if not atlas_dir.exists():
        return result
    np, nib, tifffile = import_optional_modules()
    shape, affine, header = get_reference_shape_and_affine(atlas_dir, np, nib)
    result["shape"] = list(shape)

    backup_dir = backup_root / "patched_atlas_file_backups" / atlas_dir.name
    files_to_backup = [
        atlas_dir / "reference.nii.gz",
        atlas_dir / "reference.tiff",
        atlas_dir / "hemispheres.tiff",
        atlas_dir / "metadata.json",
    ]
    for f in files_to_backup:
        if f.exists():
            backup_file(f, backup_dir / f.name, dry_run, log)

    writes: List[Dict[str, Any]] = []
    writes.append(write_zero_nifti(atlas_dir / "reference.nii.gz", shape, affine, header, "uint16", np, nib, dry_run, log))
    writes.append(write_zero_tiff(atlas_dir / "reference.tiff", shape, "uint16", np, tifffile, dry_run, log))
    if zero_hemi:
        writes.append(write_zero_tiff(atlas_dir / "hemispheres.tiff", shape, "uint8", np, tifffile, dry_run, log))
    meta_result = patch_metadata(atlas_dir, mode=mode, zero_hemi=zero_hemi, dry_run=dry_run, log=log)
    result["writes"] = writes
    result["metadata"] = meta_result
    result["patched"] = True
    return result


def quarantine_test_atlases(root: Path, backup_root: Path, label: str, dry_run: bool, log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not root.exists():
        return results
    quarantine_dir = backup_root / "quarantined_test_atlases" / label
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if is_test_atlas_name(child.name):
            dst = quarantine_dir / child.name
            copy_or_move_dir(child, dst, "move", dry_run, log)
            results.append({"name": child.name, "src": str(child), "dst": str(dst), "dry_run": dry_run})
    return results


def patch_last_versions(cache_root: Path, backup_root: Path, dry_run: bool, log: List[Dict[str, Any]]) -> Dict[str, Any]:
    path = cache_root / "last_versions.conf"
    result: Dict[str, Any] = {"path": str(path), "exists": path.exists(), "patched": False, "removed_lines": []}
    if not path.exists():
        return result
    backup_file(path, backup_root / "last_versions.conf.backup", dry_run, log)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    kept: List[str] = []
    removed: List[str] = []
    for line in lines:
        # Only remove explicit test/experimental names; keep exact stable atlas line.
        if any(rx.search(line) for rx in TEST_ATLAS_RE) and STABLE_ATLAS_NAME + "_v1.0" not in line:
            # exact stable name without suffix must not be removed
            if re.search(r"paxinos_watson_rat_40um($|\s|=|:|,|\})", line) and not re.search(r"waxholm|sigma|debug|test|affine|multires|orientation|buttons|coronal|sag_ap_lr", line, re.IGNORECASE):
                kept.append(line)
            else:
                removed.append(line)
        else:
            kept.append(line)
    result["removed_lines"] = removed
    result["patched"] = bool(removed)
    log.append({"action": "patch_last_versions", "path": str(path), "removed_lines": removed, "dry_run": dry_run})
    if not dry_run and removed:
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return result


def write_reports(report_dir: Path, report: Dict[str, Any], dry_run: bool) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "v32_13_label_only_display_cleanup_report.json"
    txt_path = report_dir / "v32_13_label_only_display_cleanup_summary.txt"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines: List[str] = []
    lines.append("V32.13 LabelAtlas strict display cleanup")
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append(f"Dry run: {dry_run}")
    lines.append(f"Mode: {report.get('mode')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Report dir: {report.get('report_dir')}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- Keep only the Paxinos label atlas as the active baseline.")
    lines.append("- Hide synthetic/helper display sources that create duplicate filled views in ABBA.")
    lines.append("- Quarantine experimental/test atlases and patch last_versions.conf.")
    lines.append("- Stable raw data and annotation/structures are not modified.")
    lines.append("")
    lines.append("Stable atlas patch results:")
    for key in ["project_stable", "cache_stable"]:
        r = report.get(key) or {}
        lines.append(f"- {key}: exists={r.get('exists')} patched={r.get('patched')} dir={r.get('atlas_dir')}")
        if r.get("shape"):
            lines.append(f"  shape={r.get('shape')}")
    lines.append("")
    lines.append("Quarantined test atlases:")
    q = report.get("quarantine", {})
    any_q = False
    for label, items in q.items():
        lines.append(f"- {label}: {len(items)}")
        any_q = any_q or bool(items)
        for item in items[:30]:
            lines.append(f"  - {item.get('name')} -> {item.get('dst')}")
        if len(items) > 30:
            lines.append(f"  ... {len(items)-30} more")
    if not any_q:
        lines.append("- none")
    lines.append("")
    lv = report.get("last_versions", {})
    lines.append("last_versions.conf:")
    lines.append(f"- exists={lv.get('exists')} patched={lv.get('patched')} removed_lines={len(lv.get('removed_lines') or [])}")
    lines.append("")
    lines.append("Backups/quarantine:")
    lines.append(f"- {report.get('backup_root')}")
    lines.append("")
    lines.append("ABBA/Fiji next step:")
    lines.append("- Restart Fiji/ABBA completely.")
    lines.append("- Open paxinos_watson_rat_40um only.")
    lines.append("- If filled duplicate views remain, they are likely generated by ABBA from the annotation source itself, not from reference/hemispheres files.")
    lines.append("- Restore is possible from the backup folder or RUN_V32_13_RESTORE_LATEST_BACKUP.bat.")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="V32.13 strict LabelAtlas display cleanup")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mode", choices=["reference-only", "strict"], default="strict",
                    help="reference-only zeros only reference; strict zeros reference and hemispheres display helper")
    ap.add_argument("--restore-latest", action="store_true", help="restore latest v32_13 backup")
    args = ap.parse_args(argv)

    project_root = Path(args.project_root) if args.project_root else project_root_from_script()
    project_root = project_root.resolve()
    generated_at = _dt.datetime.now().isoformat(timespec="seconds")
    stamp = now_stamp()
    report_dir = project_root / "reports" / "v32_13_label_only_display_cleanup"
    backup_root = project_root / "data" / "output" / "v32_13_label_only_display_cleanup_backups" / stamp
    cache_root = Path.home() / ".brainglobe"

    log: List[Dict[str, Any]] = []
    errors: List[str] = []

    project_stable = project_root / "data" / "output" / "brainglobe_official_candidate" / STABLE_ATLAS_NAME
    cache_stable = cache_root / STABLE_CACHE_DIR_NAME
    output_root = project_root / "data" / "output" / "brainglobe_official_candidate"

    if args.restore_latest:
        # Basic restore helper: copy latest backed-up stable atlas files back into project/cache stable folders.
        root = project_root / "data" / "output" / "v32_13_label_only_display_cleanup_backups"
        candidates = sorted([p for p in root.iterdir() if p.is_dir()]) if root.exists() else []
        report: Dict[str, Any] = {
            "generated_at": generated_at,
            "mode": "restore-latest",
            "project_root": str(project_root),
            "report_dir": str(report_dir),
            "backup_root": str(candidates[-1]) if candidates else None,
            "passed": False,
            "errors": [],
            "actions": [],
        }
        if not candidates:
            report["errors"].append("No V32.13 backup folders found.")
            write_reports(report_dir, report, args.dry_run)
            return 2
        latest = candidates[-1]
        backup_files_root = latest / "patched_atlas_file_backups"
        restored = []
        for atlas_dir in [project_stable, cache_stable]:
            src_backup = backup_files_root / atlas_dir.name
            if src_backup.exists() and atlas_dir.exists():
                for f in src_backup.iterdir():
                    dst = atlas_dir / f.name
                    log.append({"action": "restore_file", "src": str(f), "dst": str(dst), "dry_run": args.dry_run})
                    if not args.dry_run:
                        shutil.copy2(f, dst)
                    restored.append({"src": str(f), "dst": str(dst)})
        report["passed"] = True
        report["restored_files"] = restored
        report["actions"] = log
        write_reports(report_dir, report, args.dry_run)
        print(f"Restore report written: {report_dir}")
        return 0

    zero_hemi = args.mode == "strict"

    try:
        quarantine_project = quarantine_test_atlases(output_root, backup_root, "project_output", args.dry_run, log)
        quarantine_cache = quarantine_test_atlases(cache_root, backup_root, "brainglobe_cache", args.dry_run, log)
        last_versions = patch_last_versions(cache_root, backup_root, args.dry_run, log)

        project_patch = patch_label_only_sources(project_stable, args.mode, zero_hemi, args.dry_run, backup_root, log)
        cache_patch = patch_label_only_sources(cache_stable, args.mode, zero_hemi, args.dry_run, backup_root, log)

        # If cache stable was missing but project exists, create it from patched project.
        cache_created = None
        if (not cache_stable.exists()) and project_stable.exists():
            dst = cache_stable
            copy_or_move_dir(project_stable, dst, "copytree", args.dry_run, log)
            cache_created = {"src": str(project_stable), "dst": str(dst), "dry_run": args.dry_run}

        passed = not errors
    except Exception as e:
        errors.append(repr(e))
        project_patch = {}
        cache_patch = {}
        quarantine_project = []
        quarantine_cache = []
        last_versions = {}
        cache_created = None
        passed = False

    report = {
        "generated_at": generated_at,
        "passed": passed,
        "dry_run": args.dry_run,
        "mode": args.mode,
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "backup_root": str(backup_root),
        "stable_atlas_name": STABLE_ATLAS_NAME,
        "project_stable": project_patch,
        "cache_stable": cache_patch,
        "cache_created_from_project": cache_created,
        "quarantine": {
            "project_output": quarantine_project,
            "brainglobe_cache": quarantine_cache,
        },
        "last_versions": last_versions,
        "actions": log,
        "errors": errors,
        "important_note": "annotation.nii.gz, annotation.tiff, structures.json/csv are intentionally preserved; reference is zeroed, and hemispheres is zeroed only in strict mode.",
    }
    write_reports(report_dir, report, args.dry_run)

    print("V32.13 LabelAtlas display cleanup complete.")
    print(f"PASSED: {passed}")
    print(f"Dry run: {args.dry_run}")
    print(f"Mode: {args.mode}")
    print(f"Report dir: {report_dir}")
    if errors:
        print("Errors:")
        for e in errors:
            print("-", e)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
