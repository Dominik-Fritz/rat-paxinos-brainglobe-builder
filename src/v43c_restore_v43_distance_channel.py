#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V43C FINAL - based on the 0.2.4 final orientation/display logic.

This replaces src\v43c_restore_v43_distance_channel.py.

Critical point:
The installed native BrainGlobe atlas can arrive as shape (409, 608, 286).
That is the old/raw display orientation. The 0.2.4 release fixed this by applying:

    old/raw axes [LR, AP, SI] -> final axes [AP, SI, LR]
    perm = (1, 2, 0)
    final shape = (608, 286, 409)

This script restores exactly that behavior, then writes the final three useful ABBA
display channels:

    Ch0 reference                         = 0.2.4-style 2D coronal label-outline proxy
    Ch1 soft_region_fill_reference         = soft label-derived helper
    Ch2 distance_to_2d_outline_reference   = V43-style 2D per-slice inside-mask distance helper

It also sets:
    additional_references = ["soft_region_fill_reference", "distance_to_2d_outline_reference"]

Native ABBA borders are handled separately by V44.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR = f"{ATLAS_NAME}_v1.0"
REPORT_DIR_NAME = "v43c_restore_v43_distance_channel"

FINAL_SHAPE = (608, 286, 409)       # [AP, SI, LR], validated old 0.2.4 ABBA layout
RAW_OR_OLD_SHAPE = (409, 608, 286)  # [LR, AP, SI], native/failed display orientation
VALIDATED_PERM = (1, 2, 0)
VALIDATED_ORIENTATION = "PIL"

SOFT_NAME = "soft_region_fill_reference"
DISTANCE_NAME = "distance_to_2d_outline_reference"
ACTIVE_EXTRA_NAMES = [SOFT_NAME, DISTANCE_NAME]

OBSOLETE_EXTRA_FILES = [
    "distance_to_boundary_reference.tiff",
    "distance_to_boundary_reference.nii.gz",
    "label_boundary_display_reference.tiff",
    "label_boundary_display_reference.nii.gz",
    "clean_coronal_outline_reference.tiff",
    "clean_coronal_outline_reference.nii.gz",
]
ACTIVE_FILES = [
    "reference.tiff",
    "reference.nii.gz",
    f"{SOFT_NAME}.tiff",
    f"{SOFT_NAME}.nii.gz",
    f"{DISTANCE_NAME}.tiff",
    f"{DISTANCE_NAME}.nii.gz",
]
OBSOLETE_FILE_KEYS = [
    "distance_to_boundary_reference_tiff",
    "distance_to_boundary_reference_nifti",
    "label_boundary_display_reference_tiff",
    "label_boundary_display_reference_nifti",
    "clean_coronal_outline_reference_tiff",
    "clean_coronal_outline_reference_nifti",
]
EXPERIMENTAL_META_KEYS_TO_REMOVE = [
    "synthetic_reference_channels",
    "additional_references_note",
    "debug_zero_boundary_channel_test",
    "debug_zero_boundary_channel_test_note",
    "v41b_force_single_reference_layout",
    "clean_coronal_outline_reference",
]


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def import_image_libs():
    try:
        import numpy as np  # type: ignore
        import nibabel as nib  # type: ignore
        import tifffile  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing numpy/nibabel/tifffile in the active environment.") from exc
    return np, nib, tifffile


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def atlas_dirs(project_root: Path, target: str) -> List[Tuple[str, Path]]:
    dirs: List[Tuple[str, Path]] = []
    if target in {"all", "provisional"}:
        dirs.append(("provisional", project_root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME))
    if target in {"all", "official"}:
        dirs.append(("official", project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME))
    if target in {"all", "installed", "cache"}:
        dirs.append(("installed", Path.home() / ".brainglobe" / CACHE_DIR))
    return dirs


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_json_error": str(exc)}


