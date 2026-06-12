#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V32.7b Waxholm anatomical orientation adjudication diagnostic.

Purpose
-------
This is a preview-only diagnostic step for the rat-paxinos-brainglobe-builder.
It does NOT modify or install any atlas.

It tests Waxholm -> Paxinos orientation candidates with more anatomical context
than the previous mask-only Dice ranking:
- recomputes all 48 axis-permutation / flip candidates on low-resolution masks
- reports Dice/Jaccard/coverage, but treats them as weak screening only
- creates anatomical preview panels using Waxholm MRI under Paxinos borders
- creates Paxinos landmark overlays for regions such as cortex, cerebellum,
  ventricles, hippocampal formation, striatum/basal ganglia, olfactory labels
- creates AP/coronal series panels so anterior/posterior flips can be judged
  visually instead of promoted based on mask Dice alone.

The point is to catch exactly the failure mode where the outer mask overlaps
reasonably, but the internal anatomy is mirrored, upside down, or AP-swapped.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np

try:
    import nibabel as nib
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"ERROR: nibabel is required but could not be imported: {exc}")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"ERROR: matplotlib is required but could not be imported: {exc}")

try:
    from skimage.transform import resize
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"ERROR: scikit-image is required but could not be imported: {exc}")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw" / "bluebrainheadmodels"
OFFICIAL = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
REPORT_DIR = PROJECT_ROOT / "reports" / "v32_7b_waxholm_anatomical_orientation_diag"

TARGET_ANNOTATION = OFFICIAL / "annotation.nii.gz"
STRUCTURES_JSON = OFFICIAL / "structures.json"
WAXHOLM_MRI = RAW / "Waxholm_Atlas_MRI.nii.gz"
WAXHOLM_MASK = RAW / "Waxholm_Atlas_Mask.nii.gz"

LOW_FACTOR_TARGET = 4
LOW_FACTOR_MOVING = 4
TOP_N = 8
SERIES_N = 7

# Paxinos V32.2 axis model after validated ABBA orientation.
# Shape [608, 286, 409] = [AP, SI, LR] display-space model.
AXIS_MODEL = {
    0: "AP axis (coronal slice index; anterior/posterior ordering must be judged visually)",
    1: "SI axis (horizontal slice index; superior/inferior ordering must be judged visually)",
    2: "LR axis (sagittal slice index; left/right ordering must be judged visually)",
}


@dataclass
class Candidate:
    rank: int
    perm: Tuple[int, int, int]
    flips: Tuple[bool, bool, bool]
    dice: float
    jaccard: float
    coverage_fixed: float
    extra_fraction: float
    intersection_voxels: int
    fixed_voxels: int
    moving_voxels_after_fit: int
    moving_bbox_after_transform_min: List[int]
    moving_bbox_after_transform_max: List[int]
    fixed_bbox_min: List[int]
    fixed_bbox_max: List[int]


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def ensure_exists(path: Path, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"ERROR: required {label} not found: {path}")


def stats(arr: np.ndarray) -> Dict[str, object]:
    finite = np.isfinite(arr)
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": float(np.nanmin(arr)) if arr.size else None,
        "max": float(np.nanmax(arr)) if arr.size else None,
        "mean": float(np.nanmean(arr)) if arr.size else None,
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size) if arr.size else None,
        "finite_fraction": float(np.count_nonzero(finite) / arr.size) if arr.size else None,
    }


def robust_uint16(arr: np.ndarray, mask_positive: bool = False) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    finite = np.isfinite(a)
    if mask_positive:
        finite &= a > 0
    if not np.any(finite):
        return np.zeros(a.shape, dtype=np.uint16)
    lo = float(np.percentile(a[finite], 1.0))
    hi = float(np.percentile(a[finite], 99.5))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(a[finite]))
        hi = float(np.nanmax(a[finite]))
    if hi <= lo:
        return np.zeros(a.shape, dtype=np.uint16)
    out = (a - lo) / (hi - lo)
    out = np.clip(out, 0, 1)
    out[~np.isfinite(out)] = 0
    return (out * 65535).astype(np.uint16)


