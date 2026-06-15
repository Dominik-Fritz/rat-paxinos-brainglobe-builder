#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V32.17 LabelAtlas Display Baseline

Purpose
-------
Return the rat Paxinos BrainGlobe/ABBA workflow to a label-only baseline:
- keep paxinos_watson_rat_40um as the only active Paxinos atlas in the BrainGlobe cache
- preserve annotation.nii.gz / annotation.tiff as full label volumes for ABBA label lookup
- replace reference.nii.gz / reference.tiff with a 2D coronal in-plane border proxy
- keep/force hemispheres empty so it cannot create filled helper display panels
- document the required ABBA display state: reference Ch.0 ON, borders Ch.1 OFF

This script intentionally does NOT create MRI/Waxholm/SIGMA/NeuroRat reference channels.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR_NAME = f"{ATLAS_NAME}_v1.0"
REPORT_SUBDIR = "v32_17_labelatlas_display_baseline"

TEST_ATLAS_KEYWORDS = (
    "test",
    "debug",
    "sigma",
    "waxholm",
    "neurorat",
    "orientation",
    "affine",
    "multires",
    "rank",
    "null_reference",
    "coronal_upright",
    "buttons",
    "sag_ap_lr",
)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def copy_backup(src: Path, backup_dir: Path, actions: List[Dict[str, Any]], errors: List[str]) -> Optional[Path]:
    if not src.exists():
        return None
    ensure_dir(backup_dir)
    dst = backup_dir / src.name
    try:
        if src.is_dir():
            if dst.exists():
                dst = backup_dir / f"{src.name}_{now_stamp()}"
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        actions.append({"action": "backup", "src": str(src), "dst": str(dst)})
        return dst
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Could not backup {src}: {exc}")
        return None


def find_project_root(cli_project_root: Optional[str]) -> Path:
    if cli_project_root:
        return Path(cli_project_root).resolve()
    env_root = os.environ.get("PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    default = Path(r"G:\rat-paxinos-brainglobe-builder")
    if default.exists():
        return default
    # Fallback: infer from script location when package is inside project root.
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data").exists() and (parent / "src").exists():
            return parent
    return Path.cwd().resolve()


def cache_root() -> Path:
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / ".brainglobe"
    return Path.home() / ".brainglobe"


def import_image_libs():
    try:
        import nibabel as nib  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Missing dependency: nibabel. Run the install-deps BAT first.") from exc
    try:
        import tifffile  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Missing dependency: tifffile. Run the install-deps BAT first.") from exc
    return nib, tifffile


def load_annotation_nifti(atlas_dir: Path):
    nib, _tifffile = import_image_libs()
    ann_nii = atlas_dir / "annotation.nii.gz"
    if not ann_nii.exists():
        raise FileNotFoundError(f"Missing annotation.nii.gz in {atlas_dir}")
    img = nib.load(str(ann_nii))
    data = np.asanyarray(img.dataobj)
    if not np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.uint16)
    else:
        data = data.astype(np.uint16, copy=False)
    return img, data


def compute_coronal_inplane_edges(annotation: np.ndarray) -> np.ndarray:
    """Compute thin 2D in-plane label borders for each AP/coronal slice.

    Axis convention after the validated V32.2 reorientation:
    shape = [AP, SI, LR]. Therefore each axis-0 slice is coronal/AP.

    This deliberately avoids 3D borders along the stack axis, because those create
    filled-looking surfaces in ABBA's multislice display. Humanity survives another
    off-by-one-dimensional mistake.
    """
    if annotation.ndim != 3:
        raise ValueError(f"Expected a 3D annotation volume, got shape={annotation.shape}")

    edges = np.zeros(annotation.shape, dtype=np.uint8)

    # In-plane axis-1 differences, within each coronal slice.
    a = annotation[:, :-1, :]
    b = annotation[:, 1:, :]
    diff = (a != b) & ((a != 0) | (b != 0))
    edges[:, :-1, :] |= diff
    edges[:, 1:, :] |= diff

    # In-plane axis-2 differences, within each coronal slice.
    a = annotation[:, :, :-1]
    b = annotation[:, :, 1:]
    diff = (a != b) & ((a != 0) | (b != 0))
    edges[:, :, :-1] |= diff
    edges[:, :, 1:] |= diff

    return (edges.astype(np.uint16) * np.uint16(65535))


