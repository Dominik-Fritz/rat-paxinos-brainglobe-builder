from __future__ import annotations

import json
import math
import traceback
from pathlib import Path
from datetime import datetime

import numpy as np

try:
    import nibabel as nib
except Exception as e:
    raise SystemExit(f"Missing dependency nibabel: {e}")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as e:
    raise SystemExit(f"Missing dependency matplotlib: {e}")

try:
    import scipy.ndimage as ndi
    SCIPY_AVAILABLE = True
except Exception:
    ndi = None
    SCIPY_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "bluebrainheadmodels"
OFFICIAL = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
REPORT_DIR = PROJECT_ROOT / "reports" / "v32_5_reference_registration_prep"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_ANNOTATION = OFFICIAL / "annotation.nii.gz"
TARGET_REFERENCE = OFFICIAL / "reference.nii.gz"

CANDIDATES = [
    {
        "name": "waxholm_mri",
        "path": RAW_DIR / "Waxholm_Atlas_MRI.nii.gz",
        "expected": "highest-priority anatomical candidate; huge, oblique/PIR in previous scout",
    },
    {
        "name": "sigma_anatomical",
        "path": RAW_DIR / "SIGMA_Anatomical_Brain_Atlas.nii",
        "expected": "previous direct-affine result was visibly misaligned; included for comparison",
    },
    {
        "name": "neurorat_mri",
        "path": RAW_DIR / "NeuroRat_MRI.nii.gz",
        "expected": "previous direct-affine overlap was poor; included for comparison",
    },
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def axcodes(img) -> str:
    try:
        return "".join(nib.aff2axcodes(img.affine))
    except Exception:
        return "unknown"


def stats_array(arr: np.ndarray) -> dict:
    arr = np.asarray(arr)
    finite = np.isfinite(arr)
    if not finite.any():
        return {"finite_fraction": 0.0, "min": None, "max": None, "nonzero_fraction": 0.0}
    vals = arr[finite]
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": float(np.nanmin(vals)),
        "max": float(np.nanmax(vals)),
        "mean": float(np.nanmean(vals)),
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size),
        "finite_fraction": float(finite.mean()),
    }


def image_bbox_world(img, mask: np.ndarray | None = None, sample_step: int = 1) -> dict:
    shape = img.shape[:3]
    if mask is None:
        coords = np.array([
            [0, 0, 0],
            [shape[0] - 1, 0, 0],
            [0, shape[1] - 1, 0],
            [0, 0, shape[2] - 1],
            [shape[0] - 1, shape[1] - 1, 0],
            [shape[0] - 1, 0, shape[2] - 1],
            [0, shape[1] - 1, shape[2] - 1],
            [shape[0] - 1, shape[1] - 1, shape[2] - 1],
        ], dtype=float)
    else:
        inds = np.argwhere(mask)
        if inds.size == 0:
            return {"valid": False, "reason": "empty mask"}
        lo = inds.min(axis=0) * sample_step
        hi = inds.max(axis=0) * sample_step
        coords = np.array([
            [lo[0], lo[1], lo[2]],
            [hi[0], lo[1], lo[2]],
            [lo[0], hi[1], lo[2]],
            [lo[0], lo[1], hi[2]],
            [hi[0], hi[1], lo[2]],
            [hi[0], lo[1], hi[2]],
            [lo[0], hi[1], hi[2]],
            [hi[0], hi[1], hi[2]],
        ], dtype=float)
    world = nib.affines.apply_affine(img.affine, coords)
    return {
        "valid": True,
        "min": [float(x) for x in world.min(axis=0)],
        "max": [float(x) for x in world.max(axis=0)],
        "size": [float(x) for x in (world.max(axis=0) - world.min(axis=0))],
    }


def overlap_bbox(a: dict, b: dict) -> dict:
    if not a.get("valid") or not b.get("valid"):
        return {"valid": False}
    amin = np.array(a["min"], dtype=float)
    amax = np.array(a["max"], dtype=float)
    bmin = np.array(b["min"], dtype=float)
    bmax = np.array(b["max"], dtype=float)
    lo = np.maximum(amin, bmin)
    hi = np.minimum(amax, bmax)
    size = np.maximum(hi - lo, 0)
    vol = float(np.prod(size))
    avol = float(np.prod(np.maximum(amax - amin, 0)))
    bvol = float(np.prod(np.maximum(bmax - bmin, 0)))
    return {
        "valid": True,
        "overlap_size": [float(x) for x in size],
        "overlap_volume": vol,
        "frac_a": vol / avol if avol else 0.0,
        "frac_b": vol / bvol if bvol else 0.0,
    }