def read_downsampled_nifti(path: Path, factor: int, dtype=None) -> Tuple[nib.Nifti1Image, np.ndarray]:
    img = nib.load(str(path))
    slicer = tuple(slice(None, None, factor) for _ in range(3))
    arr = np.asanyarray(img.dataobj[slicer])
    if dtype is not None:
        arr = arr.astype(dtype, copy=False)
    return img, arr


def bbox(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray] | Tuple[None, None]:
    pts = np.argwhere(mask)
    if pts.size == 0:
        return None, None
    return pts.min(axis=0), pts.max(axis=0)


def transform_axes(arr: np.ndarray, perm: Sequence[int], flips: Sequence[bool]) -> np.ndarray:
    out = np.transpose(arr, perm)
    for ax, do_flip in enumerate(flips):
        if do_flip:
            out = np.flip(out, axis=ax)
    return np.ascontiguousarray(out)


def fit_to_fixed_bbox(
    moving_arr: np.ndarray,
    moving_mask: np.ndarray,
    fixed_shape: Tuple[int, int, int],
    fixed_bbox_min: np.ndarray,
    fixed_bbox_max: np.ndarray,
    order: int,
    preserve_range: bool = True,
) -> np.ndarray:
    """Crop moving to its mask bbox, resize into fixed mask bbox, paste into fixed volume."""
    m_min, m_max = bbox(moving_mask)
    out = np.zeros(fixed_shape, dtype=np.float32)
    if m_min is None:
        return out

    m_slices = tuple(slice(int(m_min[i]), int(m_max[i]) + 1) for i in range(3))
    f_slices = tuple(slice(int(fixed_bbox_min[i]), int(fixed_bbox_max[i]) + 1) for i in range(3))
    target_shape = tuple(int(fixed_bbox_max[i] - fixed_bbox_min[i] + 1) for i in range(3))

    cropped = moving_arr[m_slices]
    if any(s <= 1 for s in cropped.shape) or any(s <= 1 for s in target_shape):
        return out

    resized = resize(
        cropped,
        target_shape,
        order=order,
        preserve_range=preserve_range,
        anti_aliasing=(order > 0),
    ).astype(np.float32)
    out[f_slices] = resized
    return out


def dice_metrics(fixed: np.ndarray, moving: np.ndarray) -> Dict[str, float | int]:
    f = fixed.astype(bool)
    m = moving.astype(bool)
    inter = int(np.count_nonzero(f & m))
    f_n = int(np.count_nonzero(f))
    m_n = int(np.count_nonzero(m))
    union = int(np.count_nonzero(f | m))
    dice = 2 * inter / (f_n + m_n) if (f_n + m_n) else 0.0
    jaccard = inter / union if union else 0.0
    coverage_fixed = inter / f_n if f_n else 0.0
    extra_fraction = max(m_n - inter, 0) / m_n if m_n else 0.0
    return {
        "dice": float(dice),
        "jaccard": float(jaccard),
        "coverage_fixed": float(coverage_fixed),
        "extra_fraction": float(extra_fraction),
        "intersection_voxels": inter,
        "fixed_voxels": f_n,
        "moving_voxels_after_fit": m_n,
    }


def binary_border(mask: np.ndarray) -> np.ndarray:
    """Cheap 3D border approximation without scipy dependency."""
    m = mask.astype(bool)
    er = m.copy()
    for axis in range(3):
        plus = np.roll(m, 1, axis=axis)
        minus = np.roll(m, -1, axis=axis)
        # Clear wraparound contributions.
        index0 = [slice(None)] * 3
        index0[axis] = 0
        plus[tuple(index0)] = False
        index1 = [slice(None)] * 3
        index1[axis] = -1
        minus[tuple(index1)] = False
        er &= plus & minus
    return m & ~er