def write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def backup_file(project_root: Path, target_label: str, path: Path, run_stamp: str, actions: List[Dict[str, Any]], dry_run: bool) -> None:
    if not path.exists():
        return
    dst = project_root / "backups" / REPORT_DIR_NAME / run_stamp / target_label / path.name
    if dry_run:
        actions.append({"action": "would_backup", "src": str(path), "dst": str(dst)})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    actions.append({"action": "backup", "src": str(path), "dst": str(dst)})


def remove_file(path: Path, actions: List[Dict[str, Any]], dry_run: bool) -> None:
    if not path.exists():
        actions.append({"action": "obsolete_extra_absent", "path": str(path)})
        return
    if dry_run:
        actions.append({"action": "would_remove_obsolete_extra", "path": str(path)})
        return
    path.unlink()
    actions.append({"action": "remove_obsolete_extra", "path": str(path)})


def as_label_uint16(arr):
    np, _nib, _tifffile = import_image_libs()
    if not np.issubdtype(arr.dtype, np.integer):
        arr = np.rint(arr)
    return np.clip(arr, 0, 65535).astype(np.uint16, copy=False)


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


def load_annotation(atlas_dir: Path, actions: List[Dict[str, Any]]):
    np, nib, tifffile = import_image_libs()
    ann_nii = atlas_dir / "annotation.nii.gz"
    ann_tiff = atlas_dir / "annotation.tiff"

    if ann_nii.exists():
        img = nib.load(str(ann_nii))
        arr = as_label_uint16(np.asanyarray(img.dataobj))
        arr2, changed = maybe_reorient_array(arr, actions, "annotation.nii.gz")
        affine = permuted_affine(img.affine) if changed else img.affine
        header = img.header.copy()
        return arr2, affine, header, "annotation.nii.gz"

    if ann_tiff.exists():
        arr = as_label_uint16(tifffile.imread(str(ann_tiff)))
        arr2, _changed = maybe_reorient_array(arr, actions, "annotation.tiff")
        affine = np.diag([0.04, 0.04, 0.04, 1.0])
        return arr2, affine, None, "annotation.tiff"

    raise FileNotFoundError(f"Missing annotation.nii.gz/annotation.tiff in {atlas_dir}")


def compute_2d_coronal_edges_uint16(annotation):
    np, _nib, _tifffile = import_image_libs()
    if annotation.ndim != 3:
        raise ValueError(f"Expected 3D annotation, got {annotation.shape}")
    edges = np.zeros(annotation.shape, dtype=np.uint8)

    # Final shape axis model: [AP, SI, LR]. Axis 0 is coronal stack.
    # Only in-plane borders: axes 1 and 2. No previous/next slice comparison.
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


def make_soft_region_fill(labels, sigma: float):
    np, _nib, _tifffile = import_image_libs()
    lab64 = labels.astype(np.uint64, copy=False)
    out = np.zeros(labels.shape, dtype=np.uint8)
    mask = labels != 0
    hashed = ((lab64 * np.uint64(2654435761)) >> np.uint64(24)) & np.uint64(255)
    out[mask] = hashed.astype(np.uint8)[mask]
    out[mask & (out == 0)] = 1

    if sigma > 0:
        try:
            from scipy.ndimage import gaussian_filter  # type: ignore
            soft = gaussian_filter(out.astype(np.float32), sigma=float(sigma))
            soft[~mask] = 0
            out = np.clip(soft, 0, 255).astype(np.uint8)
        except Exception:
            pass
    return out


def make_distance_to_outline(labels, outline_uint16):
    np, _nib, _tifffile = import_image_libs()
    mask = labels != 0
    edge = outline_uint16 != 0
    out = np.zeros(labels.shape, dtype=np.uint16)

    try:
        from scipy.ndimage import distance_transform_edt  # type: ignore
        for i in range(labels.shape[0]):
            m = mask[i]
            if not m.any():
                continue
            e = edge[i]
            dist = distance_transform_edt(~e).astype(np.float32)
            dist[~m] = 0
            max_val = float(dist.max())
            if max_val > 0:
                dist = dist / max_val
            out[i] = np.clip(dist * 65535.0, 0, 65535).astype(np.uint16)
    except Exception:
        out[edge & mask] = np.uint16(65535)

    return out


