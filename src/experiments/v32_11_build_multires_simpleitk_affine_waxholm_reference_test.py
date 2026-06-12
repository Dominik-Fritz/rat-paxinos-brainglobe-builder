#!/usr/bin/env python3
"""
V32.11 Multi-resolution SimpleITK Affine Waxholm Reference Test Atlas

Builds separate diagnostic BrainGlobe test atlases using the SimpleITK affine
proof-of-concept path from V32.9. This script does not modify the stable
paxinos_watson_rat_40um atlas.

Important limitation:
- The default V32.11 reference is generated from a higher-resolution/multi-resolution affine
  registration proof and upsampled into the target Paxinos grid. This is a
  diagnostic ABBA test atlas, not a final anatomical registration.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _json_default(obj: Any) -> Any:
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
    except Exception:
        pass
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "data").exists() and (parent / "src").exists():
            return parent
    cwd = Path.cwd().resolve()
    if (cwd / "data").exists():
        return cwd
    return Path(r"G:\rat-paxinos-brainglobe-builder")


def module_status(name: str) -> Dict[str, Any]:
    spec = importlib.util.find_spec(name)
    out: Dict[str, Any] = {"available": spec is not None, "version": None, "error": None}
    if spec is None:
        return out
    try:
        mod = importlib.import_module(name)
        out["version"] = getattr(mod, "__version__", None)
    except Exception as e:
        out["available"] = False
        out["error"] = repr(e)
    return out


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_nifti(path: Path):
    import nibabel as nib
    img = nib.load(str(path))
    data = img.get_fdata(dtype="float32")
    return data, img


def save_nifti_uint16(path: Path, arr, reference_img) -> None:
    import nibabel as nib
    import numpy as np
    header = reference_img.header.copy()
    header.set_data_dtype(np.uint16)
    img = nib.Nifti1Image(arr.astype(np.uint16), reference_img.affine, header=header)
    nib.save(img, str(path))


def save_tiff(path: Path, arr) -> None:
    try:
        import tifffile
    except Exception as e:
        raise RuntimeError("tifffile is required to write BrainGlobe TIFF files. Install it into the project venv.") from e
    tifffile.imwrite(str(path), arr.astype(arr.dtype), bigtiff=True)


def lowres_by_stride(arr, factor: int):
    slices = tuple(slice(None, None, factor) for _ in range(arr.ndim))
    return arr[slices]


def stats(arr) -> Dict[str, Any]:
    import numpy as np
    finite = np.isfinite(arr)
    if finite.any():
        vals = arr[finite]
        mn = float(vals.min())
        mx = float(vals.max())
        mean = float(vals.mean())
    else:
        mn = mx = mean = float("nan")
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": mn,
        "max": mx,
        "mean": mean,
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size) if arr.size else 0.0,
        "finite_fraction": float(finite.mean()) if arr.size else 0.0,
    }


def bbox(mask) -> Optional[Tuple[List[int], List[int]]]:
    import numpy as np
    coords = np.argwhere(mask > 0)
    if coords.size == 0:
        return None
    mn = coords.min(axis=0).astype(int).tolist()
    mx = coords.max(axis=0).astype(int).tolist()
    return mn, mx


def crop_to_bbox(arr, mn: Sequence[int], mx: Sequence[int]):
    sl = tuple(slice(int(a), int(b) + 1) for a, b in zip(mn, mx))
    return arr[sl]


def resize_to_shape(arr, target_shape: Sequence[int], order: int):
    import numpy as np
    from scipy import ndimage
    target_shape = tuple(int(x) for x in target_shape)
    if any(s <= 0 for s in arr.shape) or any(s <= 0 for s in target_shape):
        return np.zeros(target_shape, dtype=arr.dtype)
    zoom_factors = [t / s for t, s in zip(target_shape, arr.shape)]
    out = ndimage.zoom(arr, zoom_factors, order=order)
    fixed = np.zeros(target_shape, dtype=out.dtype)
    src_slices = []
    dst_slices = []
    for ax, target in enumerate(target_shape):
        n = min(out.shape[ax], target)
        src_slices.append(slice(0, n))
        dst_slices.append(slice(0, n))
    fixed[tuple(dst_slices)] = out[tuple(src_slices)]
    return fixed


def place_crop(crop, full_shape: Sequence[int], mn: Sequence[int]):
    import numpy as np
    out = np.zeros(tuple(int(x) for x in full_shape), dtype=crop.dtype)
    dst_slices = []
    src_slices = []
    for ax, start in enumerate(mn):
        start = int(start)
        end = min(start + crop.shape[ax], out.shape[ax])
        n = max(0, end - start)
        dst_slices.append(slice(start, end))
        src_slices.append(slice(0, n))
    if all(s.stop > s.start for s in dst_slices):
        out[tuple(dst_slices)] = crop[tuple(src_slices)]
    return out


def translate_clip(arr, shift: Sequence[int]):
    import numpy as np
    out = np.zeros_like(arr)
    src_slices = []
    dst_slices = []
    for ax, sh in enumerate(shift):
        sh = int(sh)
        size = arr.shape[ax]
        if sh >= 0:
            src_start = 0
            dst_start = sh
            n = size - sh
        else:
            src_start = -sh
            dst_start = 0
            n = size + sh
        if n <= 0:
            return out
        src_slices.append(slice(src_start, src_start + n))
        dst_slices.append(slice(dst_start, dst_start + n))
    out[tuple(dst_slices)] = arr[tuple(src_slices)]
    return out


def transform_candidate_to_fixed_space(
    moving_mask,
    moving_mri,
    fixed_mask,
    perm: Sequence[int],
    flips: Sequence[bool],
    shift: Sequence[int],
):
    import numpy as np
    fixed_shape = fixed_mask.shape
    fixed_bbox = bbox(fixed_mask)
    if fixed_bbox is None:
        raise RuntimeError("Fixed mask is empty; cannot bbox-fit.")
    fmn, fmx = fixed_bbox
    target_crop_shape = tuple(int(b - a + 1) for a, b in zip(fmn, fmx))

    mask_t = np.transpose(moving_mask, perm)
    mri_t = np.transpose(moving_mri, perm)
    for ax, do_flip in enumerate(flips):
        if do_flip:
            mask_t = np.flip(mask_t, axis=ax)
            mri_t = np.flip(mri_t, axis=ax)

    moving_bbox = bbox(mask_t)
    if moving_bbox is None:
        raise RuntimeError("Moving mask is empty after candidate orientation.")
    mmn, mmx = moving_bbox
    mask_crop = crop_to_bbox(mask_t, mmn, mmx)
    mri_crop = crop_to_bbox(mri_t, mmn, mmx)

    mask_fit_crop = resize_to_shape(mask_crop.astype("uint8"), target_crop_shape, order=0).astype(bool)
    mri_fit_crop = resize_to_shape(mri_crop.astype("float32"), target_crop_shape, order=1).astype("float32")

    mask_fit = place_crop(mask_fit_crop.astype("uint8"), fixed_shape, fmn).astype(bool)
    mri_fit = place_crop(mri_fit_crop, fixed_shape, fmn)

    mask_shift = translate_clip(mask_fit.astype("uint8"), shift).astype(bool)
    mri_shift = translate_clip(mri_fit.astype("float32"), shift)

    return {
        "mask_initial": mask_fit,
        "mri_initial": mri_fit,
        "mask_shifted": mask_shift,
        "mri_shifted": mri_shift,
        "fixed_bbox": {"min": fmn, "max": fmx},
        "moving_bbox_oriented": {"min": mmn, "max": mmx},
        "target_crop_shape": list(target_crop_shape),
    }


def metrics(fixed_mask, moving_mask) -> Dict[str, Any]:
    import numpy as np
    fixed = fixed_mask.astype(bool)
    moving = moving_mask.astype(bool)
    inter = int(np.logical_and(fixed, moving).sum())
    fsum = int(fixed.sum())
    msum = int(moving.sum())
    union = int(np.logical_or(fixed, moving).sum())
    return {
        "dice": float((2 * inter) / (fsum + msum)) if (fsum + msum) else 0.0,
        "jaccard": float(inter / union) if union else 0.0,
        "coverage_fixed": float(inter / fsum) if fsum else 0.0,
        "extra_fraction": float(max(msum - inter, 0) / msum) if msum else 0.0,
        "intersection_voxels": inter,
        "fixed_voxels": fsum,
        "moving_voxels": msum,
    }


def normalize_to_uint16(arr):
    import numpy as np
    a = arr.astype("float32")
    finite = np.isfinite(a)
    out = np.zeros(a.shape, dtype=np.uint16)
    if not finite.any():
        return out
    vals = a[finite]
    # Ignore pure zero background for contrast when possible.
    nonzero = vals[vals > 0]
    vals2 = nonzero if nonzero.size > 1000 else vals
    lo, hi = np.percentile(vals2, [0.5, 99.8])
    if hi <= lo:
        lo, hi = float(vals2.min()), float(vals2.max())
    if hi <= lo:
        return out
    scaled = (a - lo) / (hi - lo)
    scaled = np.clip(scaled, 0, 1)
    out = (scaled * 65535.0).round().astype(np.uint16)
    out[~finite] = 0
    return out


def normalize_slice(slc):
    import numpy as np
    arr = slc.astype("float32")
    finite = np.isfinite(arr)
    if not finite.any():
        return np.zeros_like(arr, dtype="float32")
    vals = arr[finite]
    lo, hi = np.percentile(vals, [1, 99.5])
    if hi <= lo:
        hi = vals.max()
        lo = vals.min()
    if hi <= lo:
        return np.zeros_like(arr, dtype="float32")
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0, 1)


def mask_border(mask):
    import numpy as np
    from scipy import ndimage
    m = mask.astype(bool)
    if not m.any():
        return m
    er = ndimage.binary_erosion(m, iterations=1)
    return np.logical_xor(m, er)


def take_slice(arr, axis: int, index: Optional[int] = None):
    if index is None:
        index = arr.shape[axis] // 2
    if axis == 0:
        return arr[index, :, :]
    if axis == 1:
        return arr[:, index, :]
    if axis == 2:
        return arr[:, :, index]
    raise ValueError(axis)


def preview_panel(path: Path, fixed_mask, reference, affine_mask, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fixed_border = mask_border(fixed_mask)
    affine_border = mask_border(affine_mask)
    fig, axes = plt.subplots(3, 3, figsize=(15, 13))
    fig.suptitle(title, fontsize=13)
    labels = [("axis0/AP-coronal mid", 0), ("axis1/SI-horizontal mid", 1), ("axis2/LR-sagittal mid", 2)]
    for row, (lab, axis) in enumerate(labels):
        ax = axes[row, 0]
        ax.imshow(normalize_slice(take_slice(reference, axis)).T, cmap="gray", origin="lower")
        ax.set_title(f"affine Waxholm reference\n{lab}", fontsize=9)
        ax.axis("off")

        ax = axes[row, 1]
        ax.imshow(normalize_slice(take_slice(reference, axis)).T, cmap="gray", origin="lower")
        ax.contour(take_slice(fixed_border, axis).T, levels=[0.5], colors=["yellow"], linewidths=0.8)
        ax.contour(take_slice(affine_border, axis).T, levels=[0.5], colors=["cyan"], linewidths=0.7)
        ax.set_title("reference + borders\nyellow=Paxinos, cyan=Waxholm", fontsize=9)
        ax.axis("off")

        ax = axes[row, 2]
        sl = normalize_slice(take_slice(reference, axis))
        ax.imshow(sl.T, cmap="gray", origin="lower")
        mask = take_slice(fixed_mask, axis).T
        ax.imshow(np.ma.masked_where(mask == 0, mask), cmap="Wistia", alpha=0.45, origin="lower")
        ax.set_title("reference + Paxinos mask", fontsize=9)
        ax.axis("off")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=140)
    plt.close(fig)


def run_simpleitk_affine(fixed_mask, moving_mask, moving_mri) -> Tuple[Dict[str, Any], Any, Any]:
    import numpy as np
    import SimpleITK as sitk

    fixed_u8 = fixed_mask.astype("uint8")
    moving_u8 = moving_mask.astype("uint8")

    fixed_img_u8 = sitk.GetImageFromArray(fixed_u8.astype(np.uint8))
    moving_img_u8 = sitk.GetImageFromArray(moving_u8.astype(np.uint8))
    moving_mri_img = sitk.GetImageFromArray(moving_mri.astype(np.float32))

    fixed_dm = sitk.SignedMaurerDistanceMap(fixed_img_u8, insideIsPositive=True, squaredDistance=False, useImageSpacing=False)
    moving_dm = sitk.SignedMaurerDistanceMap(moving_img_u8, insideIsPositive=True, squaredDistance=False, useImageSpacing=False)
    fixed_dm = sitk.Cast(fixed_dm, sitk.sitkFloat32)
    moving_dm = sitk.Cast(moving_dm, sitk.sitkFloat32)

    def execute_registration(transform, iterations: int, learning_rate: float):
        reg = sitk.ImageRegistrationMethod()
        reg.SetMetricAsMeanSquares()
        reg.SetInterpolator(sitk.sitkLinear)
        reg.SetOptimizerAsRegularStepGradientDescent(
            learningRate=learning_rate,
            minStep=0.005,
            numberOfIterations=iterations,
            relaxationFactor=0.6,
            gradientMagnitudeTolerance=1e-8,
        )
        try:
            reg.SetOptimizerScalesFromPhysicalShift()
        except Exception:
            pass
        reg.SetShrinkFactorsPerLevel([2, 1])
        reg.SetSmoothingSigmasPerLevel([1, 0])
        reg.SmoothingSigmasAreSpecifiedInPhysicalUnitsOff()
        reg.SetInitialTransform(transform, inPlace=False)
        tx = reg.Execute(fixed_dm, moving_dm)
        return tx, float(reg.GetMetricValue()), int(reg.GetOptimizerIteration()), reg.GetOptimizerStopConditionDescription()

    rigid0 = sitk.CenteredTransformInitializer(
        fixed_dm, moving_dm, sitk.Euler3DTransform(), sitk.CenteredTransformInitializerFilter.GEOMETRY
    )
    rigid_tx, rigid_metric, rigid_iter, rigid_stop = execute_registration(rigid0, 80, 1.0)

    affine0 = sitk.CenteredTransformInitializer(
        fixed_dm, moving_dm, sitk.AffineTransform(3), sitk.CenteredTransformInitializerFilter.GEOMETRY
    )
    affine_tx, affine_metric, affine_iter, affine_stop = execute_registration(affine0, 120, 0.5)

    fixed_ref = fixed_img_u8
    moving_mask_rigid = sitk.Resample(moving_img_u8, fixed_ref, rigid_tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    moving_mask_affine = sitk.Resample(moving_img_u8, fixed_ref, affine_tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    moving_mri_affine = sitk.Resample(moving_mri_img, fixed_ref, affine_tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)

    rigid_arr = sitk.GetArrayFromImage(moving_mask_rigid).astype(bool)
    affine_arr = sitk.GetArrayFromImage(moving_mask_affine).astype(bool)
    affine_mri = sitk.GetArrayFromImage(moving_mri_affine).astype("float32")

    info = {
        "success": True,
        "rigid": {
            "metric": rigid_metric,
            "iterations": rigid_iter,
            "stop": rigid_stop,
            "parameters": list(map(float, rigid_tx.GetParameters())),
            "fixed_parameters": list(map(float, rigid_tx.GetFixedParameters())),
            "metrics": metrics(fixed_mask, rigid_arr),
        },
        "affine": {
            "metric": affine_metric,
            "iterations": affine_iter,
            "stop": affine_stop,
            "parameters": list(map(float, affine_tx.GetParameters())),
            "fixed_parameters": list(map(float, affine_tx.GetFixedParameters())),
            "metrics": metrics(fixed_mask, affine_arr),
        },
    }
    return info, affine_arr, affine_mri


def upsample_to_shape(arr, target_shape: Sequence[int], order: int):
    from scipy import ndimage
    import numpy as np
    target_shape = tuple(int(x) for x in target_shape)
    zoom_factors = [t / s for t, s in zip(target_shape, arr.shape)]
    up = ndimage.zoom(arr, zoom_factors, order=order)
    out = np.zeros(target_shape, dtype=up.dtype)
    src = []
    dst = []
    for ax, t in enumerate(target_shape):
        n = min(up.shape[ax], t)
        src.append(slice(0, n))
        dst.append(slice(0, n))
    out[tuple(dst)] = up[tuple(src)]
    return out


def update_metadata(base_metadata_path: Path, atlas_name: str, target_shape: Sequence[int], reference_stats: Dict[str, Any]) -> Dict[str, Any]:
    if base_metadata_path.exists():
        meta = json.loads(base_metadata_path.read_text(encoding="utf-8"))
    else:
        meta = {}
    meta.update({
        "name": atlas_name,
        "atlas_name": atlas_name,
        "title": f"Paxinos-Watson rat atlas with SimpleITK affine Waxholm MRI reference test ({atlas_name})",
        "orientation": "PIL",
        "shape": list(map(int, target_shape)),
        "annotation_shape": list(map(int, target_shape)),
        "reference_shape": list(map(int, target_shape)),
        "reference_strategy": "waxholm_mri_simpleitk_affine_multires_test_reference",
        "status": "diagnostic_test_atlas_not_stable",
        "warning": "Diagnostic test atlas only. Waxholm MRI reference is affine-registered from configurable-resolution mask registration and resampled/upscaled. It is not a final deformable anatomical registration.",
        "version": "1.0",
        "additional_references": [],
        "reference_file": "reference.tiff",
        "annotation_file": "annotation.tiff",
        "hemispheres_file": "hemispheres.tiff",
        "v32_11_note": "Built from V32.9/V32.10 SimpleITK affine proof-of-concept at higher diagnostic resolution. Stable paxinos_watson_rat_40um atlas is not modified.",
        "reference_stats": reference_stats,
    })
    return meta


def copy_base_atlas(base_dir: Path, out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(base_dir, out_dir)


def patch_last_versions(cache_root: Path, atlas_name: str) -> Optional[str]:
    ensure_dir(cache_root)
    last_versions = cache_root / "last_versions.conf"
    backup = None
    if last_versions.exists():
        backup_path = cache_root / f"last_versions.conf.backup_v32_11_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(last_versions, backup_path)
        backup = str(backup_path)
        lines = last_versions.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = []
    new_line = f"{atlas_name} = 1.0"
    replaced = False
    out_lines = []
    for line in lines:
        if line.strip().startswith(f"{atlas_name} ") or line.strip().startswith(f"{atlas_name}="):
            if not replaced:
                out_lines.append(new_line)
                replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(new_line)
    last_versions.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return backup


def install_to_brainglobe_cache(candidate_dir: Path, atlas_name: str) -> Dict[str, Any]:
    cache_root = Path.home() / ".brainglobe"
    ensure_dir(cache_root)
    cache_dir = cache_root / f"{atlas_name}_v1.0"
    backup = None
    if cache_dir.exists():
        backup_dir = cache_root / f"_{atlas_name}_backup_v32_11_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.move(str(cache_dir), str(backup_dir))
        backup = str(backup_dir)
    shutil.copytree(candidate_dir, cache_dir)
    last_versions_backup = patch_last_versions(cache_root, atlas_name)
    return {
        "cache_root": str(cache_root),
        "cache_dir": str(cache_dir),
        "existing_cache_backup": backup,
        "last_versions_conf": str(cache_root / "last_versions.conf"),
        "last_versions_backup": last_versions_backup,
    }


def write_text_summary(path: Path, report: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("V32.11 Multi-resolution SimpleITK affine Waxholm reference test")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Report dir: {report.get('report_dir')}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- Build higher-resolution diagnostic BrainGlobe test atlas/atlases from the V32.9/V32.10 SimpleITK affine path.")
    lines.append("- Stable paxinos_watson_rat_40um is not modified.")
    lines.append("- References are multi-resolution/configurable-resolution affine results resampled/upscaled to the target grid; not final deformable registration.")
    lines.append("")
    lines.append("Backend status:")
    for name, st in report.get("backend_status", {}).items():
        lines.append(f"- {name}: available={st.get('available')} version={st.get('version')} error={st.get('error')}")
    lines.append("")
    lines.append("Built atlases:")
    for res in report.get("results", []):
        lines.append(f"- {res.get('atlas_name')}")
        lines.append(f"  rank={res.get('rank')} perm={res.get('perm')} flips={res.get('flips')} shift={res.get('shift_lowres')}")
        lines.append(f"  shifted metrics: {res.get('shifted_metrics_lowres')}")
        lines.append(f"  affine metrics:  {res.get('simpleitk_registration', {}).get('affine', {}).get('metrics')}")
        lines.append(f"  candidate_dir:   {res.get('candidate_dir')}")
        lines.append(f"  cache_dir:       {res.get('cache', {}).get('cache_dir')}")
        lines.append(f"  preview:         {res.get('preview')}")
    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for err in report.get("errors", []):
            lines.append(f"- {err.get('error')}")
    lines.append("")
    lines.append("ABBA test:")
    lines.append("- Restart Fiji/ABBA completely.")
    for res in report.get("results", []):
        lines.append(f"- Open {res.get('atlas_name')}")
    lines.append("- Check reference/borders visibility, coronal/sagittal/horizontal orientation, and whether Paxinos borders sit plausibly on Waxholm anatomy.")
    lines.append("")
    lines.append("Conclusion:")
    lines.append(str(report.get("global_conclusion")))
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    project_root = find_project_root()
    report_dir = ensure_dir(project_root / "reports" / "v32_11_multires_simpleitk_affine_waxholm_reference_test")
    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": False,
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "purpose": "Build higher-resolution diagnostic BrainGlobe test atlas from V32.9/V32.10 SimpleITK affine registration proof.",
        "does_modify_stable_atlas": False,
        "does_install_test_atlases": True,
        "reference_generation_mode": "multires_simpleitk_affine_resampled_or_upsampled_to_target_grid",
        "backend_status": {},
        "errors": [],
        "results": [],
    }
    for name in ["numpy", "scipy", "nibabel", "matplotlib", "SimpleITK", "tifffile"]:
        report["backend_status"][name] = module_status(name)

    try:
        if not report["backend_status"].get("SimpleITK", {}).get("available"):
            raise RuntimeError("SimpleITK is not available. Run the install helper from V32.9/V32.10 first.")
        if not report["backend_status"].get("tifffile", {}).get("available"):
            raise RuntimeError("tifffile is not available. Install tifffile into the project venv first.")

        import numpy as np

        base_dir = project_root / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
        target_annotation = base_dir / "annotation.nii.gz"
        waxholm_mri = project_root / "data" / "raw" / "bluebrainheadmodels" / "Waxholm_Atlas_MRI.nii.gz"
        waxholm_mask = project_root / "data" / "raw" / "bluebrainheadmodels" / "Waxholm_Atlas_Mask.nii.gz"
        missing = [str(p) for p in [base_dir, target_annotation, waxholm_mri, waxholm_mask] if not p.exists()]
        if missing:
            raise FileNotFoundError("Missing required input files: " + "; ".join(missing))

        fixed_ann_full, fixed_img = load_nifti(target_annotation)
        moving_mri_full, moving_mri_img = load_nifti(waxholm_mri)
        moving_mask_full, moving_mask_img = load_nifti(waxholm_mask)
        target_shape = tuple(int(x) for x in fixed_ann_full.shape)
        fixed_mask_full = fixed_ann_full > 0
        factor = int(os.environ.get("V32_11_REGISTRATION_FACTOR", "2"))
        if factor < 1:
            factor = 1
        ranks_env = os.environ.get("V32_11_RANKS", "1").replace(" ", "")
        requested_ranks = {int(x) for x in ranks_env.split(",") if x.strip().isdigit()} or {1}
        fixed_mask_low = lowres_by_stride(fixed_mask_full, factor) > 0
        moving_mri_low = lowres_by_stride(moving_mri_full, factor).astype("float32")
        moving_mask_low = lowres_by_stride(moving_mask_full, factor) > 0

        report["registration_factor_actual"] = factor
        report["requested_ranks"] = sorted(requested_ranks)
        report["inputs"] = {
            "base_dir": str(base_dir),
            "target_annotation": str(target_annotation),
            "waxholm_mri": str(waxholm_mri),
            "waxholm_mask": str(waxholm_mask),
            "target_shape": list(target_shape),
            "target_orientation": "PIL",
            "waxholm_shape": list(map(int, moving_mri_full.shape)),
            "waxholm_orientation": "PIR",
            "low_factor": factor,
        }

        candidates = [
            {"rank": 1, "name": "rank01", "atlas_name": "paxinos_watson_rat_40um_waxholm_multires_affine_rank01_test", "perm": [2, 1, 0], "flips": [True, True, False], "shift_factor4": [-3, -4, 2]},
            {"rank": 2, "name": "rank02", "atlas_name": "paxinos_watson_rat_40um_waxholm_multires_affine_rank02_test", "perm": [2, 1, 0], "flips": [True, True, True], "shift_factor4": [-3, -4, -2]},
        ]
        candidates = [c for c in candidates if int(c["rank"]) in requested_ranks]
        # V32.8 shifts were measured at low_factor=4. Scale them to the current diagnostic factor.
        for c in candidates:
            scale = 4.0 / float(factor)
            c["shift_lowres"] = [int(round(v * scale)) for v in c["shift_factor4"]]

        for cand in candidates:
            atlas_name = cand["atlas_name"]
            transformed_low = transform_candidate_to_fixed_space(
                moving_mask=moving_mask_low,
                moving_mri=moving_mri_low,
                fixed_mask=fixed_mask_low,
                perm=cand["perm"],
                flips=cand["flips"],
                shift=cand["shift_lowres"],
            )
            shifted_metrics = metrics(fixed_mask_low, transformed_low["mask_shifted"])
            reg_info, affine_mask_low, affine_mri_low = run_simpleitk_affine(
                fixed_mask=fixed_mask_low,
                moving_mask=transformed_low["mask_shifted"],
                moving_mri=transformed_low["mri_shifted"],
            )
            affine_metrics = reg_info["affine"]["metrics"]

            # V32.11 diagnostic atlas: upsample affine lowres result into the full target grid.
            affine_ref_full_float = upsample_to_shape(affine_mri_low, target_shape, order=1).astype("float32")
            affine_mask_full = upsample_to_shape(affine_mask_low.astype("uint8"), target_shape, order=0).astype(bool)
            reference_u16 = normalize_to_uint16(affine_ref_full_float)

            candidate_dir = project_root / "data" / "output" / "brainglobe_official_candidate" / atlas_name
            copy_base_atlas(base_dir, candidate_dir)
            save_nifti_uint16(candidate_dir / "reference.nii.gz", reference_u16, fixed_img)
            save_tiff(candidate_dir / "reference.tiff", reference_u16)

            meta = update_metadata(candidate_dir / "metadata.json", atlas_name, target_shape, stats(reference_u16))
            meta.update({
                "v32_11_candidate": {
                    "registration_factor": factor,
                    "shift_factor4": cand.get("shift_factor4"),
                    "rank": cand["rank"],
                    "perm": cand["perm"],
                    "flips": cand["flips"],
                    "shift_lowres": cand["shift_lowres"],
                    "shifted_lowres_metrics": shifted_metrics,
                    "simpleitk_affine_lowres_metrics": affine_metrics,
                    "simpleitk_affine_parameters": reg_info["affine"].get("parameters"),
                    "simpleitk_affine_fixed_parameters": reg_info["affine"].get("fixed_parameters"),
                }
            })
            (candidate_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=_json_default), encoding="utf-8")
            (candidate_dir / "version.txt").write_text("1.0\n", encoding="utf-8")

            # Additional QC mask saved in report folder, not atlas folder, to avoid confusing ABBA as extra channel.
            mask_qc_path = report_dir / f"v32_11_{cand['name']}_affine_waxholm_mask_lowres_upsampled_qc.nii.gz"
            save_nifti_uint16(mask_qc_path, affine_mask_full.astype("uint16"), fixed_img)

            preview_path = report_dir / f"v32_11_{atlas_name}_preview.png"
            preview_panel(preview_path, fixed_mask_full, reference_u16, affine_mask_full, f"V32.11 {atlas_name} | SimpleITK affine Waxholm reference test")

            cache_info = install_to_brainglobe_cache(candidate_dir, atlas_name)
            res = {
                **cand,
                "candidate_dir": str(candidate_dir),
                "cache": cache_info,
                "shifted_metrics_lowres": shifted_metrics,
                "simpleitk_registration_lowres": reg_info,
                "reference_stats": stats(reference_u16),
                "affine_mask_full_stats": stats(affine_mask_full.astype("uint8")),
                "preview": str(preview_path),
                "mask_qc_path": str(mask_qc_path),
            }
            report["results"].append(res)

        report["global_conclusion"] = (
            "V32.11 built higher-resolution SimpleITK-affine Waxholm reference test atlas/atlases for the requested rank(s). "
            "These are diagnostic ABBA/QC atlases generated from configurable-resolution SimpleITK affine registration and resampled/upscaled to the Paxinos target grid. "
            "Do not promote to stable unless ABBA visual QC confirms usefulness and limitations are documented. "
            "For final anatomical accuracy, full-resolution and/or deformable registration is still required unless ABBA visual QC is explicitly accepted as sufficient."
        )
        report["passed"] = True
    except Exception as e:
        report["errors"].append({"error": repr(e), "traceback": traceback.format_exc()})
        report["global_conclusion"] = "V32.11 failed. See JSON errors; no stable atlas was intentionally modified."

    report_path = report_dir / "v32_11_multires_simpleitk_affine_waxholm_reference_test_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    summary_path = report_dir / "v32_11_multires_simpleitk_affine_waxholm_reference_test_summary.txt"
    write_text_summary(summary_path, report)
    print(summary_path.read_text(encoding="utf-8"))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
