#!/usr/bin/env python
"""Finalize the three-channel ABBA display layout.

Ch0 is the 2D coronal label outline, Ch1 a soft region fill, Ch2 the distance to
that outline. The annotation volumes and structures.json are left untouched, and
channels from earlier display experiments are removed.
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
EXPECTED_SHAPE = (608, 286, 409)

SOFT_NAME = "soft_region_fill_reference"
DISTANCE_NAME = "distance_to_2d_outline_reference"

ACTIVE_EXTRA_NAMES = [SOFT_NAME, DISTANCE_NAME]

OBSOLETE_EXTRA_NAMES = [
    "distance_to_boundary_reference",
    "label_boundary_display_reference",
    "clean_coronal_outline_reference",
]

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
        import numpy as np
        import nibabel as nib
        import tifffile
    except Exception as exc:
        raise RuntimeError(
            "Missing numpy/nibabel/tifffile. Run run_builder.bat once so the local .venv is populated."
        ) from exc
    return np, nib, tifffile


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def target_dirs(project_root: Path, target: str) -> List[Tuple[str, Path]]:
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


def backup_file(project_root: Path, target_label: str, path: Path, run_stamp: str, actions: List[Dict[str, Any]], dry_run: bool) -> Optional[Path]:
    if not path.exists():
        return None
    dst = project_root / "backups" / REPORT_DIR_NAME / run_stamp / target_label / path.name
    if dry_run:
        actions.append({"action": "would_backup", "src": str(path), "dst": str(dst)})
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    actions.append({"action": "backup", "src": str(path), "dst": str(dst)})
    return dst


def remove_file(path: Path, actions: List[Dict[str, Any]], dry_run: bool) -> None:
    if not path.exists():
        actions.append({"action": "obsolete_extra_absent", "path": str(path)})
        return
    if dry_run:
        actions.append({"action": "would_remove_obsolete_extra", "path": str(path)})
        return
    path.unlink()
    actions.append({"action": "remove_obsolete_extra", "path": str(path)})


def load_annotation(atlas_dir: Path):
    np, nib, tifffile = import_image_libs()
    ann_nii = atlas_dir / "annotation.nii.gz"
    ann_tiff = atlas_dir / "annotation.tiff"

    if ann_nii.exists():
        img = nib.load(str(ann_nii))
        arr = np.asarray(np.asanyarray(img.dataobj))
        if not np.issubdtype(arr.dtype, np.integer):
            arr = np.rint(arr)
        arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
        return arr, img.affine, img.header.copy(), "annotation.nii.gz"

    if ann_tiff.exists():
        arr = np.asarray(tifffile.imread(str(ann_tiff)))
        if not np.issubdtype(arr.dtype, np.integer):
            arr = np.rint(arr)
        arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
        affine = np.diag([0.04, 0.04, 0.04, 1.0])
        return arr, affine, None, "annotation.tiff"

    raise FileNotFoundError(f"Missing annotation.nii.gz/annotation.tiff in {atlas_dir}")


def compute_2d_outline_uint16(labels, slice_axis: int, include_outer_boundary: bool = True):
    """Build the 2D coronal label outline from in-plane label changes only.

    Changes along the stack axis are ignored; ABBA would show them as filled slabs.
    """
    np, _nib, _tifffile = import_image_libs()

    if slice_axis not in {0, 1, 2}:
        raise ValueError(f"slice_axis must be 0, 1, or 2, got {slice_axis}")

    work = np.moveaxis(labels, slice_axis, 0)
    outline = np.zeros(work.shape, dtype=np.uint16)

    for i in range(work.shape[0]):
        sl = work[i]
        out = np.zeros(sl.shape, dtype=bool)

        diff = sl[:, 1:] != sl[:, :-1]
        if include_outer_boundary:
            active = diff & ((sl[:, 1:] != 0) | (sl[:, :-1] != 0))
            out[:, 1:] |= active
            out[:, :-1] |= active
        else:
            out[:, 1:] |= diff & (sl[:, 1:] != 0) & (sl[:, :-1] != 0)
            out[:, :-1] |= diff & (sl[:, :-1] != 0) & (sl[:, 1:] != 0)

        diff = sl[1:, :] != sl[:-1, :]
        if include_outer_boundary:
            active = diff & ((sl[1:, :] != 0) | (sl[:-1, :] != 0))
            out[1:, :] |= active
            out[:-1, :] |= active
        else:
            out[1:, :] |= diff & (sl[1:, :] != 0) & (sl[:-1, :] != 0)
            out[:-1, :] |= diff & (sl[:-1, :] != 0) & (sl[1:, :] != 0)

        if include_outer_boundary:
            mask = sl != 0
            out[0, :] |= mask[0, :]
            out[-1, :] |= mask[-1, :]
            out[:, 0] |= mask[:, 0]
            out[:, -1] |= mask[:, -1]

        outline[i][out] = 65535

    return np.moveaxis(outline, 0, slice_axis).astype(np.uint16, copy=False)


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
            from scipy.ndimage import gaussian_filter
            soft = gaussian_filter(out.astype(np.float32), sigma=float(sigma))
            soft[~mask] = 0
            out = np.clip(soft, 0, 255).astype(np.uint8)
        except Exception:
            pass

    return out


def make_distance_to_outline(labels, outline_uint16, max_distance: float = 16.0):
    """Build Ch2: per coronal slice, the distance to the 2D outline inside the atlas mask.

    Normalized per slice, zero outside the mask, written as uint16.
    """
    np, _nib, _tifffile = import_image_libs()
    mask = labels != 0
    edge = outline_uint16 != 0
    out = np.zeros(labels.shape, dtype=np.uint16)

    try:
        from scipy.ndimage import distance_transform_edt

        # Axis 0 is the coronal/AP stack in this atlas layout.
        for i in range(labels.shape[0]):
            m = mask[i]
            if not m.any():
                continue

            e = edge[i]
            dist = distance_transform_edt(~e).astype(np.float32)
            dist[~m] = 0

            # Normalize each slice separately.
            max_val = float(dist.max())
            if max_val > 0:
                dist = dist / max_val

            out[i] = np.clip(dist * 65535.0, 0, 65535).astype(np.uint16)

    except Exception:
        # Fallback: use the outline itself, still zero outside the mask.
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


def patch_metadata(atlas_dir: Path, labels, reference_outline, soft_ref, distance_ref, sigma: float, slice_axis: int, actions: List[Dict[str, Any]], dry_run: bool) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    meta = read_json(meta_path)

    for key in EXPERIMENTAL_META_KEYS_TO_REMOVE:
        if key in meta:
            meta.pop(key, None)
            actions.append({"action": "remove_metadata_key", "key": key})

    files = meta.get("files")
    if not isinstance(files, dict):
        files = {}

    for key in OBSOLETE_FILE_KEYS:
        if key in files:
            files.pop(key, None)
            actions.append({"action": "remove_obsolete_file_key", "key": key})

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

    baseline = meta.get("labelatlas_display_baseline")
    if not isinstance(baseline, dict):
        baseline = {}

    # Drop notes left by earlier display layouts.
    for key in [
        "v41_restore_024_display_logic",
        "v42_soft_plus_clean_coronal_outline",
    ]:
        baseline.pop(key, None)

    baseline["v43c_final_three_channel_abba_layout"] = {
        "applied": True,
        "created_at": now(),
        "channels": {
            "Ch0_reference": "0.2.4-style 2D coronal in-plane label-outline proxy",
            "Ch1_soft_region_fill_reference": "soft label-derived orientation reference",
            "Ch2_distance_to_2d_outline_reference": "V43-style 2D per-slice inside-mask distance helper derived from the 0.2.4 outline proxy",
            "Ch3_native_borders": "OFF / not used",
        },
        "slice_axis_for_outline": int(slice_axis),
        "annotation_changed": False,
        "reason": (
            "The old 0.2.4 release displayed useful label outlines through reference.tiff, "
            "not through ABBAs native borders renderer. V43C makes that layout final and adds two useful helper channels."
        ),
    }

    meta.update({
        "atlas_name": ATLAS_NAME,
        "name": ATLAS_NAME,
        "reference_file": "reference.tiff",
        "annotation_file": "annotation.tiff",
        "reference_shape": [int(x) for x in reference_outline.shape],
        "annotation_shape": [int(x) for x in labels.shape],
        "shape": [int(x) for x in labels.shape],
        "files": files,
        "source_references": source_refs,

        "additional_references": ACTIVE_EXTRA_NAMES,

        "reference_strategy": "v43c_final_024_label_outline_reference_with_two_helper_channels",
        "reference_channel_type": "0.2.4-style 2D coronal in-plane label-outline proxy",
        "reference_channel_is_external_anatomy": False,
        "reference_channel_is_real_nissl_mri": False,

        "soft_region_fill_reference": {
            "filename_tiff": f"{SOFT_NAME}.tiff",
            "filename_nifti": f"{SOFT_NAME}.nii.gz",
            "intended_abba_channel": "Ch. 1",
            "derived_from": "annotation label volume",
            "sigma": float(sigma),
            "warning": "Synthetic label-derived display helper. Not Nissl/MRI/external anatomy.",
        },

        "distance_to_2d_outline_reference": {
            "filename_tiff": f"{DISTANCE_NAME}.tiff",
            "filename_nifti": f"{DISTANCE_NAME}.nii.gz",
            "intended_abba_channel": "Ch. 2",
            "derived_from": "0.2.4-style 2D coronal label-outline proxy",
            "warning": "Synthetic display helper. Use native ABBA borders OFF.",
        },

        "labelatlas_display_baseline": baseline,

        "warning": (
            "Final ABBA display recommendation: reference Ch.0 ON, soft_region_fill_reference Ch.1 optional, "
            "distance_to_2d_outline_reference Ch.2 optional, native ABBA borders Ch.3 OFF. "
            "Annotation files are unchanged and remain the real atlas labels."
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
    add_refs = meta.get("additional_references")

    active_present = [name for name in ACTIVE_FILES if (atlas_dir / name).exists()]
    obsolete_present = [name for name in OBSOLETE_EXTRA_FILES if (atlas_dir / name).exists()]

    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    obsolete_keys_present = [key for key in OBSOLETE_FILE_KEYS if key in files]

    ok = (
        add_refs == ACTIVE_EXTRA_NAMES
        and all((atlas_dir / name).exists() for name in ACTIVE_FILES)
        and not obsolete_present
        and not obsolete_keys_present
        and (atlas_dir / "annotation.tiff").exists()
    )

    return {
        "ok": bool(ok),
        "additional_references": add_refs,
        "active_files_present": active_present,
        "obsolete_extra_files_present": obsolete_present,
        "obsolete_extra_file_keys_present": obsolete_keys_present,
        "reference_tiff_exists": (atlas_dir / "reference.tiff").exists(),
        "annotation_tiff_exists": (atlas_dir / "annotation.tiff").exists(),
        "metadata_path": str(atlas_dir / "metadata.json"),
    }


def process_target(
    project_root: Path,
    label: str,
    atlas_dir: Path,
    run_stamp: str,
    sigma: float,
    slice_axis: int,
    max_distance: float,
    dry_run: bool,
    validate_only: bool,
) -> Dict[str, Any]:
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
        annotation_tiff = atlas_dir / "annotation.tiff"
        annotation_nii = atlas_dir / "annotation.nii.gz"
        annotation_tiff_md5_before = md5_file(annotation_tiff)
        annotation_nii_md5_before = md5_file(annotation_nii)

        for name in [
            "metadata.json",
            *ACTIVE_FILES,
            *OBSOLETE_EXTRA_FILES,
        ]:
            backup_file(project_root, label, atlas_dir / name, run_stamp, actions, dry_run)

        labels, affine, header, ann_source = load_annotation(atlas_dir)

        if tuple(labels.shape) != EXPECTED_SHAPE:
            errors.append(f"Unexpected annotation shape {tuple(labels.shape)}; expected {EXPECTED_SHAPE}.")
            result["passed"] = False
            result["validation"] = validate_layout(atlas_dir)
            return result

        reference_outline = compute_2d_outline_uint16(labels, slice_axis=slice_axis, include_outer_boundary=True)
        soft_ref = make_soft_region_fill(labels, sigma=sigma)
        distance_ref = make_distance_to_outline(labels, reference_outline, max_distance=max_distance)

        write_tiff(atlas_dir / "reference.tiff", reference_outline, actions, dry_run)
        write_nifti(atlas_dir / "reference.nii.gz", reference_outline, affine, header, actions, dry_run)

        write_tiff(atlas_dir / f"{SOFT_NAME}.tiff", soft_ref, actions, dry_run)
        write_nifti(atlas_dir / f"{SOFT_NAME}.nii.gz", soft_ref, affine, header, actions, dry_run)

        write_tiff(atlas_dir / f"{DISTANCE_NAME}.tiff", distance_ref, actions, dry_run)
        write_nifti(atlas_dir / f"{DISTANCE_NAME}.nii.gz", distance_ref, affine, header, actions, dry_run)

        for name in OBSOLETE_EXTRA_FILES:
            remove_file(atlas_dir / name, actions, dry_run)

        meta = patch_metadata(atlas_dir, labels, reference_outline, soft_ref, distance_ref, sigma, slice_axis, actions, dry_run)

        annotation_tiff_md5_after = md5_file(annotation_tiff)
        annotation_nii_md5_after = md5_file(annotation_nii)
        annotation_unchanged = (
            annotation_tiff_md5_before == annotation_tiff_md5_after
            and annotation_nii_md5_before == annotation_nii_md5_after
        )

        if not annotation_unchanged:
            errors.append("Annotation checksum changed. This should never happen.")

        validation = validate_layout(atlas_dir) if not dry_run else {
            "ok": True,
            "dry_run_validation": True,
            "additional_references": ACTIVE_EXTRA_NAMES,
            "active_files_present": ACTIVE_FILES,
            "obsolete_extra_files_present": [],
            "obsolete_extra_file_keys_present": [],
        }

        result.update({
            "passed": bool(validation.get("ok")) and annotation_unchanged and not errors,
            "annotation_source": ann_source,
            "annotation_shape": [int(x) for x in labels.shape],
            "annotation_tiff_md5_before": annotation_tiff_md5_before,
            "annotation_tiff_md5_after": annotation_tiff_md5_after,
            "annotation_nii_md5_before": annotation_nii_md5_before,
            "annotation_nii_md5_after": annotation_nii_md5_after,
            "annotation_unchanged": annotation_unchanged,
            "reference_outline_nonzero_fraction": float(np.count_nonzero(reference_outline) / reference_outline.size),
            "soft_reference_nonzero_fraction": float(np.count_nonzero(soft_ref) / soft_ref.size),
            "distance_reference_nonzero_fraction": float(np.count_nonzero(distance_ref) / distance_ref.size),
            "metadata_additional_references": meta.get("additional_references"),
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

    json_path = report_dir / "v43c_restore_v43_distance_channel_report.json"
    md_path = report_dir / "V43C_FINALIZE_THREE_CHANNEL_ABBA_LAYOUT_REPORT.md"
    txt_path = report_dir / "v43c_restore_v43_distance_channel_summary.txt"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines: List[str] = []
    lines.append("# V43C Final Three-Channel ABBA Layout Report\n\n")
    lines.append(f"- Generated: `{report['generated_at']}`\n")
    lines.append(f"- Project root: `{report['project_root']}`\n")
    lines.append(f"- Dry run: `{report['dry_run']}`\n")
    lines.append(f"- Validate only: `{report['validate_only']}`\n")
    lines.append(f"- PASSED: `{report['passed']}`\n\n")

    lines.append("## Final ABBA working layout\n\n")
    lines.append("```text\n")
    lines.append("reference (Ch. 0)                         ON    # 0.2.4-style 2D label outline\n")
    lines.append("soft_region_fill_reference (Ch. 1)         optional\n")
    lines.append("distance_to_2d_outline_reference (Ch. 2)   optional\n")
    lines.append("borders (native Ch. 3)                     OFF / not used\n")
    lines.append("```\n\n")

    lines.append("## Targets\n\n")
    for target in report.get("targets", []):
        validation = target.get("validation", {})
        lines.append(f"### {target.get('target')}\n\n")
        lines.append(f"- Exists: `{target.get('exists')}`\n")
        lines.append(f"- Passed: `{target.get('passed')}`\n")
        lines.append(f"- Atlas dir: `{target.get('atlas_dir')}`\n")
        lines.append(f"- Additional references: `{validation.get('additional_references')}`\n")
        lines.append(f"- Active files present: `{validation.get('active_files_present')}`\n")
        lines.append(f"- Obsolete files present: `{validation.get('obsolete_extra_files_present')}`\n")
        lines.append(f"- Annotation unchanged: `{target.get('annotation_unchanged')}`\n")
        lines.append(f"- Reference outline nonzero fraction: `{target.get('reference_outline_nonzero_fraction')}`\n")
        lines.append(f"- Soft reference nonzero fraction: `{target.get('soft_reference_nonzero_fraction')}`\n")
        lines.append(f"- Distance reference nonzero fraction: `{target.get('distance_reference_nonzero_fraction')}`\n")
        if target.get("errors"):
            lines.append("- Errors:\n")
            for e in target["errors"]:
                lines.append(f"  - `{e}`\n")
        if target.get("warnings"):
            lines.append("- Warnings:\n")
            for w in target["warnings"]:
                lines.append(f"  - `{w}`\n")
        lines.append("\n")

    lines.append("## Notes\n\n")
    lines.append("The native ABBA borders channel can still appear because ABBA derives it from the annotation. It is deliberately not used.\n")
    lines.append("Annotation files are preserved and remain the real atlas labels.\n")

    md_path.write_text("".join(lines), encoding="utf-8")

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
            f"additional_refs={validation.get('additional_references')} "
            f"obsolete_files={validation.get('obsolete_extra_files_present')}"
        )
    txt_path.write_text("\n".join(txt) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Finalize V43C three-channel ABBA display layout.")
    ap.add_argument("--root", default=None, help="Project root. Default: current directory.")
    ap.add_argument("--target", action="append", choices=["all", "provisional", "official", "installed", "cache"], default=None)
    ap.add_argument("--apply", action="store_true", help="Write changes. Without this, dry-run only.")
    ap.add_argument("--validate-only", action="store_true", help="Only validate current layout.")
    ap.add_argument("--strict", action="store_true", help="Return nonzero if validation fails.")
    ap.add_argument("--slice-axis", type=int, default=0, choices=[0, 1, 2], help="Coronal stack axis. Default 0.")
    ap.add_argument("--sigma", type=float, default=0.75, help="Soft-region-fill sigma.")
    ap.add_argument("--max-distance", type=float, default=16.0, help="Max distance for Ch2 distance helper.")
    args = ap.parse_args(argv)

    project_root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    dry_run = not args.apply
    run_stamp = stamp()
    targets = args.target or ["installed"]

    dirs: List[Tuple[str, Path]] = []
    for target in targets:
        dirs.extend(target_dirs(project_root, target))

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
            slice_axis=args.slice_axis,
            max_distance=args.max_distance,
            dry_run=dry_run,
            validate_only=args.validate_only,
        )
        for label, path in unique_dirs
    ]

    existing = [r for r in results if r.get("exists")]
    passed = bool(existing) and all(bool(r.get("passed")) for r in existing)

    report = {
        "version": "V43C final three-channel ABBA layout",
        "generated_at": now(),
        "project_root": str(project_root),
        "dry_run": dry_run,
        "validate_only": bool(args.validate_only),
        "slice_axis": int(args.slice_axis),
        "sigma": float(args.sigma),
        "max_distance": float(args.max_distance),
        "targets": results,
        "passed": passed,
        "final_abba_working_layout": {
            "reference_Ch0": "ON; 0.2.4-style 2D label-outline proxy",
            "soft_region_fill_reference_Ch1": "optional",
            "distance_to_2d_outline_reference_Ch2": "optional; V43-style restored",
            "native_borders_Ch3": "OFF / not used",
        },
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
