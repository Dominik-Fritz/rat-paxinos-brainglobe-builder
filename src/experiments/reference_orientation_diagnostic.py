from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
import tifffile
from nibabel.orientations import aff2axcodes

from utils_paths import RAW_DIR, REPORTS_DIR, installed_atlas_folder

OUT_DIR = REPORTS_DIR / "reference_orientation_diagnostic"

FILES = {
    "paxinos_raw_annotation": RAW_DIR / "Paxinos_Watson_Atlas.nii.gz",
    "sigma_anatomical": RAW_DIR / "SIGMA_Anatomical_Brain_Atlas.nii",
    "neurorat_mri": RAW_DIR / "NeuroRat_MRI.nii.gz",
    "waxholm_mri": RAW_DIR / "Waxholm_Atlas_MRI.nii.gz",
    "installed_reference_tiff": installed_atlas_folder() / "reference.tiff",
    "installed_annotation_tiff": installed_atlas_folder() / "annotation.tiff",
    "installed_hemispheres_tiff": installed_atlas_folder() / "hemispheres.tiff",
    "metadata_json": installed_atlas_folder() / "metadata.json",
}

def as_jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): as_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [as_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, Path):
        return str(x)
    return x

def array_stats(arr: np.ndarray) -> dict[str, Any]:
    a = np.asarray(arr)
    finite = a[np.isfinite(a)] if np.issubdtype(a.dtype, np.floating) else a.ravel()
    return {
        "shape": list(a.shape),
        "dtype": str(a.dtype),
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "mean": float(finite.mean()) if finite.size else None,
        "nonzero_voxels": int(np.count_nonzero(a)),
        "nonzero_fraction": float(np.count_nonzero(a) / a.size) if a.size else 0.0,
    }

def nifti_summary(path: Path) -> dict[str, Any]:
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj)
    return {
        "path": str(path),
        "shape": list(img.shape),
        "dtype": str(img.header.get_data_dtype()),
        "zooms": [float(x) for x in img.header.get_zooms()[:3]],
        "orientation_codes": "".join(aff2axcodes(img.affine)),
        "affine": img.affine.tolist(),
        "stats": array_stats(data),
    }

def tiff_summary(path: Path) -> dict[str, Any]:
    arr = tifffile.imread(str(path))
    return {"path": str(path), "stats": array_stats(arr)}

def world_bbox(img: nib.Nifti1Image) -> tuple[np.ndarray, np.ndarray]:
    shape = np.array(img.shape[:3], dtype=float)
    corners = np.array([
        [0,0,0,1], [shape[0],0,0,1], [0,shape[1],0,1], [0,0,shape[2],1],
        [shape[0],shape[1],0,1], [shape[0],0,shape[2],1], [0,shape[1],shape[2],1], [shape[0],shape[1],shape[2],1],
    ], dtype=float)
    pts = (img.affine @ corners.T).T[:, :3]
    return pts.min(axis=0), pts.max(axis=0)

def bbox_overlap(a_min, a_max, b_min, b_max) -> dict[str, Any]:
    lo = np.maximum(a_min, b_min)
    hi = np.minimum(a_max, b_max)
    size = np.maximum(0, hi - lo)
    vol = float(np.prod(size))
    a_vol = float(np.prod(np.maximum(0, a_max - a_min)))
    b_vol = float(np.prod(np.maximum(0, b_max - b_min)))
    return {
        "overlap_size_mm": size.tolist(),
        "overlap_volume_mm3": vol,
        "frac_of_paxinos": vol / a_vol if a_vol else 0.0,
        "frac_of_candidate": vol / b_vol if b_vol else 0.0,
    }

def norm_slice(sl: np.ndarray) -> np.ndarray:
    arr = np.asarray(sl, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.float32)
    lo, hi = np.percentile(finite, [1, 99.5])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0, 1)