def write_reference_proxy(atlas_dir: Path, backup_dir: Path, actions: List[Dict[str, Any]], errors: List[str], dry_run: bool = False) -> Dict[str, Any]:
    nib, tifffile = import_image_libs()
    stats: Dict[str, Any] = {"atlas_dir": str(atlas_dir), "exists": atlas_dir.exists(), "patched": False}
    if not atlas_dir.exists():
        return stats

    img, ann = load_annotation_nifti(atlas_dir)
    ref = compute_coronal_inplane_edges(ann)

    stats.update({
        "annotation_shape": list(map(int, ann.shape)),
        "annotation_dtype": str(ann.dtype),
        "annotation_nonzero_fraction": float(np.count_nonzero(ann) / ann.size),
        "reference_border_nonzero_fraction": float(np.count_nonzero(ref) / ref.size),
        "reference_strategy": "labelatlas_coronal_2d_inplane_border_display_proxy_no_mri",
    })

    target_files = [atlas_dir / "reference.nii.gz", atlas_dir / "reference.tiff", atlas_dir / "hemispheres.tiff", atlas_dir / "metadata.json"]
    if not dry_run:
        for f in target_files:
            copy_backup(f, backup_dir, actions, errors)

        ref_img = nib.Nifti1Image(ref, affine=img.affine, header=img.header.copy())
        ref_img.set_data_dtype(np.uint16)
        nib.save(ref_img, str(atlas_dir / "reference.nii.gz"))
        tifffile.imwrite(str(atlas_dir / "reference.tiff"), ref, dtype=np.uint16)
        actions.append({"action": "write_reference_proxy", "atlas_dir": str(atlas_dir)})

        # Keep hemispheres empty as a display/helper source. Do not affect annotation.
        hemi = np.zeros(ann.shape, dtype=np.uint8)
        tifffile.imwrite(str(atlas_dir / "hemispheres.tiff"), hemi, dtype=np.uint8)
        actions.append({"action": "write_empty_hemispheres", "path": str(atlas_dir / "hemispheres.tiff")})

        # Metadata: document the baseline and how ABBA must be configured.
        meta_path = atlas_dir / "metadata.json"
        meta: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Could not parse metadata.json in {atlas_dir}: {exc}")
                meta = {}
        meta.update({
            "atlas_name": ATLAS_NAME,
            "name": ATLAS_NAME,
            "reference_file": "reference.tiff",
            "annotation_file": "annotation.tiff",
            "hemispheres_file": "hemispheres.tiff",
            "reference_strategy": "labelatlas_coronal_2d_inplane_border_display_proxy_no_mri",
            "labelatlas_display_baseline": {
                "version": "V32.17",
                "created_at": iso_now(),
                "purpose": "Use reference channel as a clean 2D coronal border display proxy; keep annotation full for ABBA label lookup.",
                "abba_required_display": {
                    "reference_Ch0": "ON",
                    "borders_Ch1": "OFF",
                    "reason": "ABBA-generated borders can show 3D boundary surfaces as filled panels in MultiSlice view.",
                },
                "annotation_files_preserved": True,
                "mri_reference_channels_enabled": False,
                "waxholm_sigma_neurorat_postponed": True,
            },
            "warning": "LabelAtlas display baseline: in ABBA use reference Ch.0 ON and borders Ch.1 OFF. Do not patch annotation.tiff; it is required as full label volume.",
        })
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        actions.append({"action": "update_metadata", "path": str(meta_path)})

    stats["patched"] = not dry_run
    return stats


def is_test_paxinos_atlas_dir(p: Path) -> bool:
    name = p.name.lower()
    if not name.startswith(f"{ATLAS_NAME}_".lower()):
        return False
    if name == CACHE_DIR_NAME.lower():
        return False
    return any(k in name for k in TEST_ATLAS_KEYWORDS)


