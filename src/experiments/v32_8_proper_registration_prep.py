#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V32.8 Proper Registration Prep
==============================

Preview-only mask-registration refinement for the Paxinos/Waxholm rat atlas work.

Purpose:
- Use the anatomically plausible V32.7b candidates only: rank 1 and rank 2.
- Start from the known orientation/bbox-fit transforms.
- Refine the gross fit using low-resolution integer translation search on Waxholm mask vs Paxinos mask.
- Produce before/after previews and a machine-readable report.
- Do NOT overwrite or promote the stable Paxinos atlas.
- Do NOT install new BrainGlobe atlases.

This is still not a final nonlinear registration. If translation refinement does not clearly improve the
alignment, the next step is a real registration backend such as SimpleITK/ANTs/Elastix/BigWarp.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    import nibabel as nib
except Exception as e:  # pragma: no cover
    raise SystemExit(f"ERROR: nibabel is required but could not be imported: {e}")

try:
    from skimage.transform import resize
except Exception as e:  # pragma: no cover
    raise SystemExit(f"ERROR: scikit-image is required but could not be imported: {e}")

try:
    import scipy.ndimage as ndi
    SCIPY_AVAILABLE = True
except Exception:
    ndi = None
    SCIPY_AVAILABLE = False

try:
    import SimpleITK as sitk  # type: ignore
    SIMPLEITK_AVAILABLE = True
    SIMPLEITK_VERSION = getattr(sitk, "Version_VersionString", lambda: "unknown")()
except Exception:
    sitk = None
    SIMPLEITK_AVAILABLE = False
    SIMPLEITK_VERSION = None

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT_DEFAULT = Path(r"G:\rat-paxinos-brainglobe-builder")

TARGET_REL = Path("data/output/brainglobe_official_candidate/paxinos_watson_rat_40um/annotation.nii.gz")
WAXHOLM_MRI_REL = Path("data/raw/bluebrainheadmodels/Waxholm_Atlas_MRI.nii.gz")
WAXHOLM_MASK_REL = Path("data/raw/bluebrainheadmodels/Waxholm_Atlas_Mask.nii.gz")
OUT_REL = Path("reports/v32_8_proper_registration_prep")

LOW_FACTOR_TARGET = 4
LOW_FACTOR_MOVING = 4
COARSE_MAX_SHIFT = 18
COARSE_STEP = 3
LOCAL_RADIUS = 4

# Rank 1 and Rank 2 from V32.7b. Do not use ranks 3-8 unless you intentionally edit this file.
CANDIDATES = [
    {
        "rank": 1,
        "name": "rank01",
        "perm": [2, 1, 0],
        "flips": [True, True, False],
        "dice_v32_7b": 0.7023575287089042,
        "comment": "primary candidate from V32.7b; best mask Dice and plausible anatomy",
    },
    {
        "rank": 2,
        "name": "rank02",
        "perm": [2, 1, 0],
        "flips": [True, True, True],
        "dice_v32_7b": 0.7020001916504296,
        "comment": "LR/last-flip control candidate; visually very similar to rank 1",
    },
]


@dataclass
class MaskMetrics:
    dice: float
    jaccard: float
    coverage_fixed: float
    extra_fraction: float
    intersection_voxels: int
    fixed_voxels: int
    moving_voxels: int


@dataclass
class CandidateResult:
    rank: int
    name: str
    perm: List[int]
    flips: List[bool]
    dice_v32_7b: float
    initial_metrics: Dict[str, object]
    best_translation_voxels_lowres: List[int]
    refined_metrics: Dict[str, object]
    improvement: Dict[str, float]
    preview: str
    recommendation: str


def project_root_from_argv() -> Path:
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        return Path(sys.argv[1]).resolve()
    env_root = os.environ.get("RAT_PAXINOS_PROJECT_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "data").exists() and (cwd / "src").exists():
        return cwd
    return PROJECT_ROOT_DEFAULT


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def orientation_of(path: Path) -> Optional[str]:
    try:
        img = nib.load(str(path))
        return "".join(nib.orientations.aff2axcodes(img.affine))
    except Exception:
        return None


def voxel_size_of(path: Path) -> Optional[List[float]]:
    try:
        img = nib.load(str(path))
        return [float(x) for x in img.header.get_zooms()[:3]]
    except Exception:
        return None


def downsample_proxy(path: Path, factor: int, dtype: Optional[np.dtype] = None) -> np.ndarray:
    img = nib.load(str(path))
    # Use the array proxy for slicing so the 900 MB Waxholm MRI does not need to be fully loaded.
    arr = np.asanyarray(img.dataobj[::factor, ::factor, ::factor])
    if dtype is not None:
        arr = arr.astype(dtype, copy=False)
    return arr


def normalize_uint16(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(arr)
    if not finite.any():
        return np.zeros(arr.shape, dtype=np.uint16)
    vals = arr[finite]
    lo = float(np.percentile(vals, 0.5))
    hi = float(np.percentile(vals, 99.5))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(vals))
        hi = float(np.nanmax(vals))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint16)
    out = np.clip((arr - lo) / (hi - lo), 0, 1)
    return (out * 65535).astype(np.uint16)