def robust_norm_uint16(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    out = np.zeros(arr.shape, dtype=np.uint16)
    if not finite.any():
        return out
    vals = arr[finite]
    if vals.size > 2_000_000:
        rng = np.random.default_rng(42)
        vals = rng.choice(vals, size=2_000_000, replace=False)
    lo, hi = np.percentile(vals, [1, 99.5])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    if hi <= lo:
        return out
    scaled = (arr - lo) / (hi - lo)
    scaled = np.clip(scaled, 0, 1)
    out[:] = (scaled * 65535).astype(np.uint16)
    return out


def make_mask_from_volume(arr: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(arr)
    finite = np.isfinite(arr)
    if not finite.any():
        return np.zeros(arr.shape, dtype=bool)
    vals = arr[finite]
    # anatomical MRIs often have large zero background; threshold above very dark background
    try:
        p = np.percentile(vals, 10)
        p2 = np.percentile(vals, 2)
        threshold = max(p2, p * 0.25)
    except Exception:
        threshold = 0
    mask = finite & (arr > threshold)
    # if mask is too large/small, fall back to nonzero
    frac = mask.mean()
    if frac < 0.01 or frac > 0.95:
        mask = finite & (arr != 0)
    return mask


def bbox_voxel(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    pts = np.argwhere(mask)
    if pts.size == 0:
        return None
    return pts.min(axis=0).astype(float), pts.max(axis=0).astype(float)


def downsample_dataobj(img, max_voxels: int = 18_000_000):
    shape = np.array(img.shape[:3], dtype=int)
    factor = 1
    while int(np.prod(np.ceil(shape / factor))) > max_voxels:
        factor *= 2
    sl = tuple(slice(None, None, factor) for _ in range(3))
    data = np.asanyarray(img.dataobj[sl], dtype=np.float32)
    return data, factor


def save_axis_panel(vol: np.ndarray, mask: np.ndarray | None, title: str, out_path: Path):
    vol = np.asarray(vol)
    fig, axes = plt.subplots(3, 2 if mask is not None else 1, figsize=(10 if mask is not None else 5, 12))
    if mask is None:
        axes = np.array([[ax] for ax in axes])
    for axis in range(3):
        idx = vol.shape[axis] // 2
        img = np.take(vol, idx, axis=axis)
        axes[axis, 0].imshow(np.rot90(img), cmap="gray")
        axes[axis, 0].set_title(f"{title} axis{axis}_mid")
        axes[axis, 0].axis("off")
        if mask is not None:
            m = np.take(mask, idx, axis=axis)
            axes[axis, 1].imshow(np.rot90(img), cmap="gray")
            axes[axis, 1].imshow(np.rot90(m), alpha=0.35)
            axes[axis, 1].set_title(f"{title} + Paxinos mask axis{axis}_mid")
            axes[axis, 1].axis("off")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def direct_affine_lowres(mov_img, target_img, target_mask_low: np.ndarray, low_factor: int, name: str):
    if not SCIPY_AVAILABLE:
        return {"attempted": False, "error": "scipy not available"}
    # Build low-res target grid in target voxel coordinates, convert to world, then to moving voxels.
    target_shape = np.array(target_img.shape[:3], dtype=int)
    low_shape = np.ceil(target_shape / low_factor).astype(int)
    grids = np.meshgrid(
        np.arange(low_shape[0], dtype=np.float32) * low_factor,
        np.arange(low_shape[1], dtype=np.float32) * low_factor,
        np.arange(low_shape[2], dtype=np.float32) * low_factor,
        indexing="ij",
    )
    coords_target = np.stack([g.ravel() for g in grids], axis=1)
    world = nib.affines.apply_affine(target_img.affine, coords_target)
    inv_mov = np.linalg.inv(mov_img.affine)
    coords_mov = nib.affines.apply_affine(inv_mov, world).T
    # Load moving downsample? direct affine must sample native full data; use dataobj as array proxy converted to float32.
    mov_data = np.asanyarray(mov_img.dataobj, dtype=np.float32)
    sampled = ndi.map_coordinates(mov_data, coords_mov, order=1, mode="constant", cval=0.0)
    sampled = sampled.reshape(tuple(low_shape))
    norm = robust_norm_uint16(sampled)
    out_png = REPORT_DIR / f"{name}_direct_affine_lowres_overlay.png"
    save_axis_panel(norm, target_mask_low, f"{name} direct_affine_lowres", out_png)
    return {
        "attempted": True,
        "low_shape": [int(x) for x in low_shape],
        "stats": stats_array(norm),
        "preview": str(out_png),
    }


def bbox_fit_lowres(mov_img, target_img, target_mask_full: np.ndarray, low_factor: int, name: str):
    if not SCIPY_AVAILABLE:
        return {"attempted": False, "error": "scipy not available"}
    mov_down, mov_factor = downsample_dataobj(mov_img)
    mov_mask = make_mask_from_volume(mov_down, name)
    mov_bbox = bbox_voxel(mov_mask)
    fixed_bbox = bbox_voxel(target_mask_full)
    if mov_bbox is None or fixed_bbox is None:
        return {"attempted": False, "error": "empty moving or fixed bbox"}

    mov_min, mov_max = mov_bbox
    fixed_min, fixed_max = fixed_bbox
    target_shape = np.array(target_img.shape[:3], dtype=int)
    low_shape = np.ceil(target_shape / low_factor).astype(int)

    # map target full voxel coordinate -> moving downsampled voxel coordinate via bbox scale
    fixed_extent = np.maximum(fixed_max - fixed_min, 1.0)
    mov_extent = np.maximum(mov_max - mov_min, 1.0)

    grids = np.meshgrid(
        np.arange(low_shape[0], dtype=np.float32) * low_factor,
        np.arange(low_shape[1], dtype=np.float32) * low_factor,
        np.arange(low_shape[2], dtype=np.float32) * low_factor,
        indexing="ij",
    )
    coords_target = np.stack(grids, axis=0)
    coords_mov = np.empty_like(coords_target)
    for ax in range(3):
        coords_mov[ax] = mov_min[ax] + (coords_target[ax] - fixed_min[ax]) * (mov_extent[ax] / fixed_extent[ax])
    sampled = ndi.map_coordinates(mov_down, coords_mov.reshape(3, -1), order=1, mode="constant", cval=0.0)
    sampled = sampled.reshape(tuple(low_shape))
    norm = robust_norm_uint16(sampled)
    target_mask_low = target_mask_full[::low_factor, ::low_factor, ::low_factor]
    out_png = REPORT_DIR / f"{name}_bbox_fit_lowres_overlay.png"
    save_axis_panel(norm, target_mask_low, f"{name} bbox_fit_lowres", out_png)
    return {
        "attempted": True,
        "moving_downsample_factor": int(mov_factor),
        "low_factor": int(low_factor),
        "low_shape": [int(x) for x in low_shape],
        "moving_bbox_downsampled_voxels": {"min": [float(x) for x in mov_min], "max": [float(x) for x in mov_max]},
        "fixed_bbox_voxels": {"min": [float(x) for x in fixed_min], "max": [float(x) for x in fixed_max]},
        "stats": stats_array(norm),
        "preview": str(out_png),
    }


def main() -> int:
    result = {
        "generated_at": now(),
        "project_root": str(PROJECT_ROOT),
        "report_dir": str(REPORT_DIR),
        "target_annotation": str(TARGET_ANNOTATION),
        "candidates": [],
        "passed": False,
        "notes": [
            "Preview-only registration prep. No atlas files are modified.",
            "Direct affine tests NIfTI world-coordinate alignment.",
            "BBox fit is only a coarse diagnostic, not a valid final registration.",
        ],
    }

    if not TARGET_ANNOTATION.exists():
        result["error"] = f"Missing target annotation: {TARGET_ANNOTATION}"
        write_reports(result)
        return 2

    target_img = nib.load(str(TARGET_ANNOTATION))
    target_ann = np.asanyarray(target_img.dataobj)
    target_mask = target_ann > 0
    target_mask_stats = stats_array(target_mask.astype(np.uint8))
    low_factor = 4
    target_mask_low = target_mask[::low_factor, ::low_factor, ::low_factor]

    result["target"] = {
        "shape": list(target_img.shape[:3]),
        "orientation": axcodes(target_img),
        "voxel_size": [float(x) for x in target_img.header.get_zooms()[:3]],
        "mask_stats": target_mask_stats,
        "world_bbox_full_image": image_bbox_world(target_img),
        "world_bbox_mask": image_bbox_world(target_img, target_mask),
    }

    for cand in CANDIDATES:
        entry = {"name": cand["name"], "path": str(cand["path"]), "expected": cand["expected"], "exists": cand["path"].exists()}
        try:
            if not cand["path"].exists():
                entry["skipped"] = True
                entry["reason"] = "file not found"
                result["candidates"].append(entry)
                continue
            img = nib.load(str(cand["path"]))
            entry.update({
                "shape": list(img.shape[:3]),
                "dtype": str(img.get_data_dtype()),
                "orientation": axcodes(img),
                "voxel_size": [float(x) for x in img.header.get_zooms()[:3]],
                "affine": np.asarray(img.affine).round(6).tolist(),
                "world_bbox_full_image": image_bbox_world(img),
                "world_bbox_overlap_full_vs_target_mask": None,
            })
            entry["world_bbox_overlap_full_vs_target_mask"] = overlap_bbox(entry["world_bbox_full_image"], result["target"]["world_bbox_mask"])

            # native preview using memory-controlled downsampling
            mov_down, mov_factor = downsample_dataobj(img)
            mov_norm = robust_norm_uint16(mov_down)
            native_png = REPORT_DIR / f"{cand['name']}_native_downsampled_axes.png"
            save_axis_panel(mov_norm, None, f"{cand['name']} native_downsampled x{mov_factor}", native_png)
            entry["native_preview"] = str(native_png)
            entry["moving_downsample_factor_for_native"] = int(mov_factor)
            entry["native_downsampled_stats"] = stats_array(mov_norm)

            # direct affine can be memory-heavy for Waxholm. Try, but catch errors.
            try:
                entry["direct_affine_lowres"] = direct_affine_lowres(img, target_img, target_mask_low, low_factor, cand["name"])
            except Exception as e:
                entry["direct_affine_lowres"] = {"attempted": True, "error": str(e), "traceback": traceback.format_exc(limit=5)}

            # bbox fit lowres
            try:
                entry["bbox_fit_lowres"] = bbox_fit_lowres(img, target_img, target_mask, low_factor, cand["name"])
            except Exception as e:
                entry["bbox_fit_lowres"] = {"attempted": True, "error": str(e), "traceback": traceback.format_exc(limit=5)}
        except Exception as e:
            entry["error"] = str(e)
            entry["traceback"] = traceback.format_exc(limit=8)
        result["candidates"].append(entry)

    result["passed"] = True
    write_reports(result)
    return 0


def write_reports(result: dict):
    json_path = REPORT_DIR / "v32_5_reference_registration_prep.json"
    txt_path = REPORT_DIR / "v32_5_reference_registration_prep_summary.txt"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines = []
    lines.append("V32.5 Reference Registration Prep")
    lines.append("=" * 72)
    lines.append(f"Generated: {result.get('generated_at')}")
    lines.append(f"Project root: {result.get('project_root')}")
    lines.append(f"Report dir: {result.get('report_dir')}")
    lines.append(f"PASSED: {result.get('passed')}")
    lines.append("")
    if "error" in result:
        lines.append(f"ERROR: {result['error']}")
    target = result.get("target") or {}
    if target:
        lines.append("Target Paxinos:")
        lines.append(f"- shape: {target.get('shape')}")
        lines.append(f"- orientation: {target.get('orientation')}")
        lines.append(f"- voxel_size: {target.get('voxel_size')}")
        lines.append(f"- mask nonzero fraction: {target.get('mask_stats', {}).get('nonzero_fraction')}")
        lines.append(f"- mask world bbox: {target.get('world_bbox_mask')}")
        lines.append("")

    lines.append("Candidate results:")
    for c in result.get("candidates", []):
        lines.append(f"- {c.get('name')}: exists={c.get('exists')} path={c.get('path')}")
        if c.get("shape"):
            lines.append(f"  shape={c.get('shape')} orientation={c.get('orientation')} voxel_size={c.get('voxel_size')} dtype={c.get('dtype')}")
            ov = c.get("world_bbox_overlap_full_vs_target_mask")
            lines.append(f"  full-world overlap vs target mask={ov}")
        if c.get("native_preview"):
            lines.append(f"  native preview: {c.get('native_preview')}")
        da = c.get("direct_affine_lowres")
        if da:
            lines.append(f"  direct affine: attempted={da.get('attempted')} error={da.get('error')} preview={da.get('preview')} stats={da.get('stats')}")
        bf = c.get("bbox_fit_lowres")
        if bf:
            lines.append(f"  bbox fit: attempted={bf.get('attempted')} error={bf.get('error')} preview={bf.get('preview')} stats={bf.get('stats')}")
        if c.get("error"):
            lines.append(f"  ERROR: {c.get('error')}")
        lines.append("")

    lines.append("Interpretation:")
    lines.append("- Direct affine alignment is acceptable only if the overlay looks anatomically plausible without manual wishful thinking.")
    lines.append("- BBox-fit previews are only a registration-start diagnostic. They are not final atlas data.")
    lines.append("- If Waxholm bbox-fit is plausible, the next step is a real registration/test atlas. If not, use a proper registration backend or find a closer Paxinos-compatible reference.")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    print("Wrote:")
    print(txt_path)
    print(json_path)


if __name__ == "__main__":
    raise SystemExit(main())