def load_structures(path: Path) -> List[Dict[str, object]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_landmark_masks(annotation_low: np.ndarray, structures: List[Dict[str, object]]) -> Tuple[np.ndarray, Dict[str, Dict[str, object]]]:
    """Return integer category mask and category metadata."""
    # Category order matters. Later categories can overwrite earlier ones.
    categories = {
        1: {
            "name": "cortex",
            "color": "#f7b267",
            "keywords": ["cortex", "cortical", "isocortex", "neocortex"],
            "container_ids": [998110],
        },
        2: {
            "name": "hippocampal formation",
            "color": "#70d6ff",
            "keywords": ["hippoc", "dentate", "subiculum", "fimbria", "ammon"],
            "container_ids": [],
        },
        3: {
            "name": "striatum / basal ganglia",
            "color": "#ff70a6",
            "keywords": ["striat", "caudate", "putamen", "accumbens", "globus", "pallid", "basal ganglia"],
            "container_ids": [998150],
        },
        4: {
            "name": "cerebellum",
            "color": "#b8f2e6",
            "keywords": ["cerebell"],
            "container_ids": [998400],
        },
        5: {
            "name": "ventricular system",
            "color": "#ffffff",
            "keywords": ["ventricle", "aqueduct", "central canal"],
            "container_ids": [998500],
        },
        6: {
            "name": "olfactory labels",
            "color": "#caffbf",
            "keywords": ["olfactory", "olfact"],
            "container_ids": [],
        },
        7: {
            "name": "amygdaloid complex",
            "color": "#ffd6a5",
            "keywords": ["amygd", "amygdala"],
            "container_ids": [998140],
        },
    }

    cat_ids: Dict[int, List[int]] = {k: [] for k in categories}
    for s in structures:
        sid = int(s.get("id"))
        text = (str(s.get("name", "")) + " " + str(s.get("acronym", ""))).lower()
        path_ids = [int(x) for x in s.get("structure_id_path", [])]
        for cid, meta in categories.items():
            if any(container in path_ids for container in meta["container_ids"]):
                cat_ids[cid].append(sid)
                continue
            if any(kw in text for kw in meta["keywords"]):
                cat_ids[cid].append(sid)

    present_ids = set(int(x) for x in np.unique(annotation_low) if int(x) != 0)
    landmark = np.zeros(annotation_low.shape, dtype=np.uint8)
    for cid, ids in cat_ids.items():
        ids_present = sorted(set(ids) & present_ids)
        categories[cid]["structure_ids_total"] = len(set(ids))
        categories[cid]["structure_ids_present_lowres"] = len(ids_present)
        if ids_present:
            landmark[np.isin(annotation_low, ids_present)] = cid
    return landmark, categories


def show_slice(arr: np.ndarray, axis: int, index: int) -> np.ndarray:
    sl = np.take(arr, int(index), axis=axis)
    # Use origin lower-ish consistency? For image panels, rotate only by imshow default not data transformation.
    return np.asarray(sl)


def slice_indices_from_mask(mask: np.ndarray, axis: int, n: int = SERIES_N) -> List[int]:
    coords = np.where(mask)[axis]
    if coords.size == 0:
        return [mask.shape[axis] // 2]
    lo, hi = int(coords.min()), int(coords.max())
    if n <= 1:
        return [(lo + hi) // 2]
    vals = np.linspace(lo, hi, n)
    return sorted(set(int(round(v)) for v in vals))


def draw_mask_border(ax, mask_slice: np.ndarray, color: str = "cyan", lw: float = 0.6, alpha: float = 0.85):
    if np.any(mask_slice):
        ax.contour(mask_slice.astype(float), levels=[0.5], colors=color, linewidths=lw, alpha=alpha)


def landmark_cmap(categories: Dict[int, Dict[str, object]]) -> ListedColormap:
    colors = ["#00000000"]
    for i in range(1, 8):
        colors.append(str(categories.get(i, {}).get("color", "#ffffff")))
    return ListedColormap(colors)


def make_midplane_panel(
    out_path: Path,
    title: str,
    mri: np.ndarray,
    fixed_mask: np.ndarray,
    moving_mask: np.ndarray,
    landmarks: np.ndarray,
    categories: Dict[int, Dict[str, object]],
):
    cmap_land = landmark_cmap(categories)
    fig, axes = plt.subplots(3, 4, figsize=(18, 13), constrained_layout=True)
    axes = np.asarray(axes)
    plane_names = {
        0: "axis0 coronal/AP index",
        1: "axis1 horizontal/SI index",
        2: "axis2 sagittal/LR index",
    }
    for r, axis in enumerate([0, 1, 2]):
        idx = fixed_mask.shape[axis] // 2
        img_sl = show_slice(mri, axis, idx)
        fixed_sl = show_slice(fixed_mask, axis, idx)
        moving_sl = show_slice(moving_mask, axis, idx)
        land_sl = show_slice(landmarks, axis, idx)

        ax = axes[r, 0]
        ax.imshow(img_sl, cmap="gray")
        ax.set_title(f"Waxholm MRI {plane_names[axis]} mid={idx}")
        ax.axis("off")

        ax = axes[r, 1]
        ax.imshow(img_sl, cmap="gray")
        draw_mask_border(ax, fixed_sl, color="yellow", lw=0.8)
        draw_mask_border(ax, moving_sl, color="magenta", lw=0.6)
        ax.set_title("MRI + Paxinos border (yellow) + Waxholm mask border (magenta)")
        ax.axis("off")

        ax = axes[r, 2]
        ax.imshow(fixed_sl.astype(float), cmap="gray", alpha=0.25)
        ax.imshow(moving_sl.astype(float), cmap="magma", alpha=0.35)
        draw_mask_border(ax, fixed_sl, color="yellow", lw=0.8)
        draw_mask_border(ax, moving_sl, color="cyan", lw=0.8)
        ax.set_title("Mask adjudication: fixed + moving borders")
        ax.axis("off")

        ax = axes[r, 3]
        ax.imshow(img_sl, cmap="gray")
        masked_land = np.ma.masked_where(land_sl == 0, land_sl)
        ax.imshow(masked_land, cmap=cmap_land, vmin=0, vmax=7, alpha=0.48, interpolation="nearest")
        draw_mask_border(ax, fixed_sl, color="white", lw=0.5)
        ax.set_title("MRI + Paxinos landmark groups")
        ax.axis("off")

    fig.suptitle(title, fontsize=14)
    # Compact legend text in figure margin.
    legend_lines = []
    for cid in range(1, 8):
        meta = categories.get(cid, {})
        legend_lines.append(f"{cid}: {meta.get('name')} (ids present={meta.get('structure_ids_present_lowres', 0)})")
    fig.text(0.01, 0.01, " | ".join(legend_lines), fontsize=8)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def make_axis_series_panel(
    out_path: Path,
    title: str,
    axis: int,
    mri: np.ndarray,
    fixed_mask: np.ndarray,
    moving_mask: np.ndarray,
    landmarks: np.ndarray,
    categories: Dict[int, Dict[str, object]],
):
    idxs = slice_indices_from_mask(fixed_mask, axis=axis, n=SERIES_N)
    cmap_land = landmark_cmap(categories)
    fig, axes = plt.subplots(2, len(idxs), figsize=(3.1 * len(idxs), 6.4), constrained_layout=True)
    if len(idxs) == 1:
        axes = np.asarray(axes).reshape(2, 1)
    for c, idx in enumerate(idxs):
        img_sl = show_slice(mri, axis, idx)
        fixed_sl = show_slice(fixed_mask, axis, idx)
        moving_sl = show_slice(moving_mask, axis, idx)
        land_sl = show_slice(landmarks, axis, idx)

        ax = axes[0, c]
        ax.imshow(img_sl, cmap="gray")
        draw_mask_border(ax, fixed_sl, color="yellow", lw=0.8)
        draw_mask_border(ax, moving_sl, color="magenta", lw=0.6)
        ax.set_title(f"idx {idx}\nMRI + borders", fontsize=9)
        ax.axis("off")

        ax = axes[1, c]
        ax.imshow(img_sl, cmap="gray")
        masked_land = np.ma.masked_where(land_sl == 0, land_sl)
        ax.imshow(masked_land, cmap=cmap_land, vmin=0, vmax=7, alpha=0.48, interpolation="nearest")
        draw_mask_border(ax, fixed_sl, color="white", lw=0.5)
        ax.set_title("landmarks", fontsize=9)
        ax.axis("off")
    fig.suptitle(title + f" | {AXIS_MODEL[axis]}", fontsize=13)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def make_pair_compare_panel(
    out_path: Path,
    candidates_data: List[Tuple[Candidate, np.ndarray, np.ndarray]],
    fixed_mask: np.ndarray,
):
    # Compare top candidates in one row using mid-slices through all axes.
    n = len(candidates_data)
    fig, axes = plt.subplots(n, 3, figsize=(12, 3.4 * n), constrained_layout=True)
    if n == 1:
        axes = np.asarray(axes).reshape(1, 3)
    for r, (cand, mri, moving_mask) in enumerate(candidates_data):
        for c, axis in enumerate([0, 1, 2]):
            idx = fixed_mask.shape[axis] // 2
            img_sl = show_slice(mri, axis, idx)
            fixed_sl = show_slice(fixed_mask, axis, idx)
            moving_sl = show_slice(moving_mask, axis, idx)
            ax = axes[r, c]
            ax.imshow(img_sl, cmap="gray")
            draw_mask_border(ax, fixed_sl, color="yellow", lw=0.8)
            draw_mask_border(ax, moving_sl, color="magenta", lw=0.6)
            ax.set_title(
                f"rank {cand.rank} axis{axis}\ndice={cand.dice:.4f} perm={list(cand.perm)} flips={list(cand.flips)}",
                fontsize=9,
            )
            ax.axis("off")
    fig.suptitle("Top candidate anatomical orientation comparison: yellow=Paxinos, magenta=Waxholm", fontsize=13)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def save_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_exists(TARGET_ANNOTATION, "target Paxinos annotation")
    ensure_exists(STRUCTURES_JSON, "Paxinos structures.json")
    ensure_exists(WAXHOLM_MRI, "Waxholm MRI")
    ensure_exists(WAXHOLM_MASK, "Waxholm mask")

    target_img, target_ann_low = read_downsampled_nifti(TARGET_ANNOTATION, LOW_FACTOR_TARGET, dtype=np.uint32)
    fixed_mask = target_ann_low > 0
    fixed_min, fixed_max = bbox(fixed_mask)
    if fixed_min is None:
        raise SystemExit("ERROR: target annotation mask is empty after downsampling")

    mri_img, moving_mri_low_raw = read_downsampled_nifti(WAXHOLM_MRI, LOW_FACTOR_MOVING)
    mask_img, moving_mask_low_raw = read_downsampled_nifti(WAXHOLM_MASK, LOW_FACTOR_MOVING)
    moving_mri_low_raw = robust_uint16(moving_mri_low_raw, mask_positive=False).astype(np.float32)
    moving_mask_low_raw = moving_mask_low_raw > 0

    structures = load_structures(STRUCTURES_JSON)
    landmarks, landmark_categories = build_landmark_masks(target_ann_low, structures)
    fixed_border = binary_border(fixed_mask)

    candidates: List[Candidate] = []
    fitted_cache: Dict[Tuple[Tuple[int, int, int], Tuple[bool, bool, bool]], Tuple[np.ndarray, np.ndarray]] = {}

    for perm in itertools.permutations([0, 1, 2]):
        for flips in itertools.product([False, True], repeat=3):
            t_mask = transform_axes(moving_mask_low_raw, perm, flips)
            t_mri = transform_axes(moving_mri_low_raw, perm, flips)
            fitted_mask_float = fit_to_fixed_bbox(
                t_mask.astype(np.float32),
                t_mask,
                fixed_mask.shape,
                fixed_min,
                fixed_max,
                order=0,
            )
            fitted_mask = fitted_mask_float > 0.5
            fitted_mri = fit_to_fixed_bbox(
                t_mri,
                t_mask,
                fixed_mask.shape,
                fixed_min,
                fixed_max,
                order=1,
            )
            metrics = dice_metrics(fixed_mask, fitted_mask)
            m_min, m_max = bbox(t_mask)
            cand = Candidate(
                rank=0,
                perm=tuple(int(x) for x in perm),
                flips=tuple(bool(x) for x in flips),
                dice=float(metrics["dice"]),
                jaccard=float(metrics["jaccard"]),
                coverage_fixed=float(metrics["coverage_fixed"]),
                extra_fraction=float(metrics["extra_fraction"]),
                intersection_voxels=int(metrics["intersection_voxels"]),
                fixed_voxels=int(metrics["fixed_voxels"]),
                moving_voxels_after_fit=int(metrics["moving_voxels_after_fit"]),
                moving_bbox_after_transform_min=[int(x) for x in (m_min.tolist() if m_min is not None else [])],
                moving_bbox_after_transform_max=[int(x) for x in (m_max.tolist() if m_max is not None else [])],
                fixed_bbox_min=[int(x) for x in fixed_min.tolist()],
                fixed_bbox_max=[int(x) for x in fixed_max.tolist()],
            )
            candidates.append(cand)
            fitted_cache[(cand.perm, cand.flips)] = (fitted_mri, fitted_mask)

    candidates.sort(key=lambda c: c.dice, reverse=True)
    for i, c in enumerate(candidates, start=1):
        c.rank = i

    top = candidates[:TOP_N]
    top_preview_paths: List[str] = []
    top_series_paths: List[str] = []
    top_pair_data: List[Tuple[Candidate, np.ndarray, np.ndarray]] = []

    for cand in top:
        fitted_mri, fitted_mask = fitted_cache[(cand.perm, cand.flips)]
        top_pair_data.append((cand, fitted_mri, fitted_mask))

        title = (
            f"V32.7b rank {cand.rank} | dice={cand.dice:.4f} | "
            f"perm={list(cand.perm)} flips={list(cand.flips)}\n"
            "Diagnostic only: judge internal anatomy, AP/SI/LR orientation, and mirror errors visually."
        )
        mid_path = REPORT_DIR / f"v32_7b_rank_{cand.rank:02d}_midplane_landmark_adjudication.png"
        make_midplane_panel(
            mid_path,
            title,
            fitted_mri,
            fixed_mask,
            fitted_mask,
            landmarks,
            landmark_categories,
        )
        top_preview_paths.append(str(mid_path))

        for axis, axis_name in [(0, "axis0_AP_coronal_series"), (1, "axis1_SI_horizontal_series"), (2, "axis2_LR_sagittal_series")]:
            series_path = REPORT_DIR / f"v32_7b_rank_{cand.rank:02d}_{axis_name}.png"
            make_axis_series_panel(
                series_path,
                title=f"V32.7b rank {cand.rank} {axis_name} | perm={list(cand.perm)} flips={list(cand.flips)}",
                axis=axis,
                mri=fitted_mri,
                fixed_mask=fixed_mask,
                moving_mask=fitted_mask,
                landmarks=landmarks,
                categories=landmark_categories,
            )
            top_series_paths.append(str(series_path))

    compare_path = REPORT_DIR / "v32_7b_top_candidates_midplane_comparison.png"
    make_pair_compare_panel(compare_path, top_pair_data[:min(8, len(top_pair_data))], fixed_mask)

    ranked_rows = []
    for c in candidates:
        row = asdict(c)
        row["perm"] = json.dumps(list(c.perm))
        row["flips"] = json.dumps(list(c.flips))
        row["moving_bbox_after_transform_min"] = json.dumps(c.moving_bbox_after_transform_min)
        row["moving_bbox_after_transform_max"] = json.dumps(c.moving_bbox_after_transform_max)
        row["fixed_bbox_min"] = json.dumps(c.fixed_bbox_min)
        row["fixed_bbox_max"] = json.dumps(c.fixed_bbox_max)
        ranked_rows.append(row)
    save_csv(REPORT_DIR / "v32_7b_orientation_candidates_ranked.csv", ranked_rows)

    # Landmark summary csv
    landmark_rows = []
    for cid, meta in landmark_categories.items():
        landmark_rows.append({
            "category_id": cid,
            "name": meta.get("name"),
            "structure_ids_total": meta.get("structure_ids_total", 0),
            "structure_ids_present_lowres": meta.get("structure_ids_present_lowres", 0),
            "voxel_fraction_lowres": float(np.count_nonzero(landmarks == cid) / landmarks.size),
        })
    save_csv(REPORT_DIR / "v32_7b_paxinos_landmark_categories.csv", landmark_rows)

    report = {
        "generated_at": now(),
        "passed": True,
        "project_root": str(PROJECT_ROOT),
        "report_dir": str(REPORT_DIR),
        "purpose": "Anatomical orientation adjudication before any Waxholm reference test atlas promotion.",
        "target_annotation": str(TARGET_ANNOTATION),
        "waxholm_mri": str(WAXHOLM_MRI),
        "waxholm_mask": str(WAXHOLM_MASK),
        "low_factor_target": LOW_FACTOR_TARGET,
        "low_factor_moving": LOW_FACTOR_MOVING,
        "axis_model": AXIS_MODEL,
        "target": {
            "shape_full": list(target_img.shape),
            "shape_lowres": list(target_ann_low.shape),
            "orientation_full": "".join(nib.aff2axcodes(target_img.affine)),
            "voxel_size_full": [float(x) for x in target_img.header.get_zooms()[:3]],
            "mask_stats_lowres": stats(fixed_mask.astype(np.uint8)),
            "fixed_bbox_lowres_min": [int(x) for x in fixed_min.tolist()],
            "fixed_bbox_lowres_max": [int(x) for x in fixed_max.tolist()],
        },
        "waxholm": {
            "mri_shape_full": list(mri_img.shape),
            "mri_orientation_full": "".join(nib.aff2axcodes(mri_img.affine)),
            "mri_voxel_size_full": [float(x) for x in mri_img.header.get_zooms()[:3]],
            "mask_shape_full": list(mask_img.shape),
            "mask_orientation_full": "".join(nib.aff2axcodes(mask_img.affine)),
            "mask_voxel_size_full": [float(x) for x in mask_img.header.get_zooms()[:3]],
            "mri_stats_lowres": stats(moving_mri_low_raw),
            "mask_stats_lowres": stats(moving_mask_low_raw.astype(np.uint8)),
        },
        "landmark_categories": landmark_categories,
        "best_candidate_by_mask_dice": asdict(candidates[0]),
        "top_candidates": [asdict(c) for c in top],
        "preview_paths": {
            "top_candidates_midplane_comparison": str(compare_path),
            "top_midplane_landmark_panels": top_preview_paths,
            "top_axis_series_panels": top_series_paths,
        },
        "main_output_files": [
            "v32_7b_orientation_candidates_ranked.csv",
            "v32_7b_paxinos_landmark_categories.csv",
            "v32_7b_waxholm_anatomical_orientation_diag_report.json",
            "v32_7b_waxholm_anatomical_orientation_diag_summary.txt",
            "v32_7b_top_candidates_midplane_comparison.png",
            "v32_7b_rank_XX_midplane_landmark_adjudication.png",
            "v32_7b_rank_XX_axis0_AP_coronal_series.png",
            "v32_7b_rank_XX_axis1_SI_horizontal_series.png",
            "v32_7b_rank_XX_axis2_LR_sagittal_series.png",
        ],
        "interpretation_notes": [
            "Mask Dice remains only a weak filter. It cannot decide whether internal anatomy is mirrored or AP-swapped.",
            "Use the landmark and axis-series panels to judge cerebellum/posterior, olfactory/anterior, ventricles, cortex, hippocampal formation, and gross LR/SI orientation.",
            "Do not build or promote a Waxholm-reference test atlas until one candidate is anatomically plausible across the AP, SI, and LR series.",
        ],
    }

    with open(REPORT_DIR / "v32_7b_waxholm_anatomical_orientation_diag_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    summary_lines = [
        "V32.7b Waxholm anatomical orientation adjudication",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        "PASSED: True",
        "",
        f"Target annotation: {TARGET_ANNOTATION}",
        f"Waxholm MRI:       {WAXHOLM_MRI}",
        f"Waxholm mask:      {WAXHOLM_MASK}",
        "",
        "This diagnostic exists because mask Dice alone can pick a mirrored or anatomically upside-down candidate.",
        "It is preview-only and does not modify/install atlas data.",
        "",
        "Axis model used for oriented Paxinos target:",
    ]
    for ax, desc in AXIS_MODEL.items():
        summary_lines.append(f"- axis {ax}: {desc}")
    summary_lines += [
        "",
        "Best candidates by mask Dice only:",
    ]
    for c in top:
        summary_lines.append(
            f"- rank {c.rank}: dice={c.dice:.6f} jaccard={c.jaccard:.6f} "
            f"coverage_fixed={c.coverage_fixed:.6f} extra_fraction={c.extra_fraction:.6f} "
            f"perm={list(c.perm)} flips={list(c.flips)}"
        )
    summary_lines += [
        "",
        "Landmark categories found in Paxinos lowres annotation:",
    ]
    for row in landmark_rows:
        summary_lines.append(
            f"- {row['category_id']}: {row['name']} | ids_present={row['structure_ids_present_lowres']} "
            f"voxel_fraction={row['voxel_fraction_lowres']:.6f}"
        )
    summary_lines += [
        "",
        "Main preview files:",
        f"- {compare_path}",
    ]
    for p in top_preview_paths[:TOP_N]:
        summary_lines.append(f"- {p}")
    summary_lines += [
        "",
        "Recommended upload for review:",
        "- v32_7b_waxholm_anatomical_orientation_diag_summary.txt",
        "- v32_7b_waxholm_anatomical_orientation_diag_report.json",
        "- v32_7b_orientation_candidates_ranked.csv",
        "- v32_7b_paxinos_landmark_categories.csv",
        "- v32_7b_top_candidates_midplane_comparison.png",
        "- v32_7b_rank_01_midplane_landmark_adjudication.png",
        "- v32_7b_rank_01_axis0_AP_coronal_series.png",
        "- v32_7b_rank_01_axis2_LR_sagittal_series.png",
        "- same files for rank_02/rank_03 if they look competitive",
        "",
        "Conclusion rule:",
        "- If a candidate looks anatomically plausible across AP/SI/LR series, build a separate test atlas from that candidate.",
        "- If all candidates show mirror/AP/SI errors, stop bbox-fit promotion and move to proper registration.",
    ]
    (REPORT_DIR / "v32_7b_waxholm_anatomical_orientation_diag_summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("V32.7b diagnostic completed.")
    print(f"Report dir: {REPORT_DIR}")
    print(f"Best mask-Dice candidate: rank 1 dice={candidates[0].dice:.4f}, perm={list(candidates[0].perm)}, flips={list(candidates[0].flips)}")
    print("IMPORTANT: mask Dice is not final. Review the anatomical landmark panels before promoting anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
