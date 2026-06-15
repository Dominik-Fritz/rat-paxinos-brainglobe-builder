#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V32.27 Final ABBA LabelAtlas Baseline

This is the release-candidate version of the fixes that made the working root
usable in ABBA:
- apply the validated V32.2 display-space orientation when needed
  old raw/build axes [LR, AP, SI] -> final axes [AP, SI, LR], perm=(1,2,0)
- preserve annotation.nii.gz / annotation.tiff as full label volumes
- rebuild reference.nii.gz / reference.tiff as a 2D coronal in-plane border proxy
- keep hemispheres.tiff empty to avoid helper display panels
- document the required ABBA display state: reference Ch.0 ON, borders Ch.1 OFF

No MRI, Waxholm, SIGMA or NeuroRat reference channel experiment is run here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR_NAME = f"{ATLAS_NAME}_v1.0"
REPORT_SUBDIR = "v32_27_final_abba_labelatlas_baseline"
FINAL_SHAPE = (608, 286, 409)  # [AP, SI, LR], validated in ABBA
RAW_OR_OLD_SHAPE = (409, 608, 286)  # [LR, AP, SI], old/failed candidate display
VALIDATED_PERM = (1, 2, 0)
VALIDATED_ORIENTATION = "PIL"


def iso_now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_root(cli: Optional[str]) -> Path:
    if cli:
        return Path(cli).resolve()
    return Path.cwd().resolve()


def import_image_libs():
    try:
        import numpy as np  # type: ignore
        import nibabel as nib  # type: ignore
        import tifffile  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency for atlas finalization. Run run_builder.bat so the local .venv installs requirements first."
        ) from exc
    return np, nib, tifffile


def atlas_dirs(root: Path, target: str) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    if target in {"all", "provisional"}:
        out.append(("provisional", root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME))
    if target in {"all", "official"}:
        out.append(("official", root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME))
    if target in {"all", "installed", "cache"}:
        out.append(("installed", Path.home() / ".brainglobe" / CACHE_DIR_NAME))
    return out


def backup_file(path: Path, backup_root: Path, actions: List[Dict[str, Any]], dry_run: bool) -> Optional[Path]:
    if not path.exists():
        return None
    rel_name = path.name
    dst = backup_root / rel_name
    if dry_run:
        actions.append({"action": "would_backup", "src": str(path), "dst": str(dst)})
        return dst
    backup_root.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst = backup_root / f"{path.stem}_{stamp()}{path.suffix}"
    shutil.copy2(path, dst)
    actions.append({"action": "backup", "src": str(path), "dst": str(dst)})
    return dst


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, obj: Dict[str, Any], dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    if dry_run:
        actions.append({"action": "would_write_metadata", "path": str(path)})
        return
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    actions.append({"action": "write_metadata", "path": str(path)})


def as_label_uint16(arr):
    np, _nib, _tifffile = import_image_libs()
    # Paxinos labels sometimes arrive as float32 but integer-valued.
    if not np.issubdtype(arr.dtype, np.integer):
        arr = np.rint(arr)
    arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
    return arr


def maybe_reorient_array(arr, actions: List[Dict[str, Any]], label: str):
    np, _nib, _tifffile = import_image_libs()
    shape = tuple(int(x) for x in arr.shape)
    if shape == FINAL_SHAPE:
        actions.append({"action": "orientation_already_final", "label": label, "shape": list(shape)})
        return np.ascontiguousarray(arr), False
    if shape == RAW_OR_OLD_SHAPE:
        out = np.ascontiguousarray(np.transpose(arr, VALIDATED_PERM))
        actions.append({
            "action": "apply_validated_orientation_perm",
            "label": label,
            "old_shape": list(shape),
            "new_shape": list(out.shape),
            "perm": list(VALIDATED_PERM),
            "axis_model": "old [LR,AP,SI] -> final [AP,SI,LR]",
        })
        return out, True
    actions.append({"action": "unexpected_shape_no_orientation_change", "label": label, "shape": list(shape)})
    return np.ascontiguousarray(arr), False


