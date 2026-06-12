from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
from nibabel.orientations import aff2axcodes
from nibabel.processing import resample_from_to
import numpy as np
import tifffile

from utils_paths import RAW_DIR, REPORTS_DIR

OUT_DIR = REPORTS_DIR / "sigma_reference_experiment"
PAXINOS = RAW_DIR / "Paxinos_Watson_Atlas.nii.gz"
SIGMA = RAW_DIR / "SIGMA_Anatomical_Brain_Atlas.nii"

def jsonable(x: Any) -> Any:
    if isinstance(x, dict): return {str(k): jsonable(v) for k,v in x.items()}
    if isinstance(x, (list,tuple)): return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray): return x.tolist()
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, Path): return str(x)
    return x

def stats(arr: np.ndarray) -> dict[str, Any]:
    a = np.asarray(arr)
    finite = a[np.isfinite(a)] if np.issubdtype(a.dtype, np.floating) else a.ravel()
    return {
        "shape": list(a.shape), "dtype": str(a.dtype),
        "min": float(finite.min()) if finite.size else None,
        "max": float(finite.max()) if finite.size else None,
        "mean": float(finite.mean()) if finite.size else None,
        "nonzero_voxels": int(np.count_nonzero(a)),
        "nonzero_fraction": float(np.count_nonzero(a)/a.size) if a.size else 0.0,
    }

def world_bbox(img: nib.Nifti1Image) -> tuple[np.ndarray, np.ndarray]:
    shape = np.array(img.shape[:3], dtype=float)
    corners = np.array([
        [0,0,0,1], [shape[0],0,0,1], [0,shape[1],0,1], [0,0,shape[2],1],
        [shape[0],shape[1],0,1], [shape[0],0,shape[2],1], [0,shape[1],shape[2],1], [shape[0],shape[1],shape[2],1],
    ], dtype=float)
    pts = (img.affine @ corners.T).T[:, :3]
    return pts.min(axis=0), pts.max(axis=0)

def overlap_report(pax: nib.Nifti1Image, cand: nib.Nifti1Image) -> dict[str, Any]:
    pmin,pmax = world_bbox(pax); cmin,cmax = world_bbox(cand)
    lo = np.maximum(pmin,cmin); hi = np.minimum(pmax,cmax); size = np.maximum(0, hi-lo)
    vol = float(np.prod(size))
    pvol = float(np.prod(np.maximum(0,pmax-pmin))); cvol = float(np.prod(np.maximum(0,cmax-cmin)))
    return {
        "paxinos_bbox_min": pmin.tolist(), "paxinos_bbox_max": pmax.tolist(),
        "candidate_bbox_min": cmin.tolist(), "candidate_bbox_max": cmax.tolist(),
        "overlap_size_mm": size.tolist(), "overlap_volume_mm3": vol,
        "frac_of_paxinos": vol/pvol if pvol else 0.0,
        "frac_of_candidate": vol/cvol if cvol else 0.0,
    }

