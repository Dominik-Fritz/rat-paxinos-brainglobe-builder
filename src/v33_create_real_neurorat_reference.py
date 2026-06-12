from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import nibabel as nib
from nibabel.processing import resample_from_to
import numpy as np
import tifffile
from rich.console import Console
from rich.table import Table

from utils_paths import RAW_DIR, REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()

# IMPORTANT:
# In this project RAW_DIR already points to:
#   data/raw/bluebrainheadmodels
# Do NOT append "bluebrainheadmodels" again.
SOURCE_REF = RAW_DIR / "NeuroRat_MRI.nii.gz"
TARGET_PAXINOS = RAW_DIR / "Paxinos_Watson_Atlas.nii.gz"


def json_safe(obj):
    """Convert NumPy/Python objects into plain JSON-serializable objects."""
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def folder_for(target: str) -> Path:
    if target == "provisional":
        return provisional_folder()
    if target == "official":
        return official_candidate_folder()
    if target == "installed":
        return Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"
    raise ValueError(target)


def robust_uint16(data: np.ndarray) -> tuple[np.ndarray, dict]:
    arr = np.asarray(data, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise RuntimeError("Reference image contains no finite voxels.")

    p1 = float(np.percentile(finite, 1.0))
    p998 = float(np.percentile(finite, 99.8))
    if p998 <= p1:
        p1 = float(np.nanmin(finite))
        p998 = float(np.nanmax(finite))

    clipped = np.clip(arr, p1, p998)
    norm = (clipped - p1) / (p998 - p1 + 1e-12)
    out = np.round(norm * 65535.0).astype(np.uint16)

    return out, {
        "input_min": float(np.nanmin(finite)),
        "input_max": float(np.nanmax(finite)),
        "input_mean": float(np.nanmean(finite)),
        "p01": float(p1),
        "p998": float(p998),
        "output_min": int(out.min()),
        "output_max": int(out.max()),
        "output_mean": float(out.mean()),
    }


def update_metadata(folder: Path, stats: dict) -> dict:
    mp = folder / "metadata.json"
    if not mp.exists():
        return {"metadata_exists": False, "patched": False}

    meta = json.loads(mp.read_text(encoding="utf-8"))
    meta["reference_strategy"] = "v33_neurorat_mri_resampled_to_paxinos_geometry"
    meta["reference_source_file"] = "NeuroRat_MRI.nii.gz"
    meta["reference_target_geometry_file"] = "Paxinos_Watson_Atlas.nii.gz"
    meta["reference_resampling"] = {
        "method": "nibabel.processing.resample_from_to",
        "interpolation_order": 1,
        "normalization": "robust percentile 1.0-99.8 to uint16 0-65535",
        "warning": "Technical reference replacement; not validated nonlinear registration.",
        "stats": stats,
    }
    meta["warning"] = "V33/V38 uses NeuroRat MRI resampled onto Paxinos label geometry as practical ABBA reference."
    meta["files"] = meta.get("files", {})
    meta["files"]["reference"] = "reference.nii.gz"
    meta["files"]["reference_tiff"] = "reference.tiff"
    meta["reference_file"] = "reference.tiff"
    meta["additional_references"] = []
    mp.write_text(json.dumps(json_safe(meta), indent=2, ensure_ascii=False), encoding="utf-8")
    return {"metadata_exists": True, "patched": True, "metadata_path": str(mp)}


def create(target: str) -> dict:
    folder = folder_for(target)

    if not SOURCE_REF.exists():
        raise FileNotFoundError(f"Missing NeuroRat MRI source: {SOURCE_REF}")
    if not TARGET_PAXINOS.exists():
        raise FileNotFoundError(f"Missing Paxinos target geometry: {TARGET_PAXINOS}")

    src = nib.load(str(SOURCE_REF))
    tgt = nib.load(str(TARGET_PAXINOS))

    res = resample_from_to(src, (tgt.shape, tgt.affine), order=1)
    ref, stats = robust_uint16(np.asanyarray(res.dataobj))

    folder.mkdir(parents=True, exist_ok=True)
    ref_nii = folder / "reference.nii.gz"
    ref_tiff = folder / "reference.tiff"

    out_img = nib.Nifti1Image(ref, tgt.affine, header=tgt.header.copy())
    out_img.set_data_dtype(np.uint16)
    nib.save(out_img, str(ref_nii))
    tifffile.imwrite(str(ref_tiff), ref, photometric="minisblack")

    ann_shape = None
    same = None
    ann_tiff = folder / "annotation.tiff"
    if ann_tiff.exists():
        ann = tifffile.imread(str(ann_tiff))
        ann_shape = list(ann.shape)
        same = bool(tuple(ann.shape) == tuple(ref.shape))

    meta_res = update_metadata(folder, stats)

    result = {
        "target": target,
        "folder": str(folder),
        "source_reference": str(SOURCE_REF),
        "target_geometry": str(TARGET_PAXINOS),
        "source_shape": list(src.shape),
        "source_orientation": "".join(nib.aff2axcodes(src.affine)),
        "source_zooms": [float(x) for x in src.header.get_zooms()[:3]],
        "target_shape": list(tgt.shape),
        "target_orientation": "".join(nib.aff2axcodes(tgt.affine)),
        "target_zooms": [float(x) for x in tgt.header.get_zooms()[:3]],
        "output_reference_nii": str(ref_nii),
        "output_reference_tiff": str(ref_tiff),
        "output_shape": list(ref.shape),
        "output_dtype": str(ref.dtype),
        "annotation_shape": ann_shape,
        "reference_annotation_same_shape": same,
        "intensity_stats": stats,
        "metadata": meta_res,
        "passed": bool(
            ref_nii.exists()
            and ref_tiff.exists()
            and tuple(ref.shape) == tuple(tgt.shape)
            and (same is True or same is None)
            and int(ref.max()) > int(ref.min())
        ),
    }
    return result


def write_reports(target: str, result: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": target,
        "result": result,
        "passed": bool(result["passed"]),
    }

    safe_report = json_safe(report)
    suffix = "_" + target

    (REPORTS_DIR / f"v33_real_reference_report{suffix}.json").write_text(
        json.dumps(safe_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    def w(name: str, lines: list[str]) -> None:
        (REPORTS_DIR / name).write_text("\n".join(lines), encoding="utf-8")

    w("v33_reference_source_report.txt", [
        "V33/V38 reference source report",
        "=" * 72,
        f"Target: {target}",
        f"Source reference: {result['source_reference']}",
        f"Source shape: {result['source_shape']}",
        f"Source orientation: {result['source_orientation']}",
        f"Source zooms: {result['source_zooms']}",
        f"Target geometry: {result['target_geometry']}",
        f"Target shape: {result['target_shape']}",
        f"Target orientation: {result['target_orientation']}",
        f"Target zooms: {result['target_zooms']}",
    ])

    w("v33_geometry_match_report.txt", [
        "V33/V38 geometry match report",
        "=" * 72,
        f"Target: {target}",
        f"Output shape: {result['output_shape']}",
        f"Annotation shape: {result['annotation_shape']}",
        f"Reference annotation same shape: {result['reference_annotation_same_shape']}",
        f"Output reference NIfTI: {result['output_reference_nii']}",
        f"Output reference TIFF: {result['output_reference_tiff']}",
    ])

    s = result["intensity_stats"]
    w("v33_intensity_report.txt", [
        "V33/V38 intensity report",
        "=" * 72,
        f"Target: {target}",
        f"Input min: {s['input_min']}",
        f"Input max: {s['input_max']}",
        f"Input mean: {s['input_mean']}",
        f"Percentile 1.0: {s['p01']}",
        f"Percentile 99.8: {s['p998']}",
        f"Output min: {s['output_min']}",
        f"Output max: {s['output_max']}",
        f"Output mean: {s['output_mean']}",
    ])

    final = [
        "V33/V38 final reference validation",
        "=" * 72,
        f"Target: {target}",
        f"PASSED: {result['passed']}",
        f"Reference annotation same shape: {result['reference_annotation_same_shape']}",
        f"Output dtype: {result['output_dtype']}",
        f"Metadata patched: {result['metadata'].get('patched')}",
        "",
        "Interpretation:",
        "Practical MRI reference replacement using NeuroRat_MRI resampled to Paxinos geometry.",
        "Not a validated nonlinear registration.",
    ]
    w("v33_final_reference_validation.txt", final)
    w("v33_real_reference_report.txt", final + ["", "Full result:", json.dumps(json_safe(result), indent=2, ensure_ascii=False)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["provisional", "official", "installed"], required=True)
    args = ap.parse_args()

    result = create(args.target)
    write_reports(args.target, result)

    t = Table(title=f"V33/V38 real NeuroRat MRI reference ({args.target})")
    t.add_column("Check")
    t.add_column("Value")
    t.add_row("Passed", str(result["passed"]))
    t.add_row("Source", "NeuroRat_MRI.nii.gz")
    t.add_row("Source orient", result["source_orientation"])
    t.add_row("Target orient", result["target_orientation"])
    t.add_row("Output shape", str(result["output_shape"]))
    t.add_row("Same shape", str(result["reference_annotation_same_shape"]))
    t.add_row("Output min/max", f"{result['intensity_stats']['output_min']} / {result['intensity_stats']['output_max']}")
    console.print(t)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