def write_tiff(path: Path, arr, actions: List[Dict[str, Any]], dry_run: bool) -> None:
    _np, _nib, tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": str(arr.dtype)})
        return
    tifffile.imwrite(str(path), arr, photometric="minisblack")
    actions.append({"action": "write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": str(arr.dtype)})


def write_nifti(path: Path, arr, affine, header, actions: List[Dict[str, Any]], dry_run: bool) -> None:
    _np, nib, _tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": str(arr.dtype)})
        return
    hdr = header.copy() if header is not None else None
    img = nib.Nifti1Image(arr, affine, header=hdr)
    img.set_data_dtype(arr.dtype)
    nib.save(img, str(path))
    actions.append({"action": "write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": str(arr.dtype)})


def patch_metadata(atlas_dir: Path, labels, reference_outline, soft_ref, distance_ref, sigma: float, actions: List[Dict[str, Any]], dry_run: bool) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    meta = read_json(meta_path)

    for key in EXPERIMENTAL_META_KEYS_TO_REMOVE:
        meta.pop(key, None)

    files = meta.get("files")
    if not isinstance(files, dict):
        files = {}

    for key in OBSOLETE_FILE_KEYS:
        files.pop(key, None)

    files.update({
        "reference_tiff": "reference.tiff",
        "reference_nifti": "reference.nii.gz",
        "annotation_tiff": "annotation.tiff",
        "annotation_nifti": "annotation.nii.gz",
        "soft_region_fill_reference_tiff": f"{SOFT_NAME}.tiff",
        "soft_region_fill_reference_nifti": f"{SOFT_NAME}.nii.gz",
        "distance_to_2d_outline_reference_tiff": f"{DISTANCE_NAME}.tiff",
        "distance_to_2d_outline_reference_nifti": f"{DISTANCE_NAME}.nii.gz",
    })

    source_refs = meta.get("source_references")
    if not isinstance(source_refs, list):
        source_refs = [
            "Paxinos G, Watson C. The Rat Brain in Stereotaxic Coordinates, 6th edition. Academic Press, 2007.",
            "BlueBrainHeadModels v1 / Paxinos-Watson atlas digitization, DOI: 10.5281/zenodo.10926947.",
        ]

    meta.update({
        "atlas_name": ATLAS_NAME,
        "name": ATLAS_NAME,
        "orientation": VALIDATED_ORIENTATION,
        "reference_file": "reference.tiff",
        "annotation_file": "annotation.tiff",
        "reference_shape": [int(x) for x in reference_outline.shape],
        "annotation_shape": [int(x) for x in labels.shape],
        "shape": [int(x) for x in labels.shape],
        "files": files,
        "source_references": source_refs,
        "additional_references": ACTIVE_EXTRA_NAMES,
        "reference_strategy": "v43c_final_three_channel_abba_layout_024_orientation_restored",
        "reference_channel_type": "0.2.4-style 2D coronal in-plane label-outline proxy",
        "reference_channel_is_external_anatomy": False,
        "reference_channel_is_real_nissl_mri": False,
        "v32_2_validated_abba_orientation": {
            "applied": True,
            "perm": list(VALIDATED_PERM),
            "old_axis_model": "[LR, AP, SI]",
            "new_axis_model": "[AP, SI, LR]",
            "orientation": VALIDATED_ORIENTATION,
            "reason": "Restores the working 0.2.4 ABBA coronal/sagittal/horizontal display baseline.",
        },
        "soft_region_fill_reference": {
            "filename_tiff": f"{SOFT_NAME}.tiff",
            "filename_nifti": f"{SOFT_NAME}.nii.gz",
            "intended_abba_channel": "Ch. 1",
            "derived_from": "annotation label volume",
            "sigma": float(sigma),
        },
        "distance_to_2d_outline_reference": {
            "filename_tiff": f"{DISTANCE_NAME}.tiff",
            "filename_nifti": f"{DISTANCE_NAME}.nii.gz",
            "intended_abba_channel": "Ch. 2",
            "derived_from": "0.2.4-style 2D coronal label-outline proxy",
        },
        "warning": (
            "Final ABBA display: reference Ch0 ON, soft_region_fill_reference Ch1 optional, "
            "distance_to_2d_outline_reference Ch2 optional. Native borders source is hidden by V44."
        ),
    })

    if dry_run:
        actions.append({"action": "would_write_metadata", "path": str(meta_path), "additional_references": ACTIVE_EXTRA_NAMES})
    else:
        write_json(meta_path, meta)
        actions.append({"action": "write_metadata", "path": str(meta_path), "additional_references": ACTIVE_EXTRA_NAMES})

    return meta