def bbox(mask: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    coords = np.argwhere(mask > 0)
    if coords.size == 0:
        return None
    return coords.min(axis=0), coords.max(axis=0)


def crop_by_bbox(arr: np.ndarray, b: Tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    mn, mx = b
    slices = tuple(slice(int(mn[i]), int(mx[i]) + 1) for i in range(arr.ndim))
    return arr[slices]


def apply_perm_flips(arr: np.ndarray, perm: Sequence[int], flips: Sequence[bool]) -> np.ndarray:
    out = np.transpose(arr, axes=tuple(perm))
    for axis, do_flip in enumerate(flips):
        if do_flip:
            out = np.flip(out, axis=axis)
    return np.ascontiguousarray(out)


def resize_to_shape(arr: np.ndarray, shape: Sequence[int], order: int) -> np.ndarray:
    return resize(
        arr,
        output_shape=tuple(int(x) for x in shape),
        order=order,
        mode="constant",
        cval=0,
        anti_aliasing=(order > 0),
        preserve_range=True,
    )


def bbox_fit_to_fixed(
    moving_arr: np.ndarray,
    moving_mask: np.ndarray,
    fixed_mask: np.ndarray,
    order: int,
) -> np.ndarray:
    fixed_b = bbox(fixed_mask)
    moving_b = bbox(moving_mask)
    if fixed_b is None or moving_b is None:
        return np.zeros(fixed_mask.shape, dtype=moving_arr.dtype)

    f_mn, f_mx = fixed_b
    target_shape = (f_mx - f_mn + 1).astype(int)
    moving_crop = crop_by_bbox(moving_arr, moving_b)
    resized = resize_to_shape(moving_crop, target_shape, order=order)

    out = np.zeros(fixed_mask.shape, dtype=resized.dtype)
    slices = tuple(slice(int(f_mn[i]), int(f_mx[i]) + 1) for i in range(3))
    out[slices] = resized
    return out


def metrics(fixed_mask: np.ndarray, moving_mask: np.ndarray) -> MaskMetrics:
    f = fixed_mask.astype(bool, copy=False)
    m = moving_mask.astype(bool, copy=False)
    inter = int(np.logical_and(f, m).sum())
    fvox = int(f.sum())
    mvox = int(m.sum())
    union = int(np.logical_or(f, m).sum())
    dice = (2.0 * inter / (fvox + mvox)) if (fvox + mvox) else 0.0
    jaccard = (inter / union) if union else 0.0
    coverage = (inter / fvox) if fvox else 0.0
    extra = ((mvox - inter) / mvox) if mvox else 0.0
    return MaskMetrics(
        dice=float(dice),
        jaccard=float(jaccard),
        coverage_fixed=float(coverage),
        extra_fraction=float(extra),
        intersection_voxels=inter,
        fixed_voxels=fvox,
        moving_voxels=mvox,
    )


def integer_shift_3d(arr: np.ndarray, shift: Sequence[int], fill_value=0) -> np.ndarray:
    shift = [int(x) for x in shift]
    out = np.full(arr.shape, fill_value, dtype=arr.dtype)

    src_slices = []
    dst_slices = []
    for ax, s in enumerate(shift):
        n = arr.shape[ax]
        if s >= 0:
            src_start, src_end = 0, n - s
            dst_start, dst_end = s, n
        else:
            src_start, src_end = -s, n
            dst_start, dst_end = 0, n + s
        if src_end <= src_start or dst_end <= dst_start:
            return out
        src_slices.append(slice(src_start, src_end))
        dst_slices.append(slice(dst_start, dst_end))
    out[tuple(dst_slices)] = arr[tuple(src_slices)]
    return out


def best_translation_search(
    fixed_mask: np.ndarray,
    moving_mask: np.ndarray,
    max_shift: int = COARSE_MAX_SHIFT,
    coarse_step: int = COARSE_STEP,
    local_radius: int = LOCAL_RADIUS,
) -> Tuple[List[int], MaskMetrics, List[Dict[str, object]]]:
    candidates: List[Tuple[float, Tuple[int, int, int], MaskMetrics]] = []
    coarse_values = list(range(-max_shift, max_shift + 1, coarse_step))
    for dx in coarse_values:
        for dy in coarse_values:
            for dz in coarse_values:
                shifted = integer_shift_3d(moving_mask, (dx, dy, dz), fill_value=0)
                mm = metrics(fixed_mask, shifted)
                candidates.append((mm.dice, (dx, dy, dz), mm))
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_shift = candidates[0][1]
    best_m = candidates[0][2]

    local_candidates: List[Tuple[float, Tuple[int, int, int], MaskMetrics]] = []
    for dx in range(best_shift[0] - local_radius, best_shift[0] + local_radius + 1):
        for dy in range(best_shift[1] - local_radius, best_shift[1] + local_radius + 1):
            for dz in range(best_shift[2] - local_radius, best_shift[2] + local_radius + 1):
                shifted = integer_shift_3d(moving_mask, (dx, dy, dz), fill_value=0)
                mm = metrics(fixed_mask, shifted)
                local_candidates.append((mm.dice, (dx, dy, dz), mm))
    local_candidates.sort(key=lambda x: x[0], reverse=True)
    if local_candidates and local_candidates[0][0] >= candidates[0][0]:
        best_shift = local_candidates[0][1]
        best_m = local_candidates[0][2]

    top = []
    for score, shift, mm in (local_candidates[:20] if local_candidates else candidates[:20]):
        d = asdict(mm)
        d.update({"shift": list(shift)})
        top.append(d)
    return list(best_shift), best_m, top


def try_phase_correlation(fixed_mask: np.ndarray, moving_mask: np.ndarray) -> Optional[List[float]]:
    try:
        from skimage.registration import phase_cross_correlation
        shift, error, phasediff = phase_cross_correlation(
            fixed_mask.astype(np.float32),
            moving_mask.astype(np.float32),
            upsample_factor=1,
        )
        # skimage shift is the shift needed to apply to moving to match fixed.
        return [float(x) for x in shift]
    except Exception:
        return None


def maybe_simpleitk_translation_note() -> Dict[str, object]:
    return {
        "available": SIMPLEITK_AVAILABLE,
        "version": SIMPLEITK_VERSION,
        "used": False,
        "note": (
            "SimpleITK detected, but V32.8 keeps the automatic step limited to deterministic low-res "
            "integer translation refinement. Use SimpleITK/ANTs/Elastix in the next step if bbox+translation remains insufficient."
            if SIMPLEITK_AVAILABLE
            else "SimpleITK not detected. This is fine for V32.8; proper affine/deformable registration will need a backend later."
        ),
    }


def axis_mid(arr: np.ndarray, axis: int) -> np.ndarray:
    idx = arr.shape[axis] // 2
    if axis == 0:
        return arr[idx, :, :]
    if axis == 1:
        return arr[:, idx, :]
    return arr[:, :, idx]


def prepare_img2d(img: np.ndarray) -> np.ndarray:
    return np.rot90(np.asarray(img))


def overlay_mask(ax, base2d: np.ndarray, mask2d: np.ndarray, alpha=0.35, color="yellow") -> None:
    # For diagnostic PNGs only. Keep explicit color semantics readable.
    arr = np.zeros((*mask2d.shape, 4), dtype=np.float32)
    colors = {
        "yellow": (1.0, 0.9, 0.0, alpha),
        "magenta": (1.0, 0.0, 1.0, alpha),
        "cyan": (0.0, 1.0, 1.0, alpha),
    }
    rgba = colors.get(color, (1.0, 1.0, 1.0, alpha))
    arr[mask2d.astype(bool)] = rgba
    ax.imshow(arr)


def draw_contour(ax, mask2d: np.ndarray, color: str, linewidth: float = 0.8) -> None:
    try:
        ax.contour(mask2d.astype(float), levels=[0.5], colors=[color], linewidths=linewidth)
    except Exception:
        pass


def save_candidate_preview(
    out_path: Path,
    name: str,
    fixed_mask: np.ndarray,
    initial_mri: np.ndarray,
    initial_mask: np.ndarray,
    refined_mri: np.ndarray,
    refined_mask: np.ndarray,
    initial_metrics: MaskMetrics,
    refined_metrics: MaskMetrics,
    shift: Sequence[int],
) -> None:
    fig, axes = plt.subplots(3, 4, figsize=(18, 13))
    fig.suptitle(
        f"V32.8 {name}: initial bbox-fit vs translation-refined | shift_lowres={list(shift)} | "
        f"Dice {initial_metrics.dice:.4f} -> {refined_metrics.dice:.4f}",
        fontsize=14,
    )
    labels = ["axis0/AP-coronal mid", "axis1/SI-horizontal mid", "axis2/LR-sagittal mid"]
    for row, axis in enumerate([0, 1, 2]):
        base_init = prepare_img2d(axis_mid(initial_mri, axis))
        base_ref = prepare_img2d(axis_mid(refined_mri, axis))
        fixed2 = prepare_img2d(axis_mid(fixed_mask, axis)).astype(bool)
        init2 = prepare_img2d(axis_mid(initial_mask, axis)).astype(bool)
        ref2 = prepare_img2d(axis_mid(refined_mask, axis)).astype(bool)

        ax = axes[row, 0]
        ax.imshow(base_init, cmap="gray")
        draw_contour(ax, fixed2, "yellow")
        draw_contour(ax, init2, "magenta")
        ax.set_title(f"initial: MRI + borders\n{labels[row]}")
        ax.axis("off")

        ax = axes[row, 1]
        ax.imshow(base_ref, cmap="gray")
        draw_contour(ax, fixed2, "yellow")
        draw_contour(ax, ref2, "cyan")
        ax.set_title("translation-refined: MRI + borders")
        ax.axis("off")

        ax = axes[row, 2]
        ax.imshow(np.zeros_like(fixed2, dtype=float), cmap="gray", vmin=0, vmax=1)
        overlay_mask(ax, np.zeros_like(fixed2), fixed2, alpha=0.35, color="yellow")
        overlay_mask(ax, np.zeros_like(ref2), ref2, alpha=0.35, color="cyan")
        draw_contour(ax, fixed2, "yellow")
        draw_contour(ax, ref2, "cyan")
        ax.set_title("fixed Paxinos mask + refined Waxholm mask")
        ax.axis("off")

        ax = axes[row, 3]
        ax.imshow(base_ref, cmap="gray")
        overlay_mask(ax, base_ref, fixed2, alpha=0.30, color="yellow")
        ax.set_title("refined MRI + Paxinos mask")
        ax.axis("off")

    fig.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: List[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def choose_recommendation(initial: MaskMetrics, refined: MaskMetrics) -> str:
    improvement = refined.dice - initial.dice
    if refined.dice >= 0.78 and refined.coverage_fixed >= 0.70 and improvement >= 0.03:
        return "translation refinement looks promising; build a separate refined test atlas, still not stable"
    if improvement >= 0.02 and refined.coverage_fixed > initial.coverage_fixed:
        return "minor improvement; consider refined test atlas only as diagnostic, then proper affine/deformable registration"
    return "translation refinement insufficient; stop bbox promotion and move to proper affine/deformable registration"


def main() -> int:
    project_root = project_root_from_argv()
    out_dir = project_root / OUT_REL
    safe_mkdir(out_dir)

    started = datetime.now().isoformat(timespec="seconds")
    target_path = project_root / TARGET_REL
    wax_mri_path = project_root / WAXHOLM_MRI_REL
    wax_mask_path = project_root / WAXHOLM_MASK_REL

    report: Dict[str, object] = {
        "generated_at": started,
        "passed": False,
        "project_root": str(project_root),
        "report_dir": str(out_dir),
        "purpose": "Low-resolution mask registration refinement after V32.7b anatomical orientation adjudication.",
        "target_annotation": str(target_path),
        "waxholm_mri": str(wax_mri_path),
        "waxholm_mask": str(wax_mask_path),
        "does_modify_or_install_atlas": False,
        "low_factor_target": LOW_FACTOR_TARGET,
        "low_factor_moving": LOW_FACTOR_MOVING,
        "candidate_policy": "Only V32.7b rank 1 and rank 2 are tested; ranks 3-8 were visually rejected.",
        "simpleitk": maybe_simpleitk_translation_note(),
        "errors": [],
    }

    try:
        missing = [str(p) for p in [target_path, wax_mri_path, wax_mask_path] if not p.exists()]
        if missing:
            raise FileNotFoundError("Missing required files: " + "; ".join(missing))

        fixed_annotation = downsample_proxy(target_path, LOW_FACTOR_TARGET)
        fixed_mask = (fixed_annotation > 0).astype(np.uint8)
        moving_mask_low = (downsample_proxy(wax_mask_path, LOW_FACTOR_MOVING) > 0).astype(np.uint8)
        moving_mri_low = normalize_uint16(downsample_proxy(wax_mri_path, LOW_FACTOR_MOVING))

        report["target"] = {
            "shape_full": list(nib.load(str(target_path)).shape[:3]),
            "shape_lowres": list(fixed_mask.shape),
            "orientation_full": orientation_of(target_path),
            "voxel_size_full": voxel_size_of(target_path),
            "mask_nonzero_fraction_lowres": float(fixed_mask.mean()),
        }
        report["waxholm"] = {
            "mri_shape_full": list(nib.load(str(wax_mri_path)).shape[:3]),
            "mask_shape_full": list(nib.load(str(wax_mask_path)).shape[:3]),
            "orientation_full": orientation_of(wax_mri_path),
            "voxel_size_full": voxel_size_of(wax_mri_path),
            "mask_nonzero_fraction_lowres": float(moving_mask_low.mean()),
        }

        results: List[CandidateResult] = []
        csv_rows: List[Dict[str, object]] = []
        top_shift_tables: Dict[str, List[Dict[str, object]]] = {}

        for cand in CANDIDATES:
            name = cand["name"]
            perm = cand["perm"]
            flips = cand["flips"]
            transformed_mask = apply_perm_flips(moving_mask_low, perm, flips)
            transformed_mri = apply_perm_flips(moving_mri_low, perm, flips)

            initial_mask_float = bbox_fit_to_fixed(transformed_mask, transformed_mask, fixed_mask, order=0)
            initial_mask = (initial_mask_float > 0.5).astype(np.uint8)
            initial_mri = bbox_fit_to_fixed(transformed_mri, transformed_mask, fixed_mask, order=1).astype(np.uint16)
            initial_m = metrics(fixed_mask, initial_mask)

            phase_shift = try_phase_correlation(fixed_mask, initial_mask)
            best_shift, best_m, top_shifts = best_translation_search(fixed_mask, initial_mask)
            top_shift_tables[name] = top_shifts

            refined_mask = integer_shift_3d(initial_mask, best_shift, fill_value=0).astype(np.uint8)
            refined_mri = integer_shift_3d(initial_mri, best_shift, fill_value=0).astype(np.uint16)
            refined_m = metrics(fixed_mask, refined_mask)
            preview_path = out_dir / f"v32_8_{name}_translation_refinement_preview.png"
            save_candidate_preview(
                preview_path,
                name=name,
                fixed_mask=fixed_mask,
                initial_mri=initial_mri,
                initial_mask=initial_mask,
                refined_mri=refined_mri,
                refined_mask=refined_mask,
                initial_metrics=initial_m,
                refined_metrics=refined_m,
                shift=best_shift,
            )

            recommendation = choose_recommendation(initial_m, refined_m)
            res = CandidateResult(
                rank=int(cand["rank"]),
                name=str(name),
                perm=list(perm),
                flips=list(flips),
                dice_v32_7b=float(cand["dice_v32_7b"]),
                initial_metrics=asdict(initial_m),
                best_translation_voxels_lowres=list(best_shift),
                refined_metrics=asdict(refined_m),
                improvement={
                    "dice_delta": float(refined_m.dice - initial_m.dice),
                    "coverage_fixed_delta": float(refined_m.coverage_fixed - initial_m.coverage_fixed),
                    "extra_fraction_delta": float(refined_m.extra_fraction - initial_m.extra_fraction),
                },
                preview=str(preview_path),
                recommendation=recommendation,
            )
            results.append(res)
            csv_rows.append({
                "rank": res.rank,
                "name": res.name,
                "perm": json.dumps(res.perm),
                "flips": json.dumps(res.flips),
                "dice_v32_7b": res.dice_v32_7b,
                "initial_dice": initial_m.dice,
                "refined_dice": refined_m.dice,
                "dice_delta": refined_m.dice - initial_m.dice,
                "initial_coverage_fixed": initial_m.coverage_fixed,
                "refined_coverage_fixed": refined_m.coverage_fixed,
                "coverage_delta": refined_m.coverage_fixed - initial_m.coverage_fixed,
                "initial_extra_fraction": initial_m.extra_fraction,
                "refined_extra_fraction": refined_m.extra_fraction,
                "extra_fraction_delta": refined_m.extra_fraction - initial_m.extra_fraction,
                "best_translation_voxels_lowres": json.dumps(best_shift),
                "phase_correlation_suggestion_voxels_lowres": json.dumps(phase_shift),
                "preview": str(preview_path),
                "recommendation": recommendation,
            })

        # Sort by refined Dice, but do not call it a stable winner.
        results.sort(key=lambda r: float(r.refined_metrics["dice"]), reverse=True)
        csv_rows.sort(key=lambda r: float(r["refined_dice"]), reverse=True)
        report["results"] = [asdict(r) for r in results]
        report["top_translation_candidates_by_rank"] = top_shift_tables

        csv_path = out_dir / "v32_8_translation_refinement_results.csv"
        write_csv(csv_path, csv_rows, [
            "rank", "name", "perm", "flips", "dice_v32_7b",
            "initial_dice", "refined_dice", "dice_delta",
            "initial_coverage_fixed", "refined_coverage_fixed", "coverage_delta",
            "initial_extra_fraction", "refined_extra_fraction", "extra_fraction_delta",
            "best_translation_voxels_lowres", "phase_correlation_suggestion_voxels_lowres",
            "preview", "recommendation",
        ])
        report["main_output_files"] = [
            str(csv_path),
            str(out_dir / "v32_8_proper_registration_prep_report.json"),
            str(out_dir / "v32_8_proper_registration_prep_summary.txt"),
            *[r.preview for r in results],
        ]

        best = results[0]
        report["best_by_refined_translation_dice"] = asdict(best)
        report["passed"] = True
        report["global_conclusion"] = (
            "If translation refinement does not clearly improve Dice/coverage and visual landmark fit, "
            "do not build another bbox-derived stable atlas. Move to real affine/deformable registration."
        )

    except Exception as e:
        report["errors"].append({"error": str(e), "traceback": traceback.format_exc()})

    json_path = out_dir / "v32_8_proper_registration_prep_report.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    summary_path = out_dir / "v32_8_proper_registration_prep_summary.txt"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write("V32.8 Proper Registration Prep\n")
        f.write("========================================================================\n")
        f.write(f"Generated: {report['generated_at']}\n")
        f.write(f"PASSED: {report.get('passed')}\n")
        f.write(f"Project root: {report['project_root']}\n")
        f.write(f"Report dir: {report['report_dir']}\n")
        f.write("\nPurpose:\n")
        f.write("- Low-resolution translation refinement after V32.7b selected Rank 1/2 as the only plausible Waxholm candidates.\n")
        f.write("- Preview-only. No stable atlas is modified. No atlas is installed.\n")
        f.write("- This is not a final nonlinear registration.\n")
        f.write("\nBackend status:\n")
        f.write(f"- scipy available: {SCIPY_AVAILABLE}\n")
        f.write(f"- SimpleITK available: {SIMPLEITK_AVAILABLE} version={SIMPLEITK_VERSION}\n")
        if report.get("errors"):
            f.write("\nERRORS:\n")
            for err in report["errors"]:  # type: ignore[index]
                f.write(f"- {err['error']}\n")
        else:
            f.write("\nResults:\n")
            for r in report.get("results", []):  # type: ignore[assignment]
                im = r["initial_metrics"]
                rm = r["refined_metrics"]
                imp = r["improvement"]
                f.write(f"- {r['name']} rank={r['rank']} perm={r['perm']} flips={r['flips']}\n")
                f.write(f"  initial Dice={im['dice']:.6f} coverage={im['coverage_fixed']:.6f} extra={im['extra_fraction']:.6f}\n")
                f.write(f"  refined Dice={rm['dice']:.6f} coverage={rm['coverage_fixed']:.6f} extra={rm['extra_fraction']:.6f}\n")
                f.write(f"  delta Dice={imp['dice_delta']:.6f} delta coverage={imp['coverage_fixed_delta']:.6f} delta extra={imp['extra_fraction_delta']:.6f}\n")
                f.write(f"  best lowres translation: {r['best_translation_voxels_lowres']}\n")
                f.write(f"  preview: {r['preview']}\n")
                f.write(f"  recommendation: {r['recommendation']}\n")
            f.write("\nMain output files:\n")
            for p in report.get("main_output_files", []):
                f.write(f"- {p}\n")
            f.write("\nConclusion rule:\n")
            f.write("- If translation refinement gives only tiny improvement, stop bbox/translation promotion.\n")
            f.write("- The next meaningful step is real affine/deformable registration with SimpleITK, ANTs, Elastix, or BigWarp.\n")

    print(summary_path.read_text(encoding="utf-8", errors="replace"))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
