from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import tifffile
from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()

# Validated interactively in ABBA after test atlas builds:
# Original V32 arrays behaved as [LR, AP, SI] with metadata orientation LPI.
# ABBA buttons mapped correctly and coronal was upright only after this display-space permutation:
#     new axes = [AP, SI, LR]
#     perm = (1, 2, 0)
#     orientation = PIL
VALIDATED_PERM = (1, 2, 0)
VALIDATED_ORIENTATION = "PIL"
VALIDATION_NOTE = (
    "V32.2 validated ABBA display orientation: perm=(1,2,0), old axes [LR,AP,SI] -> "
    "new axes [AP,SI,LR]. This makes ABBA Coronal/Sagittal/Horizontal buttons match their labels "
    "and makes coronal slices upright."
)


def folder_for(target: str) -> Path:
    if target == "provisional":
        return provisional_folder()
    if target == "official":
        return official_candidate_folder()
    if target == "installed":
        return Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"
    raise ValueError(target)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def transform_array(arr: np.ndarray, perm: tuple[int, int, int] = VALIDATED_PERM) -> np.ndarray:
    if arr.ndim != 3:
        raise RuntimeError(f"Expected 3D array, got shape {arr.shape}")
    return np.ascontiguousarray(np.transpose(arr, perm))


def transformed_affine(old_affine: np.ndarray, perm: tuple[int, int, int] = VALIDATED_PERM) -> np.ndarray:
    """Permute affine columns to match a pure axis permutation without flips.

    This keeps the NIfTI internally honest enough for inspection tools. ABBA/BrainGlobe mainly use
    the TIFF arrays and metadata, but leaving a stale LPI affine after changing the array order would
    be the sort of polite lie that later becomes a bug report with screenshots.
    """
    new_aff = np.array(old_affine, dtype=float, copy=True)
    new_aff[:3, :3] = old_affine[:3, list(perm)]
    # No flips and no origin shift. Voxel [0,0,0] stays at the same physical corner.
    return new_aff


def transform_nifti(path: Path, perm: tuple[int, int, int] = VALIDATED_PERM) -> dict[str, Any]:
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj)
    out = transform_array(np.asarray(data), perm)
    new_aff = transformed_affine(img.affine, perm)
    hdr = img.header.copy()
    old_zooms = img.header.get_zooms()[:3]
    try:
        hdr.set_zooms(tuple(float(old_zooms[i]) for i in perm))
    except Exception:
        pass
    out_img = nib.Nifti1Image(out, new_aff, header=hdr)
    out_img.set_data_dtype(out.dtype)
    nib.save(out_img, str(path))
    return {
        "path": str(path),
        "old_shape": [int(x) for x in data.shape],
        "new_shape": [int(x) for x in out.shape],
        "dtype": str(out.dtype),
        "old_orientation": "".join(nib.aff2axcodes(img.affine)),
        "new_orientation_from_affine": "".join(nib.aff2axcodes(new_aff)),
    }


def transform_tiff(path: Path, perm: tuple[int, int, int] = VALIDATED_PERM) -> dict[str, Any]:
    arr = tifffile.imread(str(path))
    out = transform_array(np.asarray(arr), perm)
    tifffile.imwrite(str(path), out.astype(arr.dtype, copy=False), photometric="minisblack")
    return {
        "path": str(path),
        "old_shape": [int(x) for x in arr.shape],
        "new_shape": [int(x) for x in out.shape],
        "dtype": str(out.dtype),
    }


def already_applied(metadata: dict[str, Any]) -> bool:
    record = metadata.get("v32_2_validated_abba_orientation") or {}
    return bool(record.get("applied") is True and record.get("perm") == list(VALIDATED_PERM))