def validate_layout(atlas_dir: Path) -> Dict[str, Any]:
    meta = read_json(atlas_dir / "metadata.json")
    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    obsolete_present = [name for name in OBSOLETE_EXTRA_FILES if (atlas_dir / name).exists()]
    obsolete_keys = [key for key in OBSOLETE_FILE_KEYS if key in files]
    active_present = [name for name in ACTIVE_FILES if (atlas_dir / name).exists()]
    shape_ok = tuple(meta.get("annotation_shape", [])) == FINAL_SHAPE or tuple(meta.get("shape", [])) == FINAL_SHAPE
    ok = (
        shape_ok
        and meta.get("additional_references") == ACTIVE_EXTRA_NAMES
        and all((atlas_dir / name).exists() for name in ACTIVE_FILES)
        and not obsolete_present
        and not obsolete_keys
        and (atlas_dir / "annotation.tiff").exists()
    )
    return {
        "ok": bool(ok),
        "shape_ok": bool(shape_ok),
        "metadata_shape": meta.get("shape"),
        "metadata_annotation_shape": meta.get("annotation_shape"),
        "additional_references": meta.get("additional_references"),
        "active_files_present": active_present,
        "obsolete_extra_files_present": obsolete_present,
        "obsolete_extra_file_keys_present": obsolete_keys,
        "reference_tiff_exists": (atlas_dir / "reference.tiff").exists(),
        "annotation_tiff_exists": (atlas_dir / "annotation.tiff").exists(),
        "metadata_path": str(atlas_dir / "metadata.json"),
    }


