#!/usr/bin/env python3
"""
V32.9 Proper Registration Backend Prep

Diagnostic/pre-integration script for the rat Paxinos BrainGlobe builder.
It prepares and optionally runs a SimpleITK low-resolution registration proof-of-concept.

It does not modify or install atlas data.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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
    # Default used throughout this project. Windows path is harmless if not present; script reports it.
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

    # ndimage.zoom can round by one voxel. Crop/pad to exact target shape.
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


def preview_panel(path: Path, fixed_mask, moving_initial, mri_initial, moving_refined, mri_refined, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fixed_border = mask_border(fixed_mask)
    init_border = mask_border(moving_initial)
    ref_border = mask_border(moving_refined)

    fig, axes = plt.subplots(3, 4, figsize=(18, 13))
    fig.suptitle(title, fontsize=13)
    labels = [
        ("axis0/AP-coronal mid", 0),
        ("axis1/SI-horizontal mid", 1),
        ("axis2/LR-sagittal mid", 2),
    ]
    for row, (lab, axis) in enumerate(labels):
        panels = [
            (mri_initial, init_border, "initial MRI + moving border"),
            (mri_refined, ref_border, "refined MRI + moving border"),
            (fixed_mask.astype("float32") + moving_refined.astype("float32") * 2, None, "fixed + refined mask"),
            (mri_refined, fixed_mask, "refined MRI + Paxinos mask"),
        ]
        for col, (img3, border3, subtitle) in enumerate(panels):
            ax = axes[row, col]
            if col == 2:
                sl = take_slice(img3, axis)
                ax.imshow(sl.T, cmap="viridis", origin="lower")
            else:
                sl = normalize_slice(take_slice(img3, axis))
                ax.imshow(sl.T, cmap="gray", origin="lower")
                if col == 0 and border3 is not None:
                    ax.contour(take_slice(border3, axis).T, levels=[0.5], colors=["magenta"], linewidths=0.7)
                    ax.contour(take_slice(fixed_border, axis).T, levels=[0.5], colors=["yellow"], linewidths=0.7)
                elif col == 1 and border3 is not None:
                    ax.contour(take_slice(border3, axis).T, levels=[0.5], colors=["cyan"], linewidths=0.7)
                    ax.contour(take_slice(fixed_border, axis).T, levels=[0.5], colors=["yellow"], linewidths=0.7)
                elif col == 3:
                    ax.imshow(np.ma.masked_where(take_slice(fixed_mask, axis).T == 0, take_slice(fixed_mask, axis).T), cmap="Wistia", alpha=0.45, origin="lower")
            ax.set_title(f"{subtitle}\n{lab}", fontsize=9)
            ax.axis("off")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=140)
    plt.close(fig)


def try_simpleitk_registration(fixed_mask, moving_mask, moving_mri, report_dir: Path, label: str) -> Dict[str, Any]:
    import numpy as np
    out: Dict[str, Any] = {"attempted": True, "success": False, "error": None}
    try:
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

        def execute_registration(transform, name: str, iterations: int, learning_rate: float):
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

        # Rigid first.
        rigid0 = sitk.CenteredTransformInitializer(
            fixed_dm,
            moving_dm,
            sitk.Euler3DTransform(),
            sitk.CenteredTransformInitializerFilter.GEOMETRY,
        )
        rigid_tx, rigid_metric, rigid_iter, rigid_stop = execute_registration(rigid0, "rigid", 80, 1.0)

        # Affine initialized independently. This remains a backend proof, not final atlas data.
        affine0 = sitk.CenteredTransformInitializer(
            fixed_dm,
            moving_dm,
            sitk.AffineTransform(3),
            sitk.CenteredTransformInitializerFilter.GEOMETRY,
        )
        affine_tx, affine_metric, affine_iter, affine_stop = execute_registration(affine0, "affine", 120, 0.5)

        fixed_ref = fixed_img_u8
        moving_mask_rigid = sitk.Resample(moving_img_u8, fixed_ref, rigid_tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
        moving_mask_affine = sitk.Resample(moving_img_u8, fixed_ref, affine_tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
        moving_mri_affine = sitk.Resample(moving_mri_img, fixed_ref, affine_tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)

        rigid_arr = sitk.GetArrayFromImage(moving_mask_rigid).astype(bool)
        affine_arr = sitk.GetArrayFromImage(moving_mask_affine).astype(bool)
        affine_mri = sitk.GetArrayFromImage(moving_mri_affine).astype("float32")

        out.update({
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
        })
        return out, affine_arr, affine_mri
    except Exception as e:
        out["error"] = repr(e)
        out["traceback"] = traceback.format_exc()
        return out, moving_mask, moving_mri


def write_text_summary(path: Path, report: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("V32.9 Proper Registration Backend Prep")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Report dir: {report.get('report_dir')}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- Prepare real registration backend after V32.8 showed bbox+translation is insufficient.")
    lines.append("- Diagnostic only. No atlas data is modified or installed.")
    lines.append("")
    lines.append("Backend status:")
    for name, st in report.get("backend_status", {}).items():
        lines.append(f"- {name}: available={st.get('available')} version={st.get('version')} error={st.get('error')}")
    lines.append("")
    lines.append("Input status:")
    lines.append(f"- target_annotation: {report.get('target_annotation')}")
    lines.append(f"- waxholm_mri:       {report.get('waxholm_mri')}")
    lines.append(f"- waxholm_mask:      {report.get('waxholm_mask')}")
    lines.append("")
    lines.append("Candidate results:")
    for res in report.get("results", []):
        lines.append(f"- {res.get('name')} rank={res.get('rank')} perm={res.get('perm')} flips={res.get('flips')} shift={res.get('shift_lowres')}")
        lines.append(f"  initial metrics: {res.get('initial_metrics')}")
        lines.append(f"  shifted metrics: {res.get('shifted_metrics')}")
        backend = res.get("simpleitk_registration")
        if backend:
            lines.append(f"  SimpleITK attempted={backend.get('attempted')} success={backend.get('success')}")
            if backend.get("success"):
                lines.append(f"  rigid metrics:  {backend.get('rigid', {}).get('metrics')}")
                lines.append(f"  affine metrics: {backend.get('affine', {}).get('metrics')}")
            else:
                lines.append(f"  SimpleITK error: {backend.get('error')}")
        lines.append(f"  preview: {res.get('preview_initial')}")
        if res.get('preview_simpleitk'):
            lines.append(f"  SimpleITK preview: {res.get('preview_simpleitk')}")
    lines.append("")
    lines.append("Conclusion:")
    lines.append(str(report.get("global_conclusion")))
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    project_root = find_project_root()
    report_dir = ensure_dir(project_root / "reports" / "v32_9_registration_backend_prep")

    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": False,
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "purpose": "Prepare real registration backend after V32.8 showed bbox+translation is insufficient.",
        "does_modify_or_install_atlas": False,
        "candidate_policy": "Only V32.7b/V32.8 rank 1 and rank 2 are considered. No stable atlas promotion.",
        "backend_status": {},
        "errors": [],
        "results": [],
    }

    for name in ["numpy", "scipy", "nibabel", "matplotlib", "SimpleITK", "ants"]:
        report["backend_status"][name] = module_status(name)

    target_annotation = project_root / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um" / "annotation.nii.gz"
    waxholm_mri = project_root / "data" / "raw" / "bluebrainheadmodels" / "Waxholm_Atlas_MRI.nii.gz"
    waxholm_mask = project_root / "data" / "raw" / "bluebrainheadmodels" / "Waxholm_Atlas_Mask.nii.gz"
    report["target_annotation"] = str(target_annotation)
    report["waxholm_mri"] = str(waxholm_mri)
    report["waxholm_mask"] = str(waxholm_mask)

    try:
        missing = [str(p) for p in [target_annotation, waxholm_mri, waxholm_mask] if not p.exists()]
        if missing:
            raise FileNotFoundError("Missing required input files: " + "; ".join(missing))

        import numpy as np

        fixed_ann, fixed_img = load_nifti(target_annotation)
        moving_mri_full, moving_mri_img = load_nifti(waxholm_mri)
        moving_mask_full, moving_mask_img = load_nifti(waxholm_mask)

        factor = 4
        fixed_mask = lowres_by_stride(fixed_ann, factor) > 0
        moving_mri = lowres_by_stride(moving_mri_full, factor).astype("float32")
        moving_mask = lowres_by_stride(moving_mask_full, factor) > 0

        report["target"] = {
            "shape_full": list(fixed_ann.shape),
            "shape_lowres": list(fixed_mask.shape),
            "orientation_full": "PIL",
            "voxel_size_full": list(map(float, fixed_img.header.get_zooms()[:3])),
            "mask_stats_lowres": stats(fixed_mask.astype("uint8")),
        }
        report["waxholm"] = {
            "mri_shape_full": list(moving_mri_full.shape),
            "mask_shape_full": list(moving_mask_full.shape),
            "orientation_full": "PIR",
            "voxel_size_full": list(map(float, moving_mri_img.header.get_zooms()[:3])),
            "mri_stats_lowres": stats(moving_mri),
            "mask_stats_lowres": stats(moving_mask.astype("uint8")),
        }

        candidates = [
            {"rank": 1, "name": "rank01", "perm": [2, 1, 0], "flips": [True, True, False], "shift_lowres": [-3, -4, 2]},
            {"rank": 2, "name": "rank02", "perm": [2, 1, 0], "flips": [True, True, True], "shift_lowres": [-3, -4, -2]},
        ]

        sitk_available = bool(report["backend_status"].get("SimpleITK", {}).get("available"))

        for cand in candidates:
            transformed = transform_candidate_to_fixed_space(
                moving_mask=moving_mask,
                moving_mri=moving_mri,
                fixed_mask=fixed_mask,
                perm=cand["perm"],
                flips=cand["flips"],
                shift=cand["shift_lowres"],
            )
            initial_metrics = metrics(fixed_mask, transformed["mask_initial"])
            shifted_metrics = metrics(fixed_mask, transformed["mask_shifted"])
            preview_initial = report_dir / f"v32_9_{cand['name']}_initial_backend_input_preview.png"
            preview_panel(
                preview_initial,
                fixed_mask,
                transformed["mask_initial"],
                transformed["mri_initial"],
                transformed["mask_shifted"],
                transformed["mri_shifted"],
                f"V32.9 {cand['name']}: backend input after V32.8 translation | shift={cand['shift_lowres']}",
            )

            res: Dict[str, Any] = {
                **cand,
                "initial_metrics": initial_metrics,
                "shifted_metrics": shifted_metrics,
                "bbox_info": {k: v for k, v in transformed.items() if k not in {"mask_initial", "mri_initial", "mask_shifted", "mri_shifted"}},
                "preview_initial": str(preview_initial),
            }

            if sitk_available:
                backend, affine_mask, affine_mri = try_simpleitk_registration(
                    fixed_mask=fixed_mask,
                    moving_mask=transformed["mask_shifted"],
                    moving_mri=transformed["mri_shifted"],
                    report_dir=report_dir,
                    label=cand["name"],
                )
                res["simpleitk_registration"] = backend
                preview_sitk = report_dir / f"v32_9_{cand['name']}_simpleitk_registration_preview.png"
                preview_panel(
                    preview_sitk,
                    fixed_mask,
                    transformed["mask_shifted"],
                    transformed["mri_shifted"],
                    affine_mask,
                    affine_mri,
                    f"V32.9 {cand['name']}: SimpleITK affine proof-of-concept",
                )
                res["preview_simpleitk"] = str(preview_sitk)
            else:
                res["simpleitk_registration"] = {
                    "attempted": False,
                    "success": False,
                    "error": "SimpleITK not available. Use RUN_V32_9_INSTALL_SIMPLEITK_AND_RUN.bat to install it into the project venv, then rerun.",
                }
                res["preview_simpleitk"] = None

            report["results"].append(res)

        if sitk_available:
            report["global_conclusion"] = (
                "SimpleITK backend was available and registration proof-of-concept previews were generated. "
                "Judge affine metrics and anatomy before building any refined test atlas. "
                "No stable atlas has been modified."
            )
        else:
            report["global_conclusion"] = (
                "SimpleITK is not available in the current venv. V32.9 prepared backend inputs and previews only. "
                "Install SimpleITK or choose ANTs/Elastix/BigWarp for real affine/deformable registration. "
                "No stable atlas has been modified."
            )

        report["passed"] = True
    except Exception as e:
        report["errors"].append({"error": repr(e), "traceback": traceback.format_exc()})
        report["global_conclusion"] = "V32.9 failed before completing backend prep. See errors in JSON report."

    # Write outputs regardless of success.
    backend_status_path = report_dir / "v32_9_backend_status.json"
    backend_status_path.write_text(json.dumps(report.get("backend_status", {}), indent=2, default=_json_default), encoding="utf-8")

    report_path = report_dir / "v32_9_registration_backend_prep_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")

    summary_path = report_dir / "v32_9_registration_backend_prep_summary.txt"
    write_text_summary(summary_path, report)

    print(summary_path.read_text(encoding="utf-8"))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