def make_axis_panel(name: str, arr: np.ndarray, is_label: bool = False) -> str:
    arr = np.asarray(arr)
    x,y,z = arr.shape
    slices = [("axis0 mid", arr[x//2,:,:]), ("axis1 mid", arr[:,y//2,:]), ("axis2 mid", arr[:,:,z//2])]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, (title, sl) in zip(axes, slices):
        img = (sl > 0).astype(float) if is_label else norm_slice(sl)
        ax.imshow(np.rot90(img), cmap="gray")
        ax.set_title(title)
        ax.axis("off")
    fig.suptitle(name)
    fig.tight_layout()
    out = OUT_DIR / f"axis_panel_{name}.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return str(out)

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "installed_atlas": str(installed_atlas_folder()),
        "file_existence": {k: {"path": str(p), "exists": p.exists(), "size": p.stat().st_size if p.exists() else None} for k,p in FILES.items()},
        "metadata": {},
        "tiff_summaries": {},
        "nifti_summaries": {},
        "world_bbox_overlap_against_paxinos": {},
        "preview_pngs": {},
        "interpretation": [],
    }
    if FILES["metadata_json"].exists():
        report["metadata"] = json.loads(FILES["metadata_json"].read_text(encoding="utf-8"))

    for key in ["installed_reference_tiff", "installed_annotation_tiff", "installed_hemispheres_tiff"]:
        p = FILES[key]
        if p.exists():
            report["tiff_summaries"][key] = tiff_summary(p)
            arr = tifffile.imread(str(p))
            report["preview_pngs"][key] = make_axis_panel(key, arr, is_label=("annotation" in key or "hemispheres" in key))

    for key in ["paxinos_raw_annotation", "sigma_anatomical", "neurorat_mri", "waxholm_mri"]:
        p = FILES[key]
        if p.exists():
            report["nifti_summaries"][key] = nifti_summary(p)
            img = nib.load(str(p))
            arr = np.asanyarray(img.dataobj)
            report["preview_pngs"][key] = make_axis_panel(key, arr, is_label=("paxinos" in key))

    if FILES["paxinos_raw_annotation"].exists():
        pax = nib.load(str(FILES["paxinos_raw_annotation"]))
        pmin, pmax = world_bbox(pax)
        report["world_bbox_overlap_against_paxinos"]["paxinos_bbox_min"] = pmin.tolist()
        report["world_bbox_overlap_against_paxinos"]["paxinos_bbox_max"] = pmax.tolist()
        for key in ["sigma_anatomical", "neurorat_mri", "waxholm_mri"]:
            p = FILES[key]
            if p.exists():
                img = nib.load(str(p))
                cmin, cmax = world_bbox(img)
                ov = bbox_overlap(pmin, pmax, cmin, cmax)
                ov.update({"candidate_bbox_min": cmin.tolist(), "candidate_bbox_max": cmax.tolist()})
                report["world_bbox_overlap_against_paxinos"][key] = ov

    ref_strategy = report.get("metadata", {}).get("reference_strategy")
    if ref_strategy == "provisional_label_edge_reference_generated_from_annotation_boundaries":
        report["interpretation"].append("Installed reference is expected to be a label-edge reference, not a true anatomical MRI/reference image.")
    overlaps = report["world_bbox_overlap_against_paxinos"]
    for key in ["neurorat_mri", "waxholm_mri"]:
        if key in overlaps and overlaps[key]["overlap_volume_mm3"] == 0:
            report["interpretation"].append(f"{key} has zero world-space overlap with Paxinos; direct affine resampling will produce empty/all-zero output.")
    if "sigma_anatomical" in overlaps and overlaps["sigma_anatomical"]["overlap_volume_mm3"] > 0:
        report["interpretation"].append("SIGMA has non-zero world-space overlap with Paxinos and is the first candidate worth testing as a display reference.")

    (OUT_DIR / "reference_orientation_diagnostic_report.json").write_text(json.dumps(as_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["Reference / orientation diagnostic", "="*72, f"Generated: {report['generated_at']}", "", "Interpretation:"]
    lines += [f"- {x}" for x in report["interpretation"]] or ["- none"]
    lines += ["", "Files:"]
    for k,v in report["file_existence"].items():
        lines.append(f"- {k}: exists={v['exists']} size={v['size']} path={v['path']}")
    lines += ["", "Overlaps:"]
    for k,v in report["world_bbox_overlap_against_paxinos"].items():
        if isinstance(v, dict) and "overlap_volume_mm3" in v:
            lines.append(f"- {k}: overlap_volume_mm3={v['overlap_volume_mm3']:.4f}, frac_of_paxinos={v['frac_of_paxinos']:.6f}")
    lines += ["", "Preview PNGs:"]
    for k,v in report["preview_pngs"].items():
        lines.append(f"- {k}: {v}")
    (OUT_DIR / "reference_orientation_diagnostic_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote:", OUT_DIR / "reference_orientation_diagnostic_report.txt")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