def process_target(project_root: Path, label: str, atlas_dir: Path, run_stamp: str, sigma: float, dry_run: bool, validate_only: bool) -> Dict[str, Any]:
    np, _nib, _tifffile = import_image_libs()
    actions: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []

    result: Dict[str, Any] = {
        "target": label,
        "atlas_dir": str(atlas_dir),
        "exists": atlas_dir.exists(),
        "actions": actions,
        "warnings": warnings,
        "errors": errors,
    }

    if not atlas_dir.exists():
        warnings.append("atlas directory missing; skipped")
        result["passed"] = True
        result["skipped"] = True
        return result

    if validate_only:
        validation = validate_layout(atlas_dir)
        result.update({"passed": bool(validation.get("ok")), "validation": validation})
        return result

    try:
        for name in ["metadata.json", "annotation.tiff", "annotation.nii.gz", *ACTIVE_FILES, *OBSOLETE_EXTRA_FILES]:
            backup_file(project_root, label, atlas_dir / name, run_stamp, actions, dry_run)

        labels, affine, header, ann_source = load_annotation(atlas_dir, actions)
        labels = as_label_uint16(labels)

        if tuple(labels.shape) != FINAL_SHAPE:
            errors.append(f"Final annotation shape is {tuple(labels.shape)}, expected {FINAL_SHAPE}.")
            result["passed"] = False
            result["annotation_shape"] = [int(x) for x in labels.shape]
            result["validation"] = validate_layout(atlas_dir)
            return result

        reference_outline = compute_2d_coronal_edges_uint16(labels)
        soft_ref = make_soft_region_fill(labels, sigma=sigma)
        distance_ref = make_distance_to_outline(labels, reference_outline)

        write_nifti(atlas_dir / "annotation.nii.gz", labels, affine, header, actions, dry_run)
        write_tiff(atlas_dir / "annotation.tiff", labels, actions, dry_run)

        write_nifti(atlas_dir / "reference.nii.gz", reference_outline, affine, header, actions, dry_run)
        write_tiff(atlas_dir / "reference.tiff", reference_outline, actions, dry_run)

        write_nifti(atlas_dir / f"{SOFT_NAME}.nii.gz", soft_ref, affine, header, actions, dry_run)
        write_tiff(atlas_dir / f"{SOFT_NAME}.tiff", soft_ref, actions, dry_run)

        write_nifti(atlas_dir / f"{DISTANCE_NAME}.nii.gz", distance_ref, affine, header, actions, dry_run)
        write_tiff(atlas_dir / f"{DISTANCE_NAME}.tiff", distance_ref, actions, dry_run)

        for name in OBSOLETE_EXTRA_FILES:
            remove_file(atlas_dir / name, actions, dry_run)

        meta = patch_metadata(atlas_dir, labels, reference_outline, soft_ref, distance_ref, sigma, actions, dry_run)

        validation = validate_layout(atlas_dir) if not dry_run else {
            "ok": True,
            "shape_ok": True,
            "metadata_shape": list(FINAL_SHAPE),
            "additional_references": ACTIVE_EXTRA_NAMES,
            "active_files_present": ACTIVE_FILES,
            "obsolete_extra_files_present": [],
            "obsolete_extra_file_keys_present": [],
        }

        result.update({
            "passed": bool(validation.get("ok")) and not errors,
            "annotation_source": ann_source,
            "annotation_shape": [int(x) for x in labels.shape],
            "reference_shape": [int(x) for x in reference_outline.shape],
            "metadata_shape": meta.get("shape"),
            "metadata_additional_references": meta.get("additional_references"),
            "reference_outline_nonzero_fraction": float(np.count_nonzero(reference_outline) / reference_outline.size),
            "soft_reference_nonzero_fraction": float(np.count_nonzero(soft_ref) / soft_ref.size),
            "distance_reference_nonzero_fraction": float(np.count_nonzero(distance_ref) / distance_ref.size),
            "validation": validation,
        })

    except Exception as exc:
        errors.append(str(exc))
        result["passed"] = False
        result["validation"] = validate_layout(atlas_dir) if atlas_dir.exists() else {"ok": False}

    return result