def permuted_affine(affine):
    np, _nib, _tifffile = import_image_libs()
    new_aff = np.array(affine, dtype=float, copy=True)
    try:
        new_aff[:3, :3] = affine[:3, list(VALIDATED_PERM)]
    except Exception:
        pass
    return new_aff


def read_annotation(atlas_dir: Path, actions: List[Dict[str, Any]]):
    np, nib, tifffile = import_image_libs()
    ann_nii = atlas_dir / "annotation.nii.gz"
    ann_tif = atlas_dir / "annotation.tiff"
    if ann_nii.exists():
        img = nib.load(str(ann_nii))
        arr = as_label_uint16(np.asanyarray(img.dataobj))
        arr2, changed = maybe_reorient_array(arr, actions, "annotation.nii.gz")
        aff = permuted_affine(img.affine) if changed else img.affine
        hdr = img.header.copy()
        return arr2, aff, hdr, "annotation.nii.gz"
    if ann_tif.exists():
        arr = as_label_uint16(tifffile.imread(str(ann_tif)))
        arr2, _changed = maybe_reorient_array(arr, actions, "annotation.tiff")
        aff = np.diag([0.04, 0.04, 0.04, 1.0])
        return arr2, aff, None, "annotation.tiff"
    raise FileNotFoundError(f"Missing annotation.nii.gz/annotation.tiff in {atlas_dir}")


def compute_2d_coronal_edges(annotation):
    np, _nib, _tifffile = import_image_libs()
    if annotation.ndim != 3:
        raise ValueError(f"Expected 3D annotation, got {annotation.shape}")
    edges = np.zeros(annotation.shape, dtype=np.uint8)
    # final shape axis model: [AP, SI, LR]. Axis 0 is coronal stack.
    # Only compute in-plane borders within each coronal slice: axes 1 and 2.
    a = annotation[:, :-1, :]
    b = annotation[:, 1:, :]
    diff = (a != b) & ((a != 0) | (b != 0))
    edges[:, :-1, :] |= diff
    edges[:, 1:, :] |= diff

    a = annotation[:, :, :-1]
    b = annotation[:, :, 1:]
    diff = (a != b) & ((a != 0) | (b != 0))
    edges[:, :, :-1] |= diff
    edges[:, :, 1:] |= diff
    return edges.astype(np.uint16) * np.uint16(65535)