def patch_metadata(folder: Path, transformed: dict[str, Any], target: str) -> dict[str, Any]:
    mp = folder / "metadata.json"
    metadata = load_json(mp)
    ann_shape = None
    ref_shape = None
    if (folder / "annotation.nii.gz").exists():
        ann_shape = [int(x) for x in nib.load(str(folder / "annotation.nii.gz")).shape]
    if (folder / "reference.nii.gz").exists():
        ref_shape = [int(x) for x in nib.load(str(folder / "reference.nii.gz")).shape]

    metadata["orientation"] = VALIDATED_ORIENTATION
    if ann_shape:
        metadata["shape"] = ann_shape
        metadata["annotation_shape"] = ann_shape
    if ref_shape:
        metadata["reference_shape"] = ref_shape
    metadata["v32_2_validated_abba_orientation"] = {
        "applied": True,
        "target": target,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "perm": list(VALIDATED_PERM),
        "old_axis_model": "[LR, AP, SI] / LPI",
        "new_axis_model": "[AP, SI, LR] / PIL",
        "reason": "ABBA button mapping and upright coronal display were validated interactively.",
        "warning": "Display-orientation fix only. It is not an anatomical nonlinear registration.",
    }
    metadata["orientation_note"] = VALIDATION_NOTE
    metadata["reference_strategy"] = metadata.get(
        "reference_strategy",
        "provisional_label_edge_reference_generated_from_annotation_boundaries",
    )
    metadata["files"] = metadata.get("files", {})
    metadata["files"]["reference_tiff"] = "reference.tiff"
    metadata["files"]["annotation_tiff"] = "annotation.tiff"
    metadata["files"]["reference_nifti"] = "reference.nii.gz"
    metadata["files"]["annotation_nifti"] = "annotation.nii.gz"
    # Do not advertise hemispheres as a normal display/reference channel. ABBA/BrainGlobe can still
    # use hemispheres_file, but the file should not masquerade as a third visible reference channel.
    metadata["files"].pop("hemispheres", None)
    if (folder / "hemispheres.tiff").exists():
        metadata["hemispheres_file"] = "hemispheres.tiff"
    save_json(mp, metadata)
    return {
        "metadata_path": str(mp),
        "orientation": metadata.get("orientation"),
        "shape": metadata.get("shape"),
        "annotation_shape": metadata.get("annotation_shape"),
        "reference_shape": metadata.get("reference_shape"),
        "files_has_hemispheres_channel": "hemispheres" in metadata.get("files", {}),
    }


def run(target: str, force: bool = False) -> dict[str, Any]:
    folder = folder_for(target)
    if not folder.exists():
        raise FileNotFoundError(folder)
    mp = folder / "metadata.json"
    if not mp.exists():
        raise FileNotFoundError(mp)
    metadata = load_json(mp)
    if already_applied(metadata) and not force:
        return {
            "target": target,
            "folder": str(folder),
            "skipped": True,
            "reason": "V32.2 orientation already applied. Use --force to reapply, which is usually a bad idea.",
            "passed": True,
        }

    transformed: dict[str, Any] = {"nifti": [], "tiff": []}
    for name in ["reference.nii.gz", "annotation.nii.gz"]:
        path = folder / name
        if path.exists():
            transformed["nifti"].append(transform_nifti(path))
    for name in ["reference.tiff", "annotation.tiff", "hemispheres.tiff"]:
        path = folder / name
        if path.exists():
            transformed["tiff"].append(transform_tiff(path))

    meta_result = patch_metadata(folder, transformed, target)

    shapes = []
    for path in [folder / "reference.nii.gz", folder / "annotation.nii.gz"]:
        if path.exists():
            shapes.append(tuple(nib.load(str(path)).shape))
    for path in [folder / "reference.tiff", folder / "annotation.tiff"]:
        if path.exists():
            shapes.append(tuple(tifffile.imread(str(path)).shape))
    passed = bool(shapes and len(set(shapes)) == 1 and meta_result["orientation"] == VALIDATED_ORIENTATION)
    return {
        "target": target,
        "folder": str(folder),
        "skipped": False,
        "perm": list(VALIDATED_PERM),
        "orientation": VALIDATED_ORIENTATION,
        "transformed": transformed,
        "metadata": meta_result,
        "shape_consistency": [list(x) for x in shapes],
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official", "installed"], required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result = run(args.target, force=args.force)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "result": result,
        "passed": bool(result.get("passed")),
    }
    suffix = "_" + args.target
    (REPORTS_DIR / f"v32_2_abba_orientation_report{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "V32.2 validated ABBA orientation report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"PASSED: {report['passed']}",
        "",
        f"Folder: {result.get('folder')}",
        f"Skipped: {result.get('skipped')}",
        f"Perm: {result.get('perm')}",
        f"Orientation: {result.get('orientation')}",
        f"Shape consistency: {result.get('shape_consistency')}",
        "",
        "Interpretation:",
        "- ABBA Coronal/Sagittal/Horizontal button mapping uses this display-space orientation.",
        "- Coronal is upright after perm=(1,2,0).",
        "- This does not solve the lack of a real anatomical reference image.",
    ]
    (REPORTS_DIR / f"v32_2_abba_orientation_report{suffix}.txt").write_text("\n".join(lines), encoding="utf-8")
    (REPORTS_DIR / "v32_2_abba_orientation_report.txt").write_text("\n".join(lines), encoding="utf-8")

    table = Table(title=f"V32.2 ABBA orientation ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Skipped", str(result.get("skipped")))
    table.add_row("Perm", str(result.get("perm")))
    table.add_row("Orientation", str(result.get("orientation")))
    table.add_row("Shapes", str(result.get("shape_consistency"))[:80])
    console.print(table)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