def write_reports(project_root: Path, report: Dict[str, Any]) -> None:
    report_dir = project_root / "reports" / REPORT_DIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "v43c_restore_v43_distance_channel_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = [
        "# V43C Final Three-Channel ABBA Layout Report\n\n",
        f"- Generated: `{report['generated_at']}`\n",
        f"- Project root: `{report['project_root']}`\n",
        f"- Dry run: `{report['dry_run']}`\n",
        f"- Validate only: `{report['validate_only']}`\n",
        f"- PASSED: `{report['passed']}`\n\n",
        "## Final ABBA working layout\n\n",
        "```text\n",
        "reference (Ch. 0)                         ON\n",
        "soft_region_fill_reference (Ch. 1)         optional\n",
        "distance_to_2d_outline_reference (Ch. 2)   optional\n",
        "native borders display source              hidden by V44\n",
        "```\n\n",
    ]
    for target in report.get("targets", []):
        validation = target.get("validation", {})
        md.extend([
            f"### {target.get('target')}\n\n",
            f"- Exists: `{target.get('exists')}`\n",
            f"- Passed: `{target.get('passed')}`\n",
            f"- Atlas dir: `{target.get('atlas_dir')}`\n",
            f"- Annotation shape: `{target.get('annotation_shape')}`\n",
            f"- Additional references: `{validation.get('additional_references')}`\n",
            f"- Active files present: `{validation.get('active_files_present')}`\n",
            f"- Obsolete files present: `{validation.get('obsolete_extra_files_present')}`\n",
        ])
        if target.get("errors"):
            md.append("- Errors:\n")
            for e in target["errors"]:
                md.append(f"  - `{e}`\n")
        md.append("\n")
    (report_dir / "V43C_FINALIZE_THREE_CHANNEL_ABBA_LAYOUT_REPORT.md").write_text("".join(md), encoding="utf-8")

    txt = [
        "V43C Final Three-Channel ABBA Layout",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"PASSED: {report['passed']}",
        "",
    ]
    for target in report.get("targets", []):
        validation = target.get("validation", {})
        txt.append(
            f"{target.get('target')}: passed={target.get('passed')} "
            f"shape={target.get('annotation_shape')} "
            f"additional_refs={validation.get('additional_references')} "
            f"obsolete_files={validation.get('obsolete_extra_files_present')}"
        )
    (report_dir / "v43c_restore_v43_distance_channel_summary.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Finalize V43C three-channel ABBA display layout with restored 0.2.4 orientation.")
    ap.add_argument("--root", default=None, help="Project root. Default: current directory.")
    ap.add_argument("--target", action="append", choices=["all", "provisional", "official", "installed", "cache"], default=None)
    ap.add_argument("--apply", action="store_true", help="Write changes. Without this, dry-run only.")
    ap.add_argument("--validate-only", action="store_true", help="Only validate current layout.")
    ap.add_argument("--strict", action="store_true", help="Return nonzero if validation fails.")
    ap.add_argument("--sigma", type=float, default=0.75, help="Soft-region-fill sigma.")
    args = ap.parse_args(argv)

    project_root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    dry_run = not args.apply
    run_stamp = stamp()
    targets = args.target or ["installed"]

    dirs: List[Tuple[str, Path]] = []
    for target in targets:
        dirs.extend(atlas_dirs(project_root, target))

    seen = set()
    unique_dirs: List[Tuple[str, Path]] = []
    for label, path in dirs:
        key = (label, str(path).lower())
        if key not in seen:
            unique_dirs.append((label, path))
            seen.add(key)

    results = [
        process_target(
            project_root=project_root,
            label=label,
            atlas_dir=path,
            run_stamp=run_stamp,
            sigma=args.sigma,
            dry_run=dry_run,
            validate_only=args.validate_only,
        )
        for label, path in unique_dirs
    ]

    existing = [r for r in results if r.get("exists")]
    passed = bool(existing) and all(bool(r.get("passed")) for r in existing)

    report = {
        "version": "V43C final three-channel ABBA layout, 0.2.4 orientation restored",
        "generated_at": now(),
        "project_root": str(project_root),
        "dry_run": dry_run,
        "validate_only": bool(args.validate_only),
        "sigma": float(args.sigma),
        "targets": results,
        "passed": passed,
    }
    write_reports(project_root, report)

    print("V43C Final Three-Channel ABBA Layout")
    print("=" * 72)
    print(f"Root: {project_root}")
    print(f"Dry run: {dry_run}")
    print(f"Validate only: {args.validate_only}")
    print(f"PASSED: {passed}")
    for r in results:
        validation = r.get("validation", {})
        print(
            f"- {r.get('target')}: exists={r.get('exists')} passed={r.get('passed')} "
            f"shape={r.get('annotation_shape')} "
            f"additional_refs={validation.get('additional_references')} "
            f"obsolete_files={validation.get('obsolete_extra_files_present')}"
        )
    print()
    print("Report:")
    print(project_root / "reports" / REPORT_DIR_NAME / "V43C_FINALIZE_THREE_CHANNEL_ABBA_LAYOUT_REPORT.md")

    if args.strict and not passed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
