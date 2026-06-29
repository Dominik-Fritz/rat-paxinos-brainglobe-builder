#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V34 Synthetic Soft Reference Integration

ABBA-tested replacement for the old border-only display proxy:
- derive a soft reference channel directly from the Paxinos annotation labels
- keep annotation files unchanged as full label volumes
- write reference.nii.gz / reference.tiff with the same shape as annotation
- update metadata with explicit warnings: label-derived, not Nissl/MRI/SIGMA/Waxholm
- optionally process provisional, official, and installed BrainGlobe atlas folders

This is deliberately boring and reproducible. A rare mercy.
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
CACHE_DIR_NAME = f"{ATLAS_NAME}_v1.0"
FINAL_SHAPE = (608, 286, 409)  # [AP, SI, LR], validated in ABBA
REPORT_SUBDIR = "v34_synthetic_soft_reference"
REFERENCE_STRATEGY = "synthetic_label_derived_soft_region_fill_reference"
WARNING_TEXT = (
    "Synthetic soft reference derived from Paxinos labels. "
    "It is not Nissl, MRI, SIGMA, Waxholm, NeuroRat, or any other external anatomical intensity source."
)


def iso_now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_root(cli: Optional[str]) -> Path:
    return Path(cli).resolve() if cli else Path.cwd().resolve()