def write_nifti(path: Path, arr, affine, header, dtype, dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    np, nib, _tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": str(dtype)})
        return
    hdr = header.copy() if header is not None else None
    img = nib.Nifti1Image(arr.astype(dtype, copy=False), affine, header=hdr)
    img.set_data_dtype(dtype)
    nib.save(img, str(path))
    actions.append({"action": "write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": str(dtype)})


def write_tiff(path: Path, arr, dtype, dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    _np, _nib, tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": str(dtype)})
        return
    tifffile.imwrite(str(path), arr.astype(dtype, copy=False), photometric="minisblack")
    actions.append({"action": "write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": str(dtype)})


def patch_metadata(atlas_dir: Path, ann, ref, dry_run: bool, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    meta = load_json(meta_path)
    meta.update({
        "atlas_name": ATLAS_NAME,
        "name": ATLAS_NAME,
        "orientation": VALIDATED_ORIENTATION,
        "shape": [int(x) for x in ann.shape],
        "annotation_shape": [int(x) for x in ann.shape],
        "reference_shape": [int(x) for x in ref.shape],
        "reference_file": "reference.tiff",
        "annotation_file": "annotation.tiff",
        "hemispheres_file": "hemispheres.tiff",
        "reference_strategy": "labelatlas_coronal_2d_inplane_border_display_proxy_no_mri",
        "v32_2_validated_abba_orientation": {
            "applied": True,
            "perm": list(VALIDATED_PERM),
            "old_axis_model": "[LR, AP, SI]",
            "new_axis_model": "[AP, SI, LR]",
            "orientation": VALIDATED_ORIENTATION,
            "reason": "ABBA Coronal/Sagittal/Horizontal button mapping and upright coronal display baseline.",
        },
        "labelatlas_display_baseline": {
            "version": "V32.27",
            "created_at": iso_now(),
            "purpose": "Release-candidate final baseline matching the working root display.",
            "abba_required_display": {
                "reference_Ch0": "ON",
                "borders_Ch1": "OFF",
                "display_source": "ZSliced_paxinos_watson_rat_40um_reference",
                "avoid_source": "ZSliced_Borders_paxinos_watson_rat_40um_annotation",
            },
            "annotation_files_preserved": True,
            "mri_reference_channels_enabled": False,
            "waxholm_sigma_neurorat_postponed": True,
        },
        "warning": "In ABBA use reference Ch.0 ON and borders Ch.1 OFF. Do not patch annotation.tiff to border-only.",
    })
    files = meta.get("files")
    if not isinstance(files, dict):
        files = {}
    files.update({
        "reference_tiff": "reference.tiff",
        "annotation_tiff": "annotation.tiff",
        "reference_nifti": "reference.nii.gz",
        "annotation_nifti": "annotation.nii.gz",
    })
    files.pop("hemispheres", None)
    meta["files"] = files
    save_json(meta_path, meta, dry_run, actions)
    return meta


def finalize_one(root: Path, label: str, atlas_dir: Path, dry_run: bool, force: bool) -> Dict[str, Any]:
    np, _nib, _tifffile = import_image_libs()
    actions: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    result: Dict[str, Any] = {"target": label, "atlas_dir": str(atlas_dir), "exists": atlas_dir.exists(), "actions": actions, "warnings": warnings, "errors": errors}
    if not atlas_dir.exists():
        warnings.append("atlas directory does not exist; skipped")
        result["skipped"] = True
        result["passed"] = True
        return result
    try:
        backup_root = root / "backups" / REPORT_SUBDIR / stamp() / label
        for name in ["annotation.nii.gz", "annotation.tiff", "reference.nii.gz", "reference.tiff", "hemispheres.tiff", "metadata.json"]:
            backup_file(atlas_dir / name, backup_root, actions, dry_run)

        ann, affine, header, ann_source = read_annotation(atlas_dir, actions)
        ann = as_label_uint16(ann)
        if tuple(ann.shape) != FINAL_SHAPE:
            errors.append(f"Final annotation shape is {tuple(ann.shape)}, expected {FINAL_SHAPE}. Not writing final display proxy.")
            result.update({"annotation_source": ann_source, "annotation_shape": list(ann.shape), "passed": False})
            return result

        ref = compute_2d_coronal_edges(ann)
        hemi = np.zeros(ann.shape, dtype=np.uint8)

        write_nifti(atlas_dir / "annotation.nii.gz", ann, affine, header, np.uint16, dry_run, actions)
        write_tiff(atlas_dir / "annotation.tiff", ann, np.uint16, dry_run, actions)
        write_nifti(atlas_dir / "reference.nii.gz", ref, affine, header, np.uint16, dry_run, actions)
        write_tiff(atlas_dir / "reference.tiff", ref, np.uint16, dry_run, actions)
        write_tiff(atlas_dir / "hemispheres.tiff", hemi, np.uint8, dry_run, actions)
        meta = patch_metadata(atlas_dir, ann, ref, dry_run, actions)

        ann_nonzero = float(np.count_nonzero(ann) / ann.size)
        ref_nonzero = float(np.count_nonzero(ref) / ref.size)
        result.update({
            "annotation_source": ann_source,
            "annotation_shape": [int(x) for x in ann.shape],
            "reference_shape": [int(x) for x in ref.shape],
            "annotation_nonzero_fraction": ann_nonzero,
            "reference_border_nonzero_fraction": ref_nonzero,
            "metadata_orientation": meta.get("orientation"),
            "required_abba_display": {"reference_Ch0": "ON", "borders_Ch1": "OFF"},
            "passed": not errors,
        })
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
        result["passed"] = False
    return result


def write_report(root: Path, report: Dict[str, Any]) -> None:
    report_dir = root / "reports" / REPORT_SUBDIR
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "v32_27_final_abba_labelatlas_baseline_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "V32.27 Final ABBA LabelAtlas Baseline",
        "========================================================================",
        f"Generated: {report['generated_at']}",
        f"Dry run: {report['dry_run']}",
        f"Project root: {report['project_root']}",
        f"PASSED: {report['passed']}",
        "",
        "Purpose:",
        "- Match the working root ABBA display baseline in the release candidate.",
        "- Final axes: [AP, SI, LR], shape [608, 286, 409].",
        "- Preserve annotation as full labels.",
        "- Use reference as 2D coronal in-plane border proxy.",
        "- In ABBA: reference Ch.0 ON, borders Ch.1 OFF.",
        "",
        "Targets:",
    ]
    for r in report.get("targets", []):
        lines.append(f"- {r.get('target')}: exists={r.get('exists')} passed={r.get('passed')} shape={r.get('annotation_shape')} ref_fraction={r.get('reference_border_nonzero_fraction')}")
        if r.get("errors"):
            for e in r["errors"]:
                lines.append(f"  ERROR: {e}")
        if r.get("warnings"):
            for w in r["warnings"]:
                lines.append(f"  warning: {w}")
    lines += [
        "",
        "Errors:",
    ]
    errs = report.get("errors", [])
    if errs:
        lines += [f"- {e}" for e in errs]
    else:
        lines.append("- none")
    (report_dir / "v32_27_final_abba_labelatlas_baseline_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Finalize Paxinos LabelAtlas orientation/display baseline for ABBA.")
    ap.add_argument("--root", default=None, help="Release candidate root. Default: current directory.")
    ap.add_argument("--target", action="append", choices=["all", "provisional", "official", "installed", "cache"], default=None)
    ap.add_argument("--apply", action="store_true", help="Actually write files. Without this, dry-run only.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    root = find_root(args.root)
    dry_run = not args.apply
    targets = args.target or ["all"]
    expanded: List[Tuple[str, Path]] = []
    for t in targets:
        expanded.extend(atlas_dirs(root, t))
    # de-duplicate by label/path
    seen = set()
    unique = []
    for label, path in expanded:
        key = (label, str(path))
        if key not in seen:
            unique.append((label, path)); seen.add(key)

    errors: List[str] = []
    results = []
    for label, path in unique:
        res = finalize_one(root, label, path, dry_run=dry_run, force=args.force)
        results.append(res)
        errors.extend([f"{label}: {e}" for e in res.get("errors", [])])

    # installed/cache may not exist before install; that's okay if not targeted alone.
    strict_results = [r for r in results if r.get("exists")]
    passed = bool(strict_results) and all(bool(r.get("passed")) for r in strict_results) and not errors
    report = {
        "version": "V32.27 Final ABBA LabelAtlas Baseline",
        "generated_at": iso_now(),
        "dry_run": dry_run,
        "project_root": str(root),
        "targets": results,
        "errors": errors,
        "passed": passed,
        "next_step": "Restart Fiji/ABBA and open paxinos_watson_rat_40um with reference Ch.0 ON and borders Ch.1 OFF.",
    }
    write_report(root, report)
    print("V32.27 Final ABBA LabelAtlas Baseline")
    print("========================================================================")
    print(f"Generated: {report['generated_at']}")
    print(f"Dry run: {dry_run}")
    print(f"Root: {root}")
    print(f"PASSED: {passed}")
    for r in results:
        print(f"- {r.get('target')}: exists={r.get('exists')} passed={r.get('passed')} shape={r.get('annotation_shape')} ref_fraction={r.get('reference_border_nonzero_fraction')}")
    if errors:
        print("Errors:")
        for e in errors:
            print("-", e)
    else:
        print("Errors: none")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