def quarantine_test_atlases(bg_root: Path, project_root: Path, actions: List[Dict[str, Any]], errors: List[str], dry_run: bool = False) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if not bg_root.exists():
        return result
    quarantine_dir = project_root / "data" / "output" / REPORT_SUBDIR / "quarantined_brainglobe_test_atlases" / now_stamp()
    for child in sorted(bg_root.iterdir()):
        if child.is_dir() and is_test_paxinos_atlas_dir(child):
            entry = {"atlas_dir": str(child), "quarantined": False}
            if not dry_run:
                ensure_dir(quarantine_dir)
                dst = quarantine_dir / child.name
                try:
                    if dst.exists():
                        dst = quarantine_dir / f"{child.name}_{now_stamp()}"
                    shutil.move(str(child), str(dst))
                    actions.append({"action": "quarantine_cache_test_atlas", "src": str(child), "dst": str(dst)})
                    entry.update({"quarantined": True, "dst": str(dst)})
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Could not quarantine {child}: {exc}")
            result.append(entry)
    return result


def clean_last_versions(bg_root: Path, backup_dir: Path, actions: List[Dict[str, Any]], errors: List[str], dry_run: bool = False) -> List[Dict[str, Any]]:
    modified: List[Dict[str, Any]] = []
    candidates = [bg_root / "last_versions.conf", bg_root / "last_versions.json", bg_root / "last_versions"]
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        entry: Dict[str, Any] = {"path": str(path), "changed": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Could not read {path}: {exc}")
            continue
        lines = text.splitlines()
        kept: List[str] = []
        removed: List[str] = []
        for line in lines:
            low = line.lower()
            # Keep the stable atlas line. Remove experimental Paxinos variants only.
            if ATLAS_NAME.lower() in low and ATLAS_NAME.lower() + "_" in low:
                removed.append(line)
            else:
                kept.append(line)
        if removed:
            entry.update({"changed": True, "removed_lines": removed})
            if not dry_run:
                copy_backup(path, backup_dir, actions, errors)
                path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
                actions.append({"action": "clean_last_versions", "path": str(path), "removed_lines": removed})
        modified.append(entry)
    return modified


def write_reports(report_dir: Path, report: Dict[str, Any]) -> None:
    ensure_dir(report_dir)
    (report_dir / "v32_17_labelatlas_display_baseline_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary = []
    summary.append("V32.17 LabelAtlas Display Baseline")
    summary.append("========================================================================")
    summary.append(f"Generated: {report['generated_at']}")
    summary.append(f"PASSED: {report['passed']}")
    summary.append(f"Dry run: {report['dry_run']}")
    summary.append(f"Project root: {report['project_root']}")
    summary.append(f"BrainGlobe root: {report['brainglobe_root']}")
    summary.append("")
    summary.append("Purpose:")
    summary.append("- Keep paxinos_watson_rat_40um as the active LabelAtlas baseline.")
    summary.append("- Preserve annotation.tiff / annotation.nii.gz as full label volumes for ABBA lookup.")
    summary.append("- Use reference channel as a clean 2D coronal in-plane border display proxy.")
    summary.append("- Keep ABBA borders channel OFF, because it creates filled 3D-looking surfaces in MultiSlice view.")
    summary.append("- Do not enable Waxholm/SIGMA/NeuroRat/MRI reference channels.")
    summary.append("")
    cache_stats = report.get("cache_stable", {})
    summary.append("Stable cache atlas:")
    summary.append(f"- exists: {cache_stats.get('exists')}")
    summary.append(f"- patched: {cache_stats.get('patched')}")
    summary.append(f"- shape: {cache_stats.get('annotation_shape')}")
    summary.append(f"- annotation_nonzero_fraction: {cache_stats.get('annotation_nonzero_fraction')}")
    summary.append(f"- reference_border_nonzero_fraction: {cache_stats.get('reference_border_nonzero_fraction')}")
    summary.append("")
    project_stats = report.get("project_stable", {})
    summary.append("Project official candidate atlas:")
    summary.append(f"- exists: {project_stats.get('exists')}")
    summary.append(f"- patched: {project_stats.get('patched')}")
    summary.append(f"- shape: {project_stats.get('annotation_shape')}")
    summary.append(f"- reference_border_nonzero_fraction: {project_stats.get('reference_border_nonzero_fraction')}")
    summary.append("")
    summary.append("Quarantined cache test atlases:")
    q = report.get("quarantined_test_atlases", [])
    if q:
        for item in q:
            summary.append(f"- {item.get('atlas_dir')} -> {item.get('dst', 'dry-run/no move')}")
    else:
        summary.append("- none")
    summary.append("")
    summary.append("Required ABBA display state:")
    summary.append("- Atlas Display: reference (Ch. 0) ON")
    summary.append("- Atlas Display: borders (Ch. 1) OFF")
    summary.append("- If visible, use ZSliced_paxinos_watson_rat_40um_reference as the display source.")
    summary.append("- Do NOT use ZSliced_Borders_paxinos_watson_rat_40um_annotation for display.")
    summary.append("- Do NOT patch annotation.tiff again.")
    summary.append("")
    summary.append("Errors:")
    if report.get("errors"):
        for e in report["errors"]:
            summary.append(f"- {e}")
    else:
        summary.append("- none")
    (report_dir / "v32_17_labelatlas_display_baseline_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply/verify V32.17 LabelAtlas display baseline.")
    parser.add_argument("--project-root", default=None, help="Project root, e.g. G:\\rat-paxinos-brainglobe-builder")
    parser.add_argument("--dry-run", action="store_true", help="Report planned actions without changing files.")
    parser.add_argument("--cache-only", action="store_true", help="Patch only the BrainGlobe cache atlas, not the project official candidate.")
    parser.add_argument("--no-quarantine", action="store_true", help="Do not quarantine experimental Paxinos cache atlases.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = find_project_root(args.project_root)
    bg_root = cache_root()
    report_dir = project_root / "reports" / REPORT_SUBDIR
    backup_dir = project_root / "data" / "output" / REPORT_SUBDIR / "backups" / now_stamp()
    actions: List[Dict[str, Any]] = []
    errors: List[str] = []

    cache_atlas_dir = bg_root / CACHE_DIR_NAME
    project_atlas_dir = project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME

    report: Dict[str, Any] = {
        "generated_at": iso_now(),
        "dry_run": bool(args.dry_run),
        "project_root": str(project_root),
        "brainglobe_root": str(bg_root),
        "stable_atlas_name": ATLAS_NAME,
        "required_abba_display": {
            "reference_Ch0": "ON",
            "borders_Ch1": "OFF",
            "display_source": "ZSliced_paxinos_watson_rat_40um_reference",
            "avoid_source": "ZSliced_Borders_paxinos_watson_rat_40um_annotation",
        },
        "cache_stable": {},
        "project_stable": {},
        "quarantined_test_atlases": [],
        "last_versions_cleanup": [],
        "actions": actions,
        "errors": errors,
    }

    try:
        report["cache_stable"] = write_reference_proxy(cache_atlas_dir, backup_dir / "cache_stable", actions, errors, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Cache stable atlas patch failed: {exc}")

    if not args.cache_only:
        try:
            report["project_stable"] = write_reference_proxy(project_atlas_dir, backup_dir / "project_stable", actions, errors, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Project stable atlas patch failed: {exc}")
    else:
        report["project_stable"] = {"skipped": True, "reason": "--cache-only"}

    if not args.no_quarantine:
        report["quarantined_test_atlases"] = quarantine_test_atlases(bg_root, project_root, actions, errors, dry_run=args.dry_run)
        report["last_versions_cleanup"] = clean_last_versions(bg_root, backup_dir / "last_versions", actions, errors, dry_run=args.dry_run)

    report["passed"] = len(errors) == 0 and bool(report.get("cache_stable", {}).get("exists"))
    write_reports(report_dir, report)

    print("V32.17 LabelAtlas Display Baseline")
    print("========================================================================")
    print(f"Generated: {report['generated_at']}")
    print(f"PASSED: {report['passed']}")
    print(f"Dry run: {report['dry_run']}")
    print(f"Project root: {project_root}")
    print(f"BrainGlobe root: {bg_root}")
    print(f"Report dir: {report_dir}")
    print("")
    print("ABBA display state after restart:")
    print("- reference (Ch. 0): ON")
    print("- borders   (Ch. 1): OFF")
    print("- Use ZSliced_*_reference, not ZSliced_Borders_*")
    print("")
    if errors:
        print("Errors:")
        for e in errors:
            print("-", e)
    else:
        print("Errors: none")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