def import_image_libs():
    try:
        import numpy as np  # type: ignore
        import nibabel as nib  # type: ignore
        import tifffile  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency for synthetic reference generation. Run run_builder.bat so the local .venv installs requirements first."
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


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def backup_file(path: Path, backup_root: Path, actions: List[Dict[str, Any]], dry_run: bool) -> Optional[Path]:
    if not path.exists():
        return None
    dst = backup_root / path.name
    if dst.exists():
        dst = backup_root / f"{path.stem}_{stamp()}{path.suffix}"
    if dry_run:
        actions.append({"action": "would_backup", "src": str(path), "dst": str(dst)})
        return dst
    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)
    actions.append({"action": "backup", "src": str(path), "dst": str(dst)})
    return dst


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_json(path: Path, obj: Dict[str, Any], dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    if dry_run:
        actions.append({"action": "would_write_json", "path": str(path)})
        return
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    actions.append({"action": "write_json", "path": str(path)})


def load_annotation(atlas_dir: Path):
    np, nib, tifffile = import_image_libs()
    ann_nii = atlas_dir / "annotation.nii.gz"
    ann_tiff = atlas_dir / "annotation.tiff"
    if ann_nii.exists():
        img = nib.load(str(ann_nii))
        arr = np.asanyarray(img.dataobj)
        if not np.issubdtype(arr.dtype, np.integer):
            arr = np.rint(arr)
        arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
        return arr, img.affine, img.header.copy(), "annotation.nii.gz"
    if ann_tiff.exists():
        arr = tifffile.imread(str(ann_tiff))
        if not np.issubdtype(arr.dtype, np.integer):
            arr = np.rint(arr)
        arr = np.clip(arr, 0, 65535).astype(np.uint16, copy=False)
        affine = np.diag([0.04, 0.04, 0.04, 1.0])
        return arr, affine, None, "annotation.tiff"
    raise FileNotFoundError(f"Missing annotation.nii.gz/annotation.tiff in {atlas_dir}")


def label_to_display_uint8(labels):
    np, _nib, _tifffile = import_image_libs()
    lab = labels.astype(np.uint64, copy=False)
    out = np.zeros(labels.shape, dtype=np.uint8)
    mask = labels != 0
    # Deterministic pseudo-color/intensity hash. Do not use label ID directly as intensity.
    hashed = ((lab * np.uint64(2654435761)) >> np.uint64(24)) & np.uint64(255)
    out[mask] = hashed.astype(np.uint8)[mask]
    out[(mask) & (out == 0)] = 1
    return out


def gaussian_soften_uint8(base, labels, sigma: float):
    np, _nib, _tifffile = import_image_libs()
    arr = base.astype(np.float32, copy=False)
    if sigma <= 0:
        return arr.astype(np.uint8)

    try:
        from scipy.ndimage import gaussian_filter  # type: ignore
        soft = gaussian_filter(arr, sigma=float(sigma))
    except Exception:
        # Fallback: no smoothing. The atlas still builds instead of dying over a cosmetic blur.
        soft = arr

    soft[labels == 0] = 0
    return np.clip(soft, 0, 255).astype(np.uint8)


def build_soft_reference(labels, sigma: float):
    base = label_to_display_uint8(labels)
    return gaussian_soften_uint8(base, labels, sigma=sigma)


def write_nifti(path: Path, arr, affine, header, dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    np, nib, _tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": "uint8"})
        return
    hdr = header.copy() if header is not None else None
    img = nib.Nifti1Image(arr.astype(np.uint8, copy=False), affine, header=hdr)
    img.set_data_dtype(np.uint8)
    nib.save(img, str(path))
    actions.append({"action": "write_nifti", "path": str(path), "shape": list(arr.shape), "dtype": "uint8"})


def write_tiff(path: Path, arr, dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    _np, _nib, tifffile = import_image_libs()
    if dry_run:
        actions.append({"action": "would_write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": "uint8"})
        return
    tifffile.imwrite(str(path), arr.astype("uint8", copy=False), photometric="minisblack")
    actions.append({"action": "write_tiff", "path": str(path), "shape": list(arr.shape), "dtype": "uint8"})


def patch_metadata(atlas_dir: Path, labels, reference, sigma: float, dry_run: bool, actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    meta = read_json(meta_path)

    files = meta.get("files")
    if not isinstance(files, dict):
        files = {}

    files.update({
        "reference_tiff": "reference.tiff",
        "annotation_tiff": "annotation.tiff",
        "reference_nifti": "reference.nii.gz",
        "annotation_nifti": "annotation.nii.gz",
    })

    meta.update({
        "atlas_name": ATLAS_NAME,
        "name": ATLAS_NAME,
        "reference_file": "reference.tiff",
        "annotation_file": "annotation.tiff",
        "reference_shape": [int(x) for x in reference.shape],
        "annotation_shape": [int(x) for x in labels.shape],
        "shape": [int(x) for x in labels.shape],
        "files": files,
        "reference_strategy": REFERENCE_STRATEGY,
        "reference_channel_type": "synthetic_label_derived_soft_region_fill",
        "reference_channel_sigma": float(sigma),
        "reference_channel_is_external_anatomy": False,
        "reference_channel_is_real_nissl_mri": False,
        "reference_channel_warning": WARNING_TEXT,
        "labelatlas_display_baseline": {
            **(meta.get("labelatlas_display_baseline") if isinstance(meta.get("labelatlas_display_baseline"), dict) else {}),
            "synthetic_soft_reference_v34": {
                "applied": True,
                "created_at": iso_now(),
                "source": "annotation label volume",
                "default_for_abba": True,
                "rejected_for_default": [
                    "label_boundary_reference.tiff",
                    "distance_to_boundary_reference.tiff",
                    "WHS/SIGMA/Waxholm external anatomy",
                ],
                "abba_required_display": {
                    "reference_Ch0": "ON",
                    "borders_Ch1": "OFF",
                },
            },
        },
        "warning": (
            "In ABBA use reference Ch.0 ON and borders Ch.1 OFF. "
            "The reference channel is synthetic and label-derived; it is not Nissl/MRI/SIGMA/Waxholm anatomy."
        ),
    })

    save_json(meta_path, meta, dry_run, actions)
    return meta


def process_one(root: Path, label: str, atlas_dir: Path, sigma: float, dry_run: bool) -> Dict[str, Any]:
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
        warnings.append("atlas directory does not exist; skipped")
        result["skipped"] = True
        result["passed"] = True
        return result

    try:
        labels, affine, header, source = load_annotation(atlas_dir)
        if tuple(labels.shape) != FINAL_SHAPE:
            errors.append(f"Annotation shape {tuple(labels.shape)} does not match expected {FINAL_SHAPE}.")
            result["passed"] = False
            result["annotation_shape"] = list(labels.shape)
            return result

        reference = build_soft_reference(labels, sigma=sigma)
        if tuple(reference.shape) != tuple(labels.shape):
            errors.append(f"Reference shape {tuple(reference.shape)} does not match annotation shape {tuple(labels.shape)}.")
            result["passed"] = False
            return result

        backup_root = root / "backups" / REPORT_SUBDIR / stamp() / label
        backup_file(atlas_dir / "reference.nii.gz", backup_root, actions, dry_run)
        backup_file(atlas_dir / "reference.tiff", backup_root, actions, dry_run)
        backup_file(atlas_dir / "metadata.json", backup_root, actions, dry_run)

        write_nifti(atlas_dir / "reference.nii.gz", reference, affine, header, dry_run, actions)
        write_tiff(atlas_dir / "reference.tiff", reference, dry_run, actions)
        meta = patch_metadata(atlas_dir, labels, reference, sigma=sigma, dry_run=dry_run, actions=actions)

        ref_nonzero = float(np.count_nonzero(reference) / reference.size)
        label_nonzero = float(np.count_nonzero(labels) / labels.size)

        target_tiff = atlas_dir / "reference.tiff"
        result.update({
            "passed": not errors,
            "annotation_source": source,
            "annotation_shape": [int(x) for x in labels.shape],
            "reference_shape": [int(x) for x in reference.shape],
            "annotation_nonzero_fraction": label_nonzero,
            "reference_nonzero_fraction": ref_nonzero,
            "reference_strategy": meta.get("reference_strategy"),
            "reference_tiff_md5": md5_file(target_tiff) if target_tiff.exists() and not dry_run else None,
            "reference_tiff_size_bytes": target_tiff.stat().st_size if target_tiff.exists() and not dry_run else None,
            "sigma": float(sigma),
            "is_external_anatomy": False,
        })
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
        result["passed"] = False

    return result


def write_report(root: Path, report: Dict[str, Any]) -> None:
    report_dir = root / "reports" / REPORT_SUBDIR
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "v34_synthetic_soft_reference_report.json"
    md_path = report_dir / "v34_synthetic_soft_reference_report.md"
    txt_path = report_dir / "v34_synthetic_soft_reference_summary.txt"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# V34 Synthetic Soft Reference Report",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Dry run: `{report['dry_run']}`",
        f"- Project root: `{report['project_root']}`",
        f"- Sigma: `{report['sigma']}`",
        f"- PASSED: `{report['passed']}`",
        "",
        "## Interpretation",
        "",
        "The reference channel was derived from the Paxinos annotation labels. It is exactly congruent with the label volume, but it is not real histology, Nissl, MRI, SIGMA, Waxholm, or NeuroRat anatomy.",
        "",
        "ABBA display recommendation:",
        "",
        "```text",
        "reference (Ch. 0) = ON",
        "borders   (Ch. 1) = OFF",
        "```",
        "",
        "## Targets",
        "",
    ]

    txt_lines = [
        "V34 Synthetic Soft Reference",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Dry run: {report['dry_run']}",
        f"PASSED: {report['passed']}",
        "",
    ]

    for r in report.get("targets", []):
        lines.append(f"### {r.get('target')}")
        lines.append("")
        lines.append(f"- Exists: `{r.get('exists')}`")
        lines.append(f"- Passed: `{r.get('passed')}`")
        lines.append(f"- Atlas dir: `{r.get('atlas_dir')}`")
        lines.append(f"- Annotation shape: `{r.get('annotation_shape')}`")
        lines.append(f"- Reference shape: `{r.get('reference_shape')}`")
        lines.append(f"- Reference fraction: `{r.get('reference_nonzero_fraction')}`")
        if r.get("errors"):
            lines.append("- Errors:")
            for e in r["errors"]:
                lines.append(f"  - `{e}`")
        if r.get("warnings"):
            lines.append("- Warnings:")
            for w in r["warnings"]:
                lines.append(f"  - `{w}`")
        lines.append("")

        txt_lines.append(f"- {r.get('target')}: exists={r.get('exists')} passed={r.get('passed')} shape={r.get('annotation_shape')} ref_fraction={r.get('reference_nonzero_fraction')}")
        if r.get("errors"):
            for e in r["errors"]:
                txt_lines.append(f"  ERROR: {e}")
        if r.get("warnings"):
            for w in r["warnings"]:
                txt_lines.append(f"  warning: {w}")

    if report.get("errors"):
        lines.append("## Errors")
        for e in report["errors"]:
            lines.append(f"- `{e}`")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply synthetic label-derived soft reference channel to Paxinos atlas folders.")
    ap.add_argument("--root", default=None, help="Project root. Default: current directory.")
    ap.add_argument("--target", action="append", choices=["all", "provisional", "official", "installed", "cache"], default=None)
    ap.add_argument("--apply", action="store_true", help="Actually write files. Without this, dry-run only.")
    ap.add_argument("--sigma", type=float, default=0.75, help="Gaussian smoothing sigma for soft fill.")
    args = ap.parse_args()

    root = find_root(args.root)
    dry_run = not args.apply
    targets = args.target or ["all"]

    expanded: List[Tuple[str, Path]] = []
    for t in targets:
        expanded.extend(atlas_dirs(root, t))

    seen = set()
    unique: List[Tuple[str, Path]] = []
    for label, path in expanded:
        key = (label, str(path))
        if key not in seen:
            unique.append((label, path))
            seen.add(key)

    results = []
    errors: List[str] = []
    for label, path in unique:
        res = process_one(root, label, path, sigma=args.sigma, dry_run=dry_run)
        results.append(res)
        errors.extend([f"{label}: {e}" for e in res.get("errors", [])])

    strict_results = [r for r in results if r.get("exists")]
    passed = bool(strict_results) and all(bool(r.get("passed")) for r in strict_results) and not errors

    report = {
        "version": "V34 Synthetic Soft Reference",
        "generated_at": iso_now(),
        "dry_run": dry_run,
        "project_root": str(root),
        "targets": results,
        "errors": errors,
        "passed": passed,
        "sigma": float(args.sigma),
        "reference_strategy": REFERENCE_STRATEGY,
        "next_step": "Restart Fiji/ABBA and open paxinos_watson_rat_40um with reference Ch.0 ON and borders Ch.1 OFF.",
    }
    write_report(root, report)

    print("V34 Synthetic Soft Reference")
    print("========================================================================")
    print(f"Generated: {report['generated_at']}")
    print(f"Dry run: {dry_run}")
    print(f"Root: {root}")
    print(f"PASSED: {passed}")
    for r in results:
        print(f"- {r.get('target')}: exists={r.get('exists')} passed={r.get('passed')} shape={r.get('annotation_shape')} ref_fraction={r.get('reference_nonzero_fraction')}")
    if errors:
        print("Errors:")
        for e in errors:
            print("-", e)
    else:
        print("Errors: none")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