def to_uint16(arr: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    a = np.asarray(arr, dtype=np.float32)
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return np.zeros(a.shape, dtype=np.uint16), {"reason": "no_finite_voxels"}
    lo, hi = np.percentile(finite, [1.0, 99.8])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros(a.shape, dtype=np.uint16), {"reason": "flat_image", "input_min": lo, "input_max": hi}
    out = np.clip((a-lo)/(hi-lo), 0, 1)
    out = np.round(out * 65535).astype(np.uint16)
    return out, {"p01": float(lo), "p998": float(hi), "output_min": int(out.min()), "output_max": int(out.max()), "output_mean": float(out.mean())}

def norm(sl: np.ndarray) -> np.ndarray:
    a = np.asarray(sl, dtype=np.float32)
    f = a[np.isfinite(a)]
    if f.size == 0: return np.zeros(a.shape, dtype=np.float32)
    lo,hi = np.percentile(f, [1,99.5])
    if hi <= lo: lo,hi = float(f.min()), float(f.max())
    if hi <= lo: return np.zeros(a.shape, dtype=np.float32)
    return np.clip((a-lo)/(hi-lo), 0, 1)

def axis_slices(arr: np.ndarray) -> list[tuple[str,np.ndarray]]:
    x,y,z = arr.shape
    return [("axis0 mid", arr[x//2,:,:]), ("axis1 mid", arr[:,y//2,:]), ("axis2 mid", arr[:,:,z//2])]

def save_axis_panel(name: str, arr: np.ndarray, label_mask: np.ndarray | None = None) -> str:
    nrows = 2 if label_mask is not None else 1
    fig, axes = plt.subplots(nrows, 3, figsize=(13, 4*nrows))
    if nrows == 1: axes = np.array([axes])
    for i,(title,sl) in enumerate(axis_slices(arr)):
        axes[0,i].imshow(np.rot90(norm(sl)), cmap='gray')
        axes[0,i].set_title(f"{name} {title}")
        axes[0,i].axis('off')
    if label_mask is not None:
        for i,(title,sl) in enumerate(axis_slices(label_mask)):
            axes[1,i].imshow(np.rot90(sl > 0), cmap='gray')
            axes[1,i].set_title(f"Paxinos mask {title}")
            axes[1,i].axis('off')
    fig.tight_layout()
    out = OUT_DIR / f"{name}.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return str(out)

def save_overlay_panel(name: str, ref: np.ndarray, ann: np.ndarray) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(13,4))
    for i,((title, rsl), (_, asl)) in enumerate(zip(axis_slices(ref), axis_slices(ann))):
        base = norm(rsl)
        mask = (asl > 0).astype(float)
        rgb = np.dstack([base, base, base])
        # Red transparent boundary-like overlay from annotation mask edge.
        edge = np.zeros(mask.shape, dtype=bool)
        edge[:-1,:] |= mask[:-1,:] != mask[1:,:]
        edge[1:,:] |= mask[:-1,:] != mask[1:,:]
        edge[:,:-1] |= mask[:,:-1] != mask[:,1:]
        edge[:,1:] |= mask[:,:-1] != mask[:,1:]
        rgb[edge,0] = 1.0; rgb[edge,1] *= 0.35; rgb[edge,2] *= 0.35
        axes[i].imshow(np.rot90(rgb))
        axes[i].set_title(title)
        axes[i].axis('off')
    fig.suptitle(name)
    fig.tight_layout()
    out = OUT_DIR / f"{name}.png"
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return str(out)

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"generated_at": datetime.now().isoformat(timespec='seconds'), "paths": {"paxinos": str(PAXINOS), "sigma": str(SIGMA)}, "passed": False, "warnings": []}
    if not PAXINOS.exists():
        raise FileNotFoundError(f"Missing Paxinos atlas: {PAXINOS}")
    if not SIGMA.exists():
        raise FileNotFoundError(f"Missing SIGMA anatomical atlas: {SIGMA}")
    pax = nib.load(str(PAXINOS)); sig = nib.load(str(SIGMA))
    pax_data = np.asanyarray(pax.dataobj)
    sig_data = np.asanyarray(sig.dataobj)
    report["paxinos"] = {"shape": list(pax.shape), "zooms": [float(x) for x in pax.header.get_zooms()[:3]], "orientation": "".join(aff2axcodes(pax.affine)), "affine": pax.affine.tolist(), "stats": stats(pax_data)}
    report["sigma"] = {"shape": list(sig.shape), "zooms": [float(x) for x in sig.header.get_zooms()[:3]], "orientation": "".join(aff2axcodes(sig.affine)), "affine": sig.affine.tolist(), "stats": stats(sig_data)}
    report["world_overlap"] = overlap_report(pax, sig)

    res = resample_from_to(sig, (pax.shape, pax.affine), order=1)
    res_data = np.asanyarray(res.dataobj)
    ref_u16, norm_info = to_uint16(res_data)
    report["resampled_sigma"] = {"stats_float": stats(res_data), "normalization": norm_info, "stats_uint16": stats(ref_u16)}

    out_nii = OUT_DIR / "candidate_sigma_reference.nii.gz"
    out_tiff = OUT_DIR / "candidate_sigma_reference.tiff"
    img = nib.Nifti1Image(ref_u16, pax.affine, header=pax.header.copy())
    img.set_data_dtype(np.uint16)
    nib.save(img, str(out_nii))
    tifffile.imwrite(str(out_tiff), ref_u16, photometric="minisblack")
    report["outputs"] = {"candidate_sigma_reference_nii": str(out_nii), "candidate_sigma_reference_tiff": str(out_tiff)}
    report["preview_pngs"] = {
        "axis_panels_sigma_native": save_axis_panel("axis_panels_sigma_native", sig_data),
        "axis_panels_sigma_resampled": save_axis_panel("axis_panels_sigma_resampled", ref_u16, pax_data),
        "candidate_sigma_reference_overlay": save_overlay_panel("candidate_sigma_reference_overlay", ref_u16, pax_data),
    }

    nz = report["resampled_sigma"]["stats_uint16"]["nonzero_fraction"]
    report["passed"] = bool(tuple(ref_u16.shape) == tuple(pax.shape) and nz > 0.001 and int(ref_u16.max()) > int(ref_u16.min()))
    if report["world_overlap"]["frac_of_paxinos"] < 0.1:
        report["warnings"].append("SIGMA/Paxinos affine overlap is low; do not install this as reference without visual QC.")
    if not report["passed"]:
        report["warnings"].append("Candidate reference failed nonzero/intensity sanity checks.")
    report["interpretation"] = [
        "This experiment does not modify the installed atlas.",
        "SIGMA is tested because NeuroRat and Waxholm direct affine resampling showed zero overlap in the prior diagnostic.",
        "Use PNG overlays for visual QC before any future test-atlas installation.",
    ]
    (OUT_DIR / "sigma_reference_experiment_report.json").write_text(json.dumps(jsonable(report), indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ["SIGMA reference experiment", "="*72, f"Generated: {report['generated_at']}", f"PASSED: {report['passed']}", "", "Overlap:"]
    for k in ["overlap_volume_mm3", "frac_of_paxinos", "frac_of_candidate", "overlap_size_mm"]:
        lines.append(f"- {k}: {report['world_overlap'][k]}")
    lines += ["", "Resampled SIGMA uint16 stats:"]
    for k,v in report["resampled_sigma"]["stats_uint16"].items(): lines.append(f"- {k}: {v}")
    lines += ["", "Warnings:"] + ([f"- {w}" for w in report["warnings"]] or ["- none"])
    lines += ["", "Outputs:"]
    for k,v in report["outputs"].items(): lines.append(f"- {k}: {v}")
    lines += ["", "Preview PNGs:"]
    for k,v in report["preview_pngs"].items(): lines.append(f"- {k}: {v}")
    lines += ["", "Interpretation:"] + [f"- {x}" for x in report["interpretation"]]
    (OUT_DIR / "sigma_reference_experiment_report.txt").write_text("\n".join(lines), encoding='utf-8')
    print("Wrote:", OUT_DIR / "sigma_reference_experiment_report.txt")
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
