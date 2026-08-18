"""V53 local landmark-guided registration for optional Ch03 WHS/Nissl data.

This module intentionally leaves the stable Paxinos/ABBA builder and annotation assets
untouched. Runtime products are confined to resources/optional_ch03 and
reports/v53_ch03_landmarks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import nibabel as nib
import numpy as np
import tifffile
from scipy import ndimage
from scipy.interpolate import RBFInterpolator
from skimage import exposure, filters, transform
from skimage.registration import optical_flow_tvl1, phase_cross_correlation


def get_pyplot():
    """Load matplotlib only for commands that actually write PNG QC figures."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL_DIR = ROOT / "resources" / "optional_ch03"
REPORT_DIR = ROOT / "reports" / "v53_ch03_landmarks"
CSV_PATH = OPTIONAL_DIR / "waxholm_to_paxinos_landmarks.csv"
REGION_CSV_PATH = OPTIONAL_DIR / "waxholm_to_paxinos_region_corrections.csv"
BREGMA_CSV_PATH = OPTIONAL_DIR / "waxholm_to_paxinos_bregma_ap_map.csv"
SOURCE_PATH = Path(os.environ.get("V53_WHS_NISSL_SOURCE", r"C:\Users\49152\.brainglobe\whs_sd_rat_39um_v1.2\reference.tiff"))
WHS_ANNOTATION_PATH = Path(os.environ.get("V53_WHS_ANNOTATION", r"C:\Users\49152\.brainglobe\whs_sd_rat_39um_v1.2\annotation.tiff"))
ACTIVE_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference.tiff"
AFFINE_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference_landmarks_affine.tiff"
WARP_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference_landmarks_warp.tiff"
WHS_LABEL_AFFINE_PATH = OPTIONAL_DIR / "waxholm_annotation_labels_affine.tiff"
WHS_LABEL_WARP_PATH = OPTIONAL_DIR / "waxholm_annotation_labels_warp.tiff"
ATLAS_CANDIDATES = [
    ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um",
    ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um_v1.0",
    ROOT / "data" / "output" / "brainglobe_provisional" / "paxinos_watson_rat_40um",
    ROOT / "data" / "output" / "brainglobe_provisional" / "paxinos_watson_rat_40um_v1.0",
    Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0",
    Path.home() / ".brainglobe" / "paxinos_watson_rat_40um",
]
REPORT_JSON = REPORT_DIR / "v53_landmark_report.json"
TARGET_SHAPE = (608, 286, 409)  # AP, SI, LR
PERM = (0, 2, 1)
PRE_RESIZE_ROT90 = -1
TARGET_FLIPS = (0,)
COLUMNS = ["enabled", "name", "fixed_ap", "fixed_si", "fixed_lr", "moving_ap", "moving_si", "moving_lr", "weight", "notes"]
REGION_COLUMNS = ["enabled", "name", "target_ap", "target_si", "target_lr", "current_ap", "current_si", "current_lr", "radius", "weight", "notes"]
BREGMA_COLUMNS = ["enabled", "name", "bregma_mm", "fixed_ap", "moving_ap", "weight", "notes"]


@dataclass
class LandmarkSet:
    names: list[str]
    fixed: np.ndarray
    moving: np.ndarray
    weights: np.ndarray


def ensure_dirs() -> None:
    OPTIONAL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(update: dict) -> None:
    ensure_dirs()
    base = {}
    if REPORT_JSON.exists():
        base = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    base.update(update)
    base["updated_utc"] = datetime.now(timezone.utc).isoformat()
    REPORT_JSON.write_text(json.dumps(base, indent=2, sort_keys=True), encoding="utf-8")


def status(exit_missing_ok: bool = True) -> int:
    ensure_dirs()
    rows = {
        "whs_source": SOURCE_PATH,
        "whs_annotation": WHS_ANNOTATION_PATH,
        "landmark_csv": CSV_PATH,
        "region_correction_csv": REGION_CSV_PATH,
        "bregma_ap_csv": BREGMA_CSV_PATH,
        "paxinos_annotation": find_annotation_path(required=False) or Path("<not found>"),
        "affine_candidate": AFFINE_PATH,
        "warp_candidate": WARP_PATH,
        "whs_label_affine_candidate": WHS_LABEL_AFFINE_PATH,
        "whs_label_warp_candidate": WHS_LABEL_WARP_PATH,
        "active_ch03_asset": ACTIVE_PATH,
        "report_json": REPORT_JSON,
    }
    print("V53 Ch03 Landmarker status")
    print(f"target_shape_ap_si_lr: {TARGET_SHAPE}")
    print(f"orientation: perm={PERM}, pre_resize_rot90={PRE_RESIZE_ROT90}, target_flips={TARGET_FLIPS}")
    for key, path in rows.items():
        print(f"{key}: {path} | exists={path.exists()}")
    write_json({"status": {key: {"path": str(path), "exists": path.exists()} for key, path in rows.items()}})
    return 0 if exit_missing_ok else int(not SOURCE_PATH.exists())


def create_template() -> int:
    ensure_dirs()
    if CSV_PATH.exists():
        print(f"Landmark CSV already exists; not overwriting: {rel(CSV_PATH)}")
    else:
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerow({"enabled": "0", "name": "example_midline_landmark", "fixed_ap": "", "fixed_si": "", "fixed_lr": "", "moving_ap": "", "moving_si": "", "moving_lr": "", "weight": "1", "notes": "Replace with AP,SI,LR coordinates; enable with 1/true/yes."})
        print(f"Created landmark CSV template: {rel(CSV_PATH)}")
    if SOURCE_PATH.exists():
        vol = load_oriented_source()
        qc_slices(vol, REPORT_DIR / "qc_template", "template_oriented_whs")
    else:
        msg = f"WHS/Nissl source is missing; set V53_WHS_NISSL_SOURCE or place it at {SOURCE_PATH}."
        (REPORT_DIR / "template_status.txt").write_text(msg + "\n", encoding="utf-8")
        print(msg)
    write_json({"template": {"csv": rel(CSV_PATH), "source_exists": SOURCE_PATH.exists()}})
    return 0


def create_region_template() -> int:
    ensure_dirs()
    if REGION_CSV_PATH.exists():
        print(f"Region correction CSV already exists; not overwriting: {rel(REGION_CSV_PATH)}")
    else:
        with REGION_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REGION_COLUMNS)
            writer.writeheader()
            writer.writerow({"enabled": "0", "name": "example_region_center", "target_ap": "", "target_si": "", "target_lr": "", "current_ap": "", "current_si": "", "current_lr": "", "radius": "45", "weight": "1", "notes": "Target = Paxinos label point; current = same visible Nissl feature after auto-warp/micro-warp, both in AP,SI,LR."})
        print(f"Created region correction CSV template: {rel(REGION_CSV_PATH)}")
    write_json({"region_template": {"csv": rel(REGION_CSV_PATH)}})
    return 0



def create_bregma_template() -> int:
    ensure_dirs()
    if BREGMA_CSV_PATH.exists():
        print(f"Bregma AP CSV already exists; not overwriting: {rel(BREGMA_CSV_PATH)}")
    else:
        with BREGMA_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=BREGMA_COLUMNS)
            writer.writeheader()
            writer.writerow({
                "enabled": "0",
                "name": "example_bregma_anchor",
                "bregma_mm": "",
                "fixed_ap": "",
                "moving_ap": "",
                "weight": "1",
                "notes": "fixed_ap=Paxinos AP index; moving_ap=oriented/affine WHS AP index at the same Bregma level. Enable with 1.",
            })
        print(f"Created Bregma AP CSV template: {rel(BREGMA_CSV_PATH)}")
    write_json({"bregma_template": {"csv": rel(BREGMA_CSV_PATH)}})
    return 0

def load_native_oriented_source() -> np.ndarray:
    """Load the native 39 µm WHS volume with orientation only, without resizing."""
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing WHS/Nissl source: {SOURCE_PATH}. This command requires local WHS data and will not download it.")
    vol = tifffile.imread(SOURCE_PATH)
    if vol.ndim != 3:
        raise ValueError(f"Expected a 3D TIFF source, got shape {vol.shape}")
    vol = np.transpose(vol, PERM)
    vol = np.rot90(vol, k=PRE_RESIZE_ROT90, axes=(1, 2))
    for axis in TARGET_FLIPS:
        vol = np.flip(vol, axis=axis)
    return vol.astype(np.float32, copy=False)


def load_oriented_source() -> np.ndarray:
    """Load WHS and resample it to the Paxinos target grid for 3D workflows."""
    vol = load_native_oriented_source()
    if vol.shape != TARGET_SHAPE:
        vol = transform.resize(vol, TARGET_SHAPE, order=1, preserve_range=True, anti_aliasing=True).astype(np.float32)
    return vol.astype(np.float32, copy=False)



def all_atlas_candidates() -> list[Path]:
    candidates = list(ATLAS_CANDIDATES)
    bg_root = Path.home() / ".brainglobe"
    if bg_root.exists():
        candidates.extend(sorted(p for p in bg_root.glob("*paxinos*") if p.is_dir()))
    unique = []
    seen = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def find_annotation_path(required: bool = True) -> Path | None:
    for atlas_dir in all_atlas_candidates():
        path = atlas_dir / "annotation.tiff"
        if path.exists():
            return path
    if required:
        searched = "\n  - ".join(str(p / "annotation.tiff") for p in all_atlas_candidates())
        raise FileNotFoundError(
            "Could not find Paxinos annotation.tiff for automated label-guided registration. "
            "Run the normal atlas builder locally first, or install the atlas in the BrainGlobe cache. "
            f"Searched:\n  - {searched}"
        )
    return None


def orient_fixed_labels(labels: np.ndarray, path: Path) -> tuple[np.ndarray, dict]:
    if labels.ndim != 3:
        raise ValueError(f"Expected 3D annotation.tiff, got shape {labels.shape} at {path}")
    raw_shape = tuple(int(v) for v in labels.shape)
    if raw_shape == TARGET_SHAPE:
        return labels, {"raw_shape": raw_shape, "oriented_shape": raw_shape, "axis_permutation": None}
    if sorted(raw_shape) == sorted(TARGET_SHAPE):
        # Current ABBA/Paxinos TIFF exports may be stored as LR,AP,SI = 409,608,286.
        # Reorder any exact axis-size permutation into required AP,SI,LR without editing the source file.
        remaining = list(range(3))
        perm = []
        for target_size in TARGET_SHAPE:
            matches = [axis for axis in remaining if raw_shape[axis] == target_size]
            if len(matches) != 1:
                break
            axis = matches[0]
            perm.append(axis)
            remaining.remove(axis)
        if len(perm) == 3:
            oriented = np.transpose(labels, tuple(perm))
            return oriented, {"raw_shape": raw_shape, "oriented_shape": tuple(int(v) for v in oriented.shape), "axis_permutation": tuple(int(v) for v in perm)}
    raise ValueError(
        f"Expected annotation.tiff shape {TARGET_SHAPE} or an axis permutation of it, "
        f"got {raw_shape} at {path}"
    )


def load_fixed_label_mask() -> tuple[np.ndarray, Path, dict]:
    path = find_annotation_path(required=True)
    labels = tifffile.imread(path)
    labels, orientation = orient_fixed_labels(labels, path)
    mask = labels > 0
    if int(mask.sum()) == 0:
        raise ValueError(f"annotation.tiff contains no non-zero labels: {path}")
    return mask, path, orientation


def downsample_mask(mask: np.ndarray, factor: int = 8) -> np.ndarray:
    zoom = tuple(max(1, s // factor) / s for s in mask.shape)
    return ndimage.zoom(mask.astype(np.float32), zoom, order=0) > 0.5


def moving_brain_mask(vol: np.ndarray) -> np.ndarray:
    small = ndimage.zoom(vol, tuple(max(1, s // 8) / s for s in vol.shape), order=1)
    nonzero = small[small > 0]
    if nonzero.size == 0:
        raise ValueError("WHS/Nissl source contains no non-zero voxels after orientation.")
    threshold = filters.threshold_otsu(nonzero) if nonzero.size > 1024 else float(np.percentile(nonzero, 35))
    small_mask = ndimage.binary_fill_holes(small > threshold)
    small_mask = ndimage.binary_opening(small_mask, iterations=1)
    mask = ndimage.zoom(small_mask.astype(np.float32), np.array(vol.shape) / np.array(small_mask.shape), order=0) > 0.5
    return mask


def mask_bbox_com_score(moving_mask: np.ndarray, fixed_mask: np.ndarray) -> dict:
    f_min, f_max = bbox(fixed_mask)
    m_min, m_max = bbox(moving_mask)
    f_size = np.maximum(f_max - f_min, 1.0)
    m_size = np.maximum(m_max - m_min, 1.0)
    f_com = center_of_mass(fixed_mask)
    m_com = center_of_mass(moving_mask)
    shape = np.asarray(TARGET_SHAPE, dtype=float)
    com_error = float(np.linalg.norm((f_com - m_com) / shape))
    size_error = float(np.linalg.norm((f_size - m_size) / shape))
    return {"score": -(com_error + size_error), "com_error": com_error, "size_error": size_error}


def auto_flip_variants(vol: np.ndarray):
    yield (), vol
    for axis in range(3):
        yield (axis,), np.flip(vol, axis=axis)
    for axes in [(0, 1), (0, 2), (1, 2), (0, 1, 2)]:
        out = vol
        for axis in axes:
            out = np.flip(out, axis=axis)
        yield axes, out


def choose_best_auto_oriented_source(fixed_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    base = load_oriented_source()
    fixed_small = downsample_mask(fixed_mask, factor=16)
    best = None
    trials = []
    for axes, candidate in auto_flip_variants(base):
        moving_small = downsample_mask(moving_brain_mask(candidate), factor=16)
        score = mask_bbox_com_score(moving_small, fixed_small)
        trial = {"extra_flips_after_v53_orientation": axes, **score}
        trials.append(trial)
        if best is None or score["score"] > best[0]["score"]:
            best = (trial, candidate)
    return best[1].astype(np.float32, copy=False), {"selected": best[0], "trials": trials}


def bbox(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    coords = np.argwhere(mask)
    if coords.size == 0:
        raise ValueError("Cannot compute bounding box from an empty mask.")
    return coords.min(axis=0).astype(float), coords.max(axis=0).astype(float)


def center_of_mass(mask: np.ndarray) -> np.ndarray:
    com = ndimage.center_of_mass(mask.astype(np.uint8))
    if any(np.isnan(com)):
        raise ValueError("Cannot compute center of mass from an empty mask.")
    return np.asarray(com, dtype=float)


def automatic_affine_matrix(moving_volume: np.ndarray, fixed_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    moving_mask = moving_brain_mask(moving_volume)
    f_min, f_max = bbox(fixed_mask)
    m_min, m_max = bbox(moving_mask)
    f_size = np.maximum(f_max - f_min, 1.0)
    m_size = np.maximum(m_max - m_min, 1.0)
    scale = np.clip(f_size / m_size, 0.60, 1.70)
    f_com = center_of_mass(fixed_mask)
    m_com = center_of_mass(moving_mask)
    mat = np.eye(4)
    mat[:3, :3] = np.diag(scale)
    mat[:3, 3] = f_com - scale * m_com
    metrics = {
        "method": "label_mask_to_nissl_mask_bbox_com_affine",
        "fixed_bbox_min": f_min.tolist(),
        "fixed_bbox_max": f_max.tolist(),
        "moving_bbox_min": m_min.tolist(),
        "moving_bbox_max": m_max.tolist(),
        "scale_ap_si_lr": scale.tolist(),
        "fixed_center_of_mass": f_com.tolist(),
        "moving_center_of_mass": m_com.tolist(),
    }
    return mat, metrics




def affine_matrix_from_masks(moving_mask: np.ndarray, fixed_mask: np.ndarray) -> tuple[np.ndarray, dict]:
    f_min, f_max = bbox(fixed_mask)
    m_min, m_max = bbox(moving_mask)
    f_size = np.maximum(f_max - f_min, 1.0)
    m_size = np.maximum(m_max - m_min, 1.0)
    scale = np.clip(f_size / m_size, 0.60, 1.70)
    f_com = center_of_mass(fixed_mask)
    m_com = center_of_mass(moving_mask)
    mat = np.eye(4)
    mat[:3, :3] = np.diag(scale)
    mat[:3, 3] = f_com - scale * m_com
    metrics = {
        "method": "label_mask_to_label_mask_bbox_com_affine",
        "fixed_bbox_min": f_min.tolist(),
        "fixed_bbox_max": f_max.tolist(),
        "moving_bbox_min": m_min.tolist(),
        "moving_bbox_max": m_max.tolist(),
        "scale_ap_si_lr": scale.tolist(),
        "fixed_center_of_mass": f_com.tolist(),
        "moving_center_of_mass": m_com.tolist(),
    }
    return mat, metrics


def scaled_affine_for_shape(mat: np.ndarray, full_shape: tuple[int, int, int], small_shape: tuple[int, int, int]) -> np.ndarray:
    full = np.asarray(full_shape, dtype=np.float64)
    small = np.asarray(small_shape, dtype=np.float64)
    ratio = small / full
    scaled = np.eye(4, dtype=np.float64)
    scaled[:3, :3] = np.diag(ratio) @ mat[:3, :3] @ np.diag(1.0 / ratio)
    scaled[:3, 3] = ratio * mat[:3, 3]
    return scaled


def mask_affine_dice_score(moving_mask: np.ndarray, fixed_mask: np.ndarray, mat: np.ndarray, factor: int = 8) -> dict:
    fixed_small = downsample_mask(fixed_mask, factor=factor)
    moving_small = downsample_mask(moving_mask, factor=factor)
    small_mat = scaled_affine_for_shape(mat, TARGET_SHAPE, fixed_small.shape)
    moved_small = apply_affine_nearest_shape(moving_small.astype(np.uint8), small_mat, fixed_small.shape).astype(bool)
    intersection = int(np.logical_and(moved_small, fixed_small).sum())
    moved_count = int(moved_small.sum())
    fixed_count = int(fixed_small.sum())
    dice = 2.0 * intersection / max(moved_count + fixed_count, 1)
    return {"dice": float(dice), "intersection_voxels_small": intersection, "moving_voxels_small": moved_count, "fixed_voxels_small": fixed_count}


def choose_best_label_volume_affine(raw_moving_labels: np.ndarray, fixed_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    trials = []
    best = None
    for axes in [(), (0,), (1,), (2,), (0, 1), (0, 2), (1, 2), (0, 1, 2)]:
        moving_labels = orient_moving_labels_like_source(raw_moving_labels, axes)
        moving_mask = moving_labels > 0
        mat, metrics = affine_matrix_from_masks(moving_mask, fixed_mask)
        score = mask_affine_dice_score(moving_mask, fixed_mask, mat, factor=8)
        trial = {"extra_flips_after_v53_orientation": axes, **metrics, **score}
        trials.append(trial)
        if best is None or score["dice"] > best[0]["dice"]:
            best = (trial, moving_labels, mat)
    return best[1].astype(np.int32, copy=False), best[2], {"selected": best[0], "trials": trials}

def read_structure_terms(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    terms = set()
    for item in data if isinstance(data, list) else data.get("structures", []):
        for key in ("name", "acronym"):
            value = str(item.get(key, "")).strip().lower()
            if value:
                terms.add(value)
    return terms


def whs_paxinos_structure_overlap(annotation_path: Path) -> dict:
    whs_structures = SOURCE_PATH.parent / "structures.json"
    paxinos_structures = annotation_path.parent / "structures.json"
    whs_terms = read_structure_terms(whs_structures)
    paxinos_terms = read_structure_terms(paxinos_structures)
    overlap = sorted(whs_terms & paxinos_terms)
    return {
        "whs_structures_json": str(whs_structures),
        "whs_structures_json_exists": whs_structures.exists(),
        "paxinos_structures_json": str(paxinos_structures),
        "paxinos_structures_json_exists": paxinos_structures.exists(),
        "whs_term_count": len(whs_terms),
        "paxinos_term_count": len(paxinos_terms),
        "exact_name_or_acronym_overlap_count": len(overlap),
        "exact_name_or_acronym_overlap_examples": overlap[:50],
        "note": "Reported for audit only; registration still uses geometry/intensity masks unless explicit landmarks are provided.",
    }


def run_auto_affine() -> int:
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    vol, source_orientation = choose_best_auto_oriented_source(fixed_mask)
    mat, metrics = automatic_affine_matrix(vol, fixed_mask)
    out = apply_affine(vol, mat)
    write_tiff(AFFINE_PATH, out)
    qc_slices(out, REPORT_DIR / "qc_affine", "ch03_auto_affine")
    qc_overlay_slices(out, fixed_mask, REPORT_DIR / "qc_affine", "ch03_auto_affine")
    write_json({"auto_affine": {"candidate": rel(AFFINE_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "source_orientation": source_orientation, "structure_overlap_audit": whs_paxinos_structure_overlap(annotation_path), "matrix_moving_to_fixed": mat.tolist(), **metrics}})
    return 0


def run_auto_warp() -> int:
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    base = tifffile.imread(AFFINE_PATH) if AFFINE_PATH.exists() else None
    if base is None:
        run_auto_affine()
        base = tifffile.imread(AFFINE_PATH)
    moving_mask = moving_brain_mask(base)
    warp_downsample_factor = 8
    fixed_small = downsample_mask(fixed_mask, factor=warp_downsample_factor)
    moving_small = downsample_mask(moving_mask, factor=warp_downsample_factor)
    source_ap_small = ap_dtw_mapping(fixed_small, moving_small)
    fixed_com = np.asarray([center_of_mass(fixed_small[i]) if fixed_small[i].any() else (np.nan, np.nan) for i in range(fixed_small.shape[0])])
    moving_com = np.asarray([center_of_mass(moving_small[i]) if moving_small[i].any() else (np.nan, np.nan) for i in range(moving_small.shape[0])])
    moving_valid = np.isfinite(moving_com[:, 0])
    fixed_valid = np.isfinite(fixed_com[:, 0])
    if fixed_valid.sum() < 8 or moving_valid.sum() < 8:
        raise ValueError(f"Automated warp needs at least 8 AP slices with label and Nissl masks; fixed={int(fixed_valid.sum())}, moving={int(moving_valid.sum())}.")
    moving_idx = np.flatnonzero(moving_valid)
    fixed_idx = np.flatnonzero(fixed_valid)
    mapped_moving_com = np.column_stack([
        np.interp(source_ap_small, moving_idx, moving_com[moving_valid, axis])
        for axis in range(2)
    ])
    delta_small = np.zeros((fixed_small.shape[0], 2), dtype=np.float32)
    delta_small[fixed_valid] = fixed_com[fixed_valid] - mapped_moving_com[fixed_valid]
    fixed_sizes = slice_bbox_sizes(fixed_small)
    moving_sizes = slice_bbox_sizes(moving_small)
    size_valid = fixed_valid & np.all(np.isfinite(fixed_sizes), axis=1)
    mapped_moving_sizes = np.column_stack([
        np.interp(source_ap_small, moving_idx, moving_sizes[moving_valid, axis])
        for axis in range(2)
    ])
    scale_small = np.ones((fixed_small.shape[0], 2), dtype=np.float32)
    scale_valid = size_valid & np.all(mapped_moving_sizes > 1, axis=1)
    scale_small[scale_valid] = fixed_sizes[scale_valid] / mapped_moving_sizes[scale_valid]
    fixed_com_filled = np.zeros_like(fixed_com, dtype=np.float32)
    for axis in range(2):
        fixed_com_filled[:, axis] = np.interp(np.arange(len(fixed_com)), fixed_idx, fixed_com[fixed_valid, axis])
        delta_small[:, axis] = np.interp(np.arange(len(delta_small)), fixed_idx, delta_small[fixed_valid, axis])
        delta_small[:, axis] = ndimage.gaussian_filter1d(delta_small[:, axis], sigma=2.0)
        valid_scale_idx = np.flatnonzero(scale_valid)
        if valid_scale_idx.size >= 2:
            scale_small[:, axis] = np.interp(np.arange(len(scale_small)), valid_scale_idx, scale_small[scale_valid, axis])
        scale_small[:, axis] = ndimage.gaussian_filter1d(np.clip(scale_small[:, axis], 0.80, 1.25), sigma=2.0)
    ap_scale = TARGET_SHAPE[0] / fixed_small.shape[0]
    si_scale = TARGET_SHAPE[1] / fixed_small.shape[1]
    lr_scale = TARGET_SHAPE[2] / fixed_small.shape[2]
    full_ap_index = np.arange(TARGET_SHAPE[0]) / ap_scale
    source_ap = np.interp(full_ap_index, np.arange(fixed_small.shape[0]), source_ap_small) * ap_scale
    delta = np.column_stack([np.zeros(TARGET_SHAPE[0]), np.interp(full_ap_index, np.arange(fixed_small.shape[0]), delta_small[:, 0]) * si_scale, np.interp(full_ap_index, np.arange(fixed_small.shape[0]), delta_small[:, 1]) * lr_scale])
    fixed_center_full = np.column_stack([
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), fixed_com_filled[:, 0]) * si_scale,
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), fixed_com_filled[:, 1]) * lr_scale,
    ])
    moving_center_full = fixed_center_full - delta[:, 1:3]
    slice_scale_full = np.column_stack([
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), scale_small[:, 0]),
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), scale_small[:, 1]),
    ])
    warped = map_auto_warp_chunked(base, delta, source_ap=source_ap, slice_scale=slice_scale_full, fixed_center=fixed_center_full, moving_center=moving_center_full)
    fixed_labels, _ = orient_fixed_labels(tifffile.imread(annotation_path), annotation_path)
    edge_offsets, edge_metrics = residual_edge_offsets_from_labels(fixed_labels, warped, factor=4)
    warped = apply_slice_offsets_chunked(warped, edge_offsets)
    write_tiff(WARP_PATH, warped)
    qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_auto_warp")
    qc_overlay_slices(warped, fixed_mask, REPORT_DIR / "qc_warp", "ch03_auto_warp")
    write_json({"auto_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "method": "affine_plus_fine_ap_dtw_slice_size_and_edge_refine_warp", "warp_downsample_factor": warp_downsample_factor, "fixed_valid_ap_slices": int(fixed_valid.sum()), "moving_valid_ap_slices": int(moving_valid.sum()), "slice_scale_si_lr_min": [float(slice_scale_full[:, 0].min()), float(slice_scale_full[:, 1].min())], "slice_scale_si_lr_max": [float(slice_scale_full[:, 0].max()), float(slice_scale_full[:, 1].max())], **edge_metrics, "ap_source_min_max": [float(source_ap.min()), float(source_ap.max())], "ap_source_samples": [float(source_ap[i]) for i in np.linspace(0, TARGET_SHAPE[0] - 1, 9, dtype=int)]}})
    return 0

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_landmarks(min_count: int) -> LandmarkSet:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing landmark CSV: {rel(CSV_PATH)}. Run landmarks-template first.")
    fixed = []; moving = []; names = []; weights = []
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"Invalid CSV columns. Expected exactly: {','.join(COLUMNS)}")
        for rownum, row in enumerate(reader, start=2):
            if not parse_bool(row["enabled"]):
                continue
            try:
                fx = [float(row[c]) for c in ("fixed_ap", "fixed_si", "fixed_lr")]
                mv = [float(row[c]) for c in ("moving_ap", "moving_si", "moving_lr")]
                wt = float(row["weight"] or 1)
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value in enabled landmark row {rownum}: {exc}") from exc
            for label, coords in (("fixed", fx), ("moving", mv)):
                if not (0 <= coords[0] <= 607 and 0 <= coords[1] <= 285 and 0 <= coords[2] <= 408):
                    raise ValueError(f"{label} coordinates out of AP/SI/LR range on row {rownum}: {coords}")
            if wt <= 0:
                raise ValueError(f"Landmark weight must be positive on row {rownum}")
            names.append(row["name"] or f"row_{rownum}"); fixed.append(fx); moving.append(mv); weights.append(wt)
    if len(fixed) < min_count:
        raise ValueError(f"Need at least {min_count} enabled valid landmarks; found {len(fixed)}.")
    return LandmarkSet(names, np.asarray(fixed), np.asarray(moving), np.asarray(weights))


@dataclass
class RegionCorrectionSet:
    names: list[str]
    target: np.ndarray
    current: np.ndarray
    radius: np.ndarray
    weights: np.ndarray


def read_region_corrections(min_count: int) -> RegionCorrectionSet:
    if not REGION_CSV_PATH.exists():
        raise FileNotFoundError(f"Missing region correction CSV: {rel(REGION_CSV_PATH)}. Run region-template first.")
    target = []; current = []; names = []; radii = []; weights = []
    with REGION_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REGION_COLUMNS:
            raise ValueError(f"Invalid region CSV columns. Expected exactly: {','.join(REGION_COLUMNS)}")
        for rownum, row in enumerate(reader, start=2):
            if not parse_bool(row["enabled"]):
                continue
            try:
                tgt = [float(row[c]) for c in ("target_ap", "target_si", "target_lr")]
                cur = [float(row[c]) for c in ("current_ap", "current_si", "current_lr")]
                radius = float(row["radius"] or 45)
                wt = float(row["weight"] or 1)
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value in enabled region row {rownum}: {exc}") from exc
            for label, coords in (("target", tgt), ("current", cur)):
                if not (0 <= coords[0] <= 607 and 0 <= coords[1] <= 285 and 0 <= coords[2] <= 408):
                    raise ValueError(f"{label} coordinates out of AP/SI/LR range on row {rownum}: {coords}")
            if radius <= 0 or wt <= 0:
                raise ValueError(f"Region radius and weight must be positive on row {rownum}")
            names.append(row["name"] or f"row_{rownum}"); target.append(tgt); current.append(cur); radii.append(radius); weights.append(wt)
    if len(target) < min_count:
        raise ValueError(f"Need at least {min_count} enabled valid region corrections; found {len(target)}.")
    return RegionCorrectionSet(names, np.asarray(target, dtype=np.float32), np.asarray(current, dtype=np.float32), np.asarray(radii, dtype=np.float32), np.asarray(weights, dtype=np.float32))


def local_region_displacement(lm: RegionCorrectionSet, grid_shape: tuple[int, int, int]) -> np.ndarray:
    axes = [np.linspace(0, s - 1, n, dtype=np.float32) for s, n in zip(TARGET_SHAPE, grid_shape)]
    mesh = np.meshgrid(*axes, indexing="ij")
    disp_grid = np.zeros((*grid_shape, 3), dtype=np.float32)
    weight_grid = np.zeros(grid_shape, dtype=np.float32)
    landmark_disp = lm.target - lm.current
    for point, disp, radius, weight in zip(lm.target, landmark_disp, lm.radius, lm.weights):
        d2 = sum((mesh[axis] - point[axis]) ** 2 for axis in range(3))
        influence = np.exp(-0.5 * d2 / max(radius, 1.0) ** 2).astype(np.float32) * float(weight)
        weight_grid += influence
        for axis in range(3):
            disp_grid[..., axis] += influence * float(disp[axis])
    valid = weight_grid > 1e-6
    disp_grid[valid] /= weight_grid[valid, None]
    disp_grid *= np.clip(weight_grid[..., None], 0.0, 1.0)
    return disp_grid


def run_region_warp() -> int:
    lm = read_region_corrections(1)
    if not WARP_PATH.exists():
        raise FileNotFoundError(f"Warp candidate is missing: {rel(WARP_PATH)}. Run auto-warp first.")
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    base = tifffile.imread(WARP_PATH)
    grid_shape = tuple(max(12, s // 12) for s in TARGET_SHAPE)
    disp_small = local_region_displacement(lm, grid_shape)
    disp = np.stack([ndimage.zoom(disp_small[..., i], np.array(TARGET_SHAPE) / np.array(grid_shape), order=1) for i in range(3)]).astype(np.float32)
    warped = map_displacement_chunked(base, disp)
    write_tiff(WARP_PATH, warped)
    qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_region_warp")
    qc_overlay_slices(warped, fixed_mask, REPORT_DIR / "qc_warp", "ch03_region_warp")
    write_json({"region_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "region_count": len(lm.names), "method": "manual_local_gaussian_region_correction", "grid_shape": grid_shape, "max_requested_shift_voxels": float(np.linalg.norm(lm.target - lm.current, axis=1).max()), "radius_min_max": [float(lm.radius.min()), float(lm.radius.max())]}})
    return 0



def qc_coordinate_slices(vol: np.ndarray, fixed_mask: np.ndarray, outdir: Path, prefix: str = "ch03_region_pick") -> None:
    """Write coordinate-labelled overlays for choosing region correction points.

    The image axes are LR (horizontal x) and SI (vertical y); AP is encoded in
    the filename/title. This makes screenshots self-contained enough to discuss
    target/current coordinates without sharing CSV files.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    plt = get_pyplot()
    aps = np.linspace(40, TARGET_SHAPE[0] - 41, 10, dtype=int)
    for ap in aps:
        img = exposure.rescale_intensity(vol[ap], out_range=(0, 1))
        boundary = fixed_mask[ap] ^ ndimage.binary_erosion(fixed_mask[ap])
        rgb = np.dstack([img, img, img])
        rgb[boundary, 0] = 1.0
        rgb[boundary, 1] = 0.05
        rgb[boundary, 2] = 0.05
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.imshow(rgb, origin="upper")
        ax.set_title(f"{prefix} AP={ap} | x=LR 0-{TARGET_SHAPE[2]-1}, y=SI 0-{TARGET_SHAPE[1]-1} | red=Paxinos outline")
        ax.set_xlabel("LR coordinate")
        ax.set_ylabel("SI coordinate")
        ax.set_xticks(np.arange(0, TARGET_SHAPE[2], 50))
        ax.set_yticks(np.arange(0, TARGET_SHAPE[1], 50))
        ax.grid(color="yellow", alpha=0.35, linewidth=0.6)
        fig.tight_layout()
        fig.savefig(outdir / f"{prefix}_overlay_ap_{ap:03d}_coordgrid.png", dpi=140)
        plt.close(fig)


def run_region_qc() -> int:
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    if WARP_PATH.exists():
        vol = tifffile.imread(WARP_PATH)
        source = WARP_PATH
    elif ACTIVE_PATH.exists():
        vol = tifffile.imread(ACTIVE_PATH)
        source = ACTIVE_PATH
    else:
        raise FileNotFoundError(f"No Ch03 warp/active asset exists. Run auto-warp first; expected {rel(WARP_PATH)} or {rel(ACTIVE_PATH)}.")
    qc_coordinate_slices(vol, fixed_mask, REPORT_DIR / "qc_region_pick")
    write_json({"region_qc": {"source": rel(source), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "qc_dir": rel(REPORT_DIR / "qc_region_pick"), "coordinate_convention": "AP in filename/title; image x=LR, image y=SI"}})
    print(f"Wrote coordinate-pick QC to {rel(REPORT_DIR / 'qc_region_pick')}")
    return 0


def append_region_correction(args: argparse.Namespace) -> int:
    ensure_dirs()
    if not REGION_CSV_PATH.exists():
        create_region_template()
    row = {
        "enabled": "1",
        "name": args.name,
        "target_ap": str(args.target_ap),
        "target_si": str(args.target_si),
        "target_lr": str(args.target_lr),
        "current_ap": str(args.current_ap),
        "current_si": str(args.current_si),
        "current_lr": str(args.current_lr),
        "radius": str(args.radius),
        "weight": str(args.weight),
        "notes": args.notes or "added by region-add",
    }
    for label, coords in (("target", [args.target_ap, args.target_si, args.target_lr]), ("current", [args.current_ap, args.current_si, args.current_lr])):
        if not (0 <= coords[0] <= 607 and 0 <= coords[1] <= 285 and 0 <= coords[2] <= 408):
            raise ValueError(f"{label} coordinates out of AP/SI/LR range: {coords}")
    if args.radius <= 0 or args.weight <= 0:
        raise ValueError("radius and weight must be positive")
    with REGION_CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REGION_COLUMNS)
        writer.writerow(row)
    print(f"Added enabled region correction '{args.name}' to {rel(REGION_CSV_PATH)}")
    write_json({"region_add_last": row})
    return 0


def list_region_corrections() -> int:
    if not REGION_CSV_PATH.exists():
        raise FileNotFoundError(f"Missing region correction CSV: {rel(REGION_CSV_PATH)}. Run region-template first.")
    total = 0
    enabled = 0
    with REGION_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REGION_COLUMNS:
            raise ValueError(f"Invalid region CSV columns. Expected exactly: {','.join(REGION_COLUMNS)}")
        for rownum, row in enumerate(reader, start=2):
            total += 1
            if parse_bool(row["enabled"]):
                enabled += 1
                print(f"row {rownum}: {row['name']} target=({row['target_ap']},{row['target_si']},{row['target_lr']}) current=({row['current_ap']},{row['current_si']},{row['current_lr']}) radius={row['radius']} weight={row['weight']}")
    print(f"Region corrections: {enabled} enabled / {total} rows in {rel(REGION_CSV_PATH)}")
    return 0


def automatic_region_corrections_from_edges(
    fixed_labels: np.ndarray,
    moving_vol: np.ndarray,
    ap_step: int = 24,
    tile_size: int = 64,
    max_shift: float = 4.0,
) -> tuple[RegionCorrectionSet, dict]:
    """Create conservative local region corrections from label boundaries and Nissl edges.

    This is intended as the non-manual counterpart to region-add/region-warp: it
    samples many AP/SI/LR tiles, estimates small local SI/LR shifts by phase
    correlation, and turns reliable shifts into soft Gaussian control points.
    """
    names: list[str] = []
    target: list[list[float]] = []
    current: list[list[float]] = []
    radii: list[float] = []
    weights: list[float] = []
    half = tile_size // 2
    ap_values = range(48, TARGET_SHAPE[0] - 48, ap_step)
    si_values = range(half, TARGET_SHAPE[1] - half + 1, half)
    lr_values = range(half, TARGET_SHAPE[2] - half + 1, half)
    attempted = 0
    rejected = 0
    errors: list[float] = []
    shifts: list[float] = []
    for ap in ap_values:
        fixed_edge = label_boundary_2d(fixed_labels[ap]).astype(np.float32)
        moving_slice = exposure.rescale_intensity(moving_vol[ap], out_range=(0, 1)).astype(np.float32)
        moving_edge = filters.sobel(moving_slice).astype(np.float32)
        if moving_edge.max() > 0:
            moving_edge /= float(moving_edge.max())
        for si in si_values:
            si0, si1 = si - half, si + half
            for lr in lr_values:
                lr0, lr1 = lr - half, lr + half
                fixed_tile = fixed_edge[si0:si1, lr0:lr1]
                moving_tile = moving_edge[si0:si1, lr0:lr1]
                attempted += 1
                if fixed_tile.sum() < 18 or moving_tile.sum() < 1.5:
                    rejected += 1
                    continue
                window = np.outer(np.hanning(fixed_tile.shape[0]), np.hanning(fixed_tile.shape[1])).astype(np.float32)
                try:
                    shift, error, _ = phase_cross_correlation(
                        ndimage.gaussian_filter(fixed_tile * window, sigma=1.0),
                        ndimage.gaussian_filter(moving_tile * window, sigma=1.0),
                        upsample_factor=1,
                        normalization=None,
                    )
                except Exception:
                    rejected += 1
                    continue
                if not np.all(np.isfinite(shift[:2])):
                    rejected += 1
                    continue
                shift = np.clip(shift[:2].astype(np.float32), -max_shift, max_shift)
                shift_norm = float(np.linalg.norm(shift))
                if shift_norm < 0.5 or shift_norm > max_shift:
                    rejected += 1
                    continue
                name = f"auto_region_ap{ap:03d}_si{si:03d}_lr{lr:03d}"
                names.append(name)
                target.append([float(ap), float(si), float(lr)])
                current.append([float(ap), float(si - shift[0]), float(lr - shift[1])])
                radii.append(float(tile_size * 0.55))
                confidence = 1.0 / (1.0 + max(float(error), 0.0)) if np.isfinite(error) else 0.5
                weights.append(float(np.clip(confidence, 0.25, 1.0)))
                errors.append(float(error) if np.isfinite(error) else 0.0)
                shifts.append(shift_norm)
    if not target:
        raise ValueError("Auto region warp could not find any reliable local edge corrections. Use region-qc/region-add or adjust source data.")
    lm = RegionCorrectionSet(names, np.asarray(target, dtype=np.float32), np.asarray(current, dtype=np.float32), np.asarray(radii, dtype=np.float32), np.asarray(weights, dtype=np.float32))
    metrics = {
        "auto_region_attempted_tiles": attempted,
        "auto_region_rejected_tiles": rejected,
        "auto_region_count": len(names),
        "auto_region_ap_step": ap_step,
        "auto_region_tile_size": tile_size,
        "auto_region_max_shift": max_shift,
        "auto_region_shift_min_max": [float(np.min(shifts)), float(np.max(shifts))],
        "auto_region_shift_mean": float(np.mean(shifts)),
        "auto_region_phase_error_mean": float(np.mean(errors)) if errors else None,
    }
    return lm, metrics



def edge_alignment_score(fixed_labels: np.ndarray, moving_vol: np.ndarray, factor: int = 4) -> dict:
    """Score how strongly Nissl edges fall on Paxinos label boundaries.

    Higher is better. The score is intentionally simple and deterministic so it
    can be used as a safety gate before overwriting a good warp candidate.
    """
    small_shape = tuple(max(8, s // factor) for s in TARGET_SHAPE)
    zoom = np.asarray(small_shape) / np.asarray(TARGET_SHAPE)
    labels_small = ndimage.zoom(fixed_labels, zoom, order=0)
    moving_small = ndimage.zoom(moving_vol.astype(np.float32, copy=False), zoom, order=1)
    values: list[float] = []
    valid_slices = 0
    for ap in range(small_shape[0]):
        boundary = label_boundary_2d(labels_small[ap])
        if boundary.sum() < 20:
            continue
        moving_slice = exposure.rescale_intensity(moving_small[ap], out_range=(0, 1)).astype(np.float32)
        edge = filters.sobel(moving_slice).astype(np.float32)
        if edge.max() <= 0:
            continue
        edge /= float(edge.max())
        values.append(float(edge[boundary].mean()))
        valid_slices += 1
    return {
        "edge_alignment_factor": factor,
        "edge_alignment_valid_slices": valid_slices,
        "edge_alignment_score": float(np.mean(values)) if values else 0.0,
    }

def run_auto_region_warp() -> int:
    if not WARP_PATH.exists():
        run_auto_warp()
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    fixed_labels, _ = orient_fixed_labels(tifffile.imread(annotation_path), annotation_path)
    base = tifffile.imread(WARP_PATH)
    lm, metrics = automatic_region_corrections_from_edges(fixed_labels, base)
    grid_shape = tuple(max(12, s // 12) for s in TARGET_SHAPE)
    disp_small = local_region_displacement(lm, grid_shape)
    disp = np.stack([ndimage.zoom(disp_small[..., i], np.array(TARGET_SHAPE) / np.array(grid_shape), order=1) for i in range(3)]).astype(np.float32)
    warped = map_displacement_chunked(base, disp)
    before_score = edge_alignment_score(fixed_labels, base)
    after_score = edge_alignment_score(fixed_labels, warped)
    improvement = after_score["edge_alignment_score"] - before_score["edge_alignment_score"]
    accepted = improvement >= 0.002
    output = warped if accepted else base
    write_tiff(WARP_PATH, output)
    qc_slices(output, REPORT_DIR / "qc_warp", "ch03_auto_region_warp")
    qc_overlay_slices(output, fixed_mask, REPORT_DIR / "qc_warp", "ch03_auto_region_warp")
    write_json({"auto_region_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "method": "automatic_local_tile_edge_region_correction_with_score_gate", "grid_shape": grid_shape, "accepted": accepted, "score_improvement": float(improvement), "score_before": before_score, "score_after": after_score, **metrics}})
    if accepted:
        print(f"Accepted automatic region warp: edge score improved by {improvement:.6f}")
    else:
        print(f"Rejected automatic region warp: edge score improvement {improvement:.6f} is below threshold; kept previous warp candidate.")
    return 0




def read_report() -> dict:
    if not REPORT_JSON.exists():
        return {}
    try:
        return json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def apply_extra_flips(vol: np.ndarray, axes: Iterable[int]) -> np.ndarray:
    out = vol
    for axis in axes:
        out = np.flip(out, axis=int(axis))
    return out.astype(np.float32, copy=False)


def load_source_for_auto_affine_context(fixed_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return the source volume and affine matrix used as Bregma warp context.

    Earlier bregma-warp versions sampled the already-affined candidate, which made
    the AP anchor semantics ambiguous. This helper restores the direct path:
    original oriented WHS/Nissl source -> explicit Bregma AP map + affine SI/LR.
    """
    report = read_report()
    auto = report.get("auto_affine", {}) if isinstance(report.get("auto_affine"), dict) else {}
    matrix = auto.get("matrix_moving_to_fixed")
    selected = ((auto.get("source_orientation") or {}).get("selected") or {}) if isinstance(auto.get("source_orientation"), dict) else {}
    if matrix is not None:
        vol = load_oriented_source()
        vol = apply_extra_flips(vol, selected.get("extra_flips_after_v53_orientation", []))
        return vol, np.asarray(matrix, dtype=np.float64), {"source": "auto_affine_report", "extra_flips_after_v53_orientation": selected.get("extra_flips_after_v53_orientation", [])}
    if fixed_mask is None:
        fixed_mask, _, _ = load_fixed_label_mask()
    vol, source_orientation = choose_best_auto_oriented_source(fixed_mask)
    mat, _ = automatic_affine_matrix(vol, fixed_mask)
    return vol, mat, {"source": "computed_now", "source_orientation": source_orientation}


def affine_ap_samples(mat: np.ndarray, sample_count: int = 7) -> list[tuple[float, float]]:
    inv = np.linalg.inv(mat)
    fixed_ap = np.linspace(0, TARGET_SHAPE[0] - 1, sample_count, dtype=np.float64)
    fixed_center = np.column_stack([
        fixed_ap,
        np.full(sample_count, (TARGET_SHAPE[1] - 1) / 2, dtype=np.float64),
        np.full(sample_count, (TARGET_SHAPE[2] - 1) / 2, dtype=np.float64),
        np.ones(sample_count, dtype=np.float64),
    ])
    moving = fixed_center @ inv.T
    moving_ap = np.clip(moving[:, 0], 0, TARGET_SHAPE[0] - 1)
    return [(float(f), float(m)) for f, m in zip(fixed_ap, moving_ap)]


def init_bregma_from_affine() -> int:
    ensure_dirs()
    fixed_mask, _, _ = load_fixed_label_mask()
    _, mat, context = load_source_for_auto_affine_context(fixed_mask)
    samples = affine_ap_samples(mat, sample_count=7)
    if BREGMA_CSV_PATH.exists():
        backup = BREGMA_CSV_PATH.with_suffix(".csv.before_affine_init")
        shutil.copy2(BREGMA_CSV_PATH, backup)
        print(f"Backed up existing Bregma CSV to: {rel(backup)}")
    with BREGMA_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BREGMA_COLUMNS)
        writer.writeheader()
        for idx, (fixed_ap, moving_ap) in enumerate(samples, start=1):
            writer.writerow({
                "enabled": "1",
                "name": f"affine_ap_anchor_{idx}",
                "bregma_mm": "",
                "fixed_ap": f"{fixed_ap:.3f}",
                "moving_ap": f"{moving_ap:.3f}",
                "weight": "0.5",
                "notes": "Auto-initialized from affine AP mapping; replace with real Bregma AP anchors when available.",
            })
    write_json({"bregma_init_affine": {"csv": rel(BREGMA_CSV_PATH), "sample_count": len(samples), "context": context, "note": "Affine initialization is a reproducible fallback, not a true Bregma calibration."}})
    print(f"Wrote affine-initialized Bregma AP CSV: {rel(BREGMA_CSV_PATH)}")
    print("Important: replace these fallback anchors with real Bregma AP correspondences for best anatomical results.")
    return 0




def slice_center_size(mask2d: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    coords = np.argwhere(mask2d)
    if coords.size == 0:
        return None
    center = (coords.min(axis=0) + coords.max(axis=0)) / 2.0
    size = coords.max(axis=0) - coords.min(axis=0) + 1.0
    return center.astype(np.float32), size.astype(np.float32)


def bregma_slice_geometry(fixed_mask: np.ndarray, source_mask: np.ndarray, source_ap: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Estimate AP-locked per-slice SI/LR center and scale for Bregma warping."""
    fixed_center = np.zeros((TARGET_SHAPE[0], 2), dtype=np.float32)
    moving_center = np.zeros((TARGET_SHAPE[0], 2), dtype=np.float32)
    slice_scale = np.ones((TARGET_SHAPE[0], 2), dtype=np.float32)
    valid = np.zeros(TARGET_SHAPE[0], dtype=bool)
    default_center = np.asarray([(TARGET_SHAPE[1] - 1) / 2, (TARGET_SHAPE[2] - 1) / 2], dtype=np.float32)
    for ap in range(TARGET_SHAPE[0]):
        fixed_stats = slice_center_size(fixed_mask[ap])
        src_idx = int(np.clip(round(float(source_ap[ap])), 0, TARGET_SHAPE[0] - 1))
        moving_stats = slice_center_size(source_mask[src_idx])
        if fixed_stats is None or moving_stats is None:
            fixed_center[ap] = default_center
            moving_center[ap] = default_center
            continue
        fc, fs = fixed_stats
        mc, ms = moving_stats
        fixed_center[ap] = fc
        moving_center[ap] = mc
        slice_scale[ap] = np.clip(fs / np.maximum(ms, 1.0), 0.70, 1.55)
        valid[ap] = True
    valid_idx = np.flatnonzero(valid)
    if valid_idx.size < 8:
        raise ValueError(f"Bregma slice geometry needs at least 8 valid AP slices with fixed and moving masks; found {int(valid_idx.size)}.")
    x = np.arange(TARGET_SHAPE[0])
    for axis in range(2):
        fixed_center[:, axis] = np.interp(x, valid_idx, fixed_center[valid, axis])
        moving_center[:, axis] = np.interp(x, valid_idx, moving_center[valid, axis])
        slice_scale[:, axis] = np.interp(x, valid_idx, slice_scale[valid, axis])
        fixed_center[:, axis] = ndimage.gaussian_filter1d(fixed_center[:, axis], sigma=2.0)
        moving_center[:, axis] = ndimage.gaussian_filter1d(moving_center[:, axis], sigma=2.0)
        slice_scale[:, axis] = ndimage.gaussian_filter1d(slice_scale[:, axis], sigma=2.0)
    slice_scale = np.clip(slice_scale, 0.70, 1.55).astype(np.float32)
    metrics = {
        "bregma_slice_geometry_valid_slices": int(valid.sum()),
        "bregma_slice_scale_si_lr_min": [float(slice_scale[:, 0].min()), float(slice_scale[:, 1].min())],
        "bregma_slice_scale_si_lr_max": [float(slice_scale[:, 0].max()), float(slice_scale[:, 1].max())],
        "bregma_slice_geometry_note": "AP is Bregma-anchored; SI/LR centers and scales are fitted per AP slice from fixed and WHS/Nissl brain masks.",
    }
    return fixed_center, moving_center, slice_scale, metrics

def map_bregma_affine_source_chunked(source: np.ndarray, mat: np.ndarray, source_ap: np.ndarray, chunk: int = 24) -> np.ndarray:
    inv = np.linalg.inv(mat)
    out = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    si = np.arange(TARGET_SHAPE[1], dtype=np.float32)
    lr = np.arange(TARGET_SHAPE[2], dtype=np.float32)
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = np.arange(start, stop, dtype=np.float32)
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        fixed = np.stack([coords[0], coords[1], coords[2], np.ones_like(coords[0])], axis=0)
        moving_si = inv[1, 0] * fixed[0] + inv[1, 1] * fixed[1] + inv[1, 2] * fixed[2] + inv[1, 3]
        moving_lr = inv[2, 0] * fixed[0] + inv[2, 1] * fixed[1] + inv[2, 2] * fixed[2] + inv[2, 3]
        moving_ap = np.broadcast_to(source_ap[start:stop, None, None], moving_si.shape)
        sample = [moving_ap, moving_si, moving_lr]
        out[start:stop] = ndimage.map_coordinates(source, sample, order=1, mode="constant", cval=0).astype(np.uint16)
    return out

@dataclass
class BregmaAPMap:
    names: list[str]
    bregma_mm: np.ndarray
    fixed_ap: np.ndarray
    moving_ap: np.ndarray
    weights: np.ndarray


def read_bregma_ap_map(min_count: int = 2) -> BregmaAPMap:
    if not BREGMA_CSV_PATH.exists():
        raise FileNotFoundError(f"Missing Bregma AP CSV: {rel(BREGMA_CSV_PATH)}. Run bregma-template first.")
    names: list[str] = []
    bregma: list[float] = []
    fixed: list[float] = []
    moving: list[float] = []
    weights: list[float] = []
    with BREGMA_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != BREGMA_COLUMNS:
            raise ValueError(f"Invalid Bregma CSV columns. Expected exactly: {','.join(BREGMA_COLUMNS)}")
        for rownum, row in enumerate(reader, start=2):
            if not parse_bool(row["enabled"]):
                continue
            try:
                raw_bregma = str(row["bregma_mm"]).strip()
                bregma_mm = float(raw_bregma) if raw_bregma else float("nan")
                if str(row["fixed_ap"]).strip() == "" or str(row["moving_ap"]).strip() == "":
                    raise ValueError("fixed_ap and moving_ap are required for enabled Bregma anchors")
                fixed_ap = float(row["fixed_ap"])
                moving_ap = float(row["moving_ap"])
                wt = float(row["weight"] or 1)
            except ValueError as exc:
                raise ValueError(f"Invalid numeric value in enabled Bregma row {rownum}: {exc}") from exc
            if not (0 <= fixed_ap <= 607 and 0 <= moving_ap <= 607):
                raise ValueError(f"Bregma AP coordinates out of range on row {rownum}: fixed={fixed_ap}, moving={moving_ap}")
            if wt <= 0:
                raise ValueError(f"Bregma weight must be positive on row {rownum}")
            names.append(row["name"] or f"row_{rownum}")
            bregma.append(bregma_mm)
            fixed.append(fixed_ap)
            moving.append(moving_ap)
            weights.append(wt)
    if len(fixed) < min_count:
        raise ValueError(f"Need at least {min_count} enabled Bregma AP anchors; found {len(fixed)}.")
    order = np.argsort(fixed)
    fixed_arr = np.asarray(fixed, dtype=np.float32)[order]
    moving_arr = np.asarray(moving, dtype=np.float32)[order]
    if np.any(np.diff(fixed_arr) <= 0):
        raise ValueError("Enabled Bregma fixed_ap anchors must be strictly increasing after sorting.")
    if np.any(np.diff(moving_arr) < 0):
        raise ValueError("Enabled Bregma moving_ap anchors must be monotonic increasing; check AP direction/orientation.")
    return BregmaAPMap([names[i] for i in order], np.asarray(bregma, dtype=np.float32)[order], fixed_arr, moving_arr, np.asarray(weights, dtype=np.float32)[order])


def bregma_source_ap_for_target(lm: BregmaAPMap) -> np.ndarray:
    target_ap = np.arange(TARGET_SHAPE[0], dtype=np.float32)
    # np.interp clamps outside supplied anchors, which is conservative. Users should
    # add rostral/caudal Bregma anchors if they want full-range AP extrapolation.
    return np.interp(target_ap, lm.fixed_ap, lm.moving_ap).astype(np.float32)


def run_bregma_warp() -> int:
    lm = read_bregma_ap_map(2)
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    source, mat, context = load_source_for_auto_affine_context(fixed_mask)
    source_ap = bregma_source_ap_for_target(lm)
    anchor_span = [float(lm.fixed_ap.min()), float(lm.fixed_ap.max())]
    coverage_warning = None
    if anchor_span[0] > 0 or anchor_span[1] < TARGET_SHAPE[0] - 1:
        coverage_warning = (
            f"Bregma AP anchors cover fixed AP {anchor_span[0]:.1f}-{anchor_span[1]:.1f}; "
            f"outside this range source AP is clamped. Add rostral/caudal anchors for full-range AP mapping."
        )
        print(f"WARNING: {coverage_warning}")
    bregma_mm_present = bool(np.isfinite(lm.bregma_mm).any())
    # Directly sample the oriented WHS/Nissl source: AP is controlled only by the
    # Bregma anchor map. SI/LR use per-slice mask center/scale fitting so the
    # WHS/Nissl volume is packed into the Paxinos label volume more tightly than
    # the global affine fallback alone.
    source_mask = moving_brain_mask(source)
    fixed_center, moving_center, slice_scale, geometry_metrics = bregma_slice_geometry(fixed_mask, source_mask, source_ap)
    warped = map_auto_warp_chunked(
        source,
        np.zeros((TARGET_SHAPE[0], 3), dtype=np.float32),
        source_ap=source_ap,
        slice_scale=slice_scale,
        fixed_center=fixed_center,
        moving_center=moving_center,
    )
    write_tiff(WARP_PATH, warped)
    qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_bregma_warp")
    qc_overlay_slices(warped, fixed_mask, REPORT_DIR / "qc_warp", "ch03_bregma_warp")
    write_json({"bregma_warp": {
        "candidate": rel(WARP_PATH),
        "annotation_tiff": str(annotation_path),
        "fixed_orientation": fixed_orientation,
        "method": "direct_source_bregma_ap_map_slice_mask_si_lr_fit",
        "affine_context": context,
        "matrix_moving_to_fixed": mat.tolist(),
        **geometry_metrics,
        "anchor_count": len(lm.names),
        "anchor_fixed_ap_span": anchor_span,
        "coverage_warning": coverage_warning,
        "bregma_mm_present": bregma_mm_present,
        "anchors": [{"name": n, "bregma_mm": (float(b) if np.isfinite(b) else None), "fixed_ap": float(f), "moving_ap": float(m)} for n, b, f, m in zip(lm.names, lm.bregma_mm, lm.fixed_ap, lm.moving_ap)],
        "source_ap_min_max": [float(source_ap.min()), float(source_ap.max())],
        "source_ap_samples": [float(source_ap[i]) for i in np.linspace(0, TARGET_SHAPE[0] - 1, 9, dtype=int)],
    }})
    return 0

def affine_matrix(lm: LandmarkSet) -> np.ndarray:
    a = np.c_[lm.moving, np.ones(len(lm.moving))] * np.sqrt(lm.weights)[:, None]
    b = lm.fixed * np.sqrt(lm.weights)[:, None]
    coeff, *_ = np.linalg.lstsq(a, b, rcond=None)
    mat = np.eye(4); mat[:3, :3] = coeff[:3, :].T; mat[:3, 3] = coeff[3, :]
    return mat


def apply_affine(vol: np.ndarray, mat: np.ndarray) -> np.ndarray:
    inv = np.linalg.inv(mat)
    out = ndimage.affine_transform(vol, inv[:3, :3], offset=inv[:3, 3], output_shape=TARGET_SHAPE, order=1, mode="constant", cval=0)
    return np.clip(out, np.iinfo(np.uint16).min, np.iinfo(np.uint16).max).astype(np.uint16)


def apply_affine_nearest(vol: np.ndarray, mat: np.ndarray) -> np.ndarray:
    inv = np.linalg.inv(mat)
    out = ndimage.affine_transform(vol, inv[:3, :3], offset=inv[:3, 3], output_shape=TARGET_SHAPE, order=0, mode="constant", cval=0)
    return out.astype(vol.dtype, copy=False)


def apply_affine_nearest_shape(vol: np.ndarray, mat: np.ndarray, output_shape: tuple[int, int, int]) -> np.ndarray:
    inv = np.linalg.inv(mat)
    out = ndimage.affine_transform(vol, inv[:3, :3], offset=inv[:3, 3], output_shape=output_shape, order=0, mode="constant", cval=0)
    return out.astype(vol.dtype, copy=False)


def write_tiff(path: Path, vol: np.ndarray) -> None:
    ensure_dirs(); tifffile.imwrite(path, vol, bigtiff=True)
    print(f"Wrote {rel(path)} shape={vol.shape} dtype={vol.dtype}")


def qc_slices(vol: np.ndarray, outdir: Path, prefix: str, lm: LandmarkSet | None = None) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    plt = get_pyplot()
    for ap in np.linspace(60, TARGET_SHAPE[0] - 61, 6, dtype=int):
        img = exposure.rescale_intensity(vol[ap], out_range=(0, 1))
        fig, ax = plt.subplots(figsize=(6, 5)); ax.imshow(img, cmap="gray"); ax.set_title(f"{prefix} AP {ap}")
        if lm is not None:
            near = np.abs(lm.fixed[:, 0] - ap) <= 3
            ax.scatter(lm.fixed[near, 2], lm.fixed[near, 1], s=25, c="red")
        ax.set_axis_off(); fig.tight_layout(); fig.savefig(outdir / f"{prefix}_ap_{ap:03d}.png", dpi=120); plt.close(fig)


def qc_overlay_slices(vol: np.ndarray, fixed_mask: np.ndarray, outdir: Path, prefix: str) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    plt = get_pyplot()
    for ap in np.linspace(60, TARGET_SHAPE[0] - 61, 6, dtype=int):
        img = exposure.rescale_intensity(vol[ap], out_range=(0, 1))
        mask2d = fixed_mask[ap]
        boundary = mask2d ^ ndimage.binary_erosion(mask2d)
        rgb = np.dstack([img, img, img])
        rgb[boundary, 0] = 1.0
        rgb[boundary, 1] = 0.05
        rgb[boundary, 2] = 0.05
        fig, ax = plt.subplots(figsize=(6, 5))
        ax.imshow(rgb)
        ax.set_title(f"{prefix} AP {ap} red=Paxinos label outline")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(outdir / f"{prefix}_overlay_ap_{ap:03d}.png", dpi=120)
        plt.close(fig)


def label_boundary_2d(labels2d: np.ndarray) -> np.ndarray:
    boundary = np.zeros(labels2d.shape, dtype=bool)
    boundary[:-1, :] |= labels2d[:-1, :] != labels2d[1:, :]
    boundary[1:, :] |= labels2d[:-1, :] != labels2d[1:, :]
    boundary[:, :-1] |= labels2d[:, :-1] != labels2d[:, 1:]
    boundary[:, 1:] |= labels2d[:, :-1] != labels2d[:, 1:]
    boundary &= labels2d > 0
    return ndimage.binary_dilation(boundary, iterations=1)


def qc_label_overlap_slices(moving_labels: np.ndarray, fixed_labels: np.ndarray, outdir: Path, prefix: str) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    plt = get_pyplot()
    for ap in np.linspace(60, TARGET_SHAPE[0] - 61, 6, dtype=int):
        moving_mask = moving_labels[ap] > 0
        fixed_mask = fixed_labels[ap] > 0
        moving_boundary = label_boundary_2d(moving_labels[ap])
        fixed_boundary = label_boundary_2d(fixed_labels[ap])
        rgb = np.zeros((*moving_mask.shape, 3), dtype=np.float32)
        rgb[..., 0] = fixed_mask.astype(np.float32) * 0.18
        rgb[..., 1] = moving_mask.astype(np.float32) * 0.18
        rgb[fixed_boundary, 0] = 1.0
        rgb[moving_boundary, 1] = 1.0
        rgb[fixed_boundary & moving_boundary] = (1.0, 1.0, 0.0)
        fig, ax = plt.subplots(figsize=(6, 5))
        ax.imshow(rgb)
        ax.set_title(f"{prefix} AP {ap} red=Paxinos green=WHS labels yellow=overlap")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(outdir / f"{prefix}_label_overlap_ap_{ap:03d}.png", dpi=120)
        plt.close(fig)


def fill_and_smooth_offsets(offsets: np.ndarray, valid: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    filled = np.zeros_like(offsets, dtype=np.float32)
    valid_idx = np.flatnonzero(valid)
    if valid_idx.size < 2:
        return filled
    x = np.arange(len(offsets))
    for axis in range(offsets.shape[1]):
        filled[:, axis] = np.interp(x, valid_idx, offsets[valid, axis])
        filled[:, axis] = ndimage.gaussian_filter1d(filled[:, axis], sigma=sigma)
    return filled


def residual_edge_offsets_from_labels(fixed_labels: np.ndarray, moving_vol: np.ndarray, factor: int = 4) -> tuple[np.ndarray, dict]:
    small_shape = tuple(max(8, s // factor) for s in TARGET_SHAPE)
    zoom = np.asarray(small_shape) / np.asarray(TARGET_SHAPE)
    labels_small = ndimage.zoom(fixed_labels, zoom, order=0)
    moving_small = ndimage.zoom(moving_vol.astype(np.float32, copy=False), zoom, order=1)
    raw_offsets = np.zeros((small_shape[0], 2), dtype=np.float32)
    valid = np.zeros(small_shape[0], dtype=bool)
    errors = []
    window = np.outer(np.hanning(small_shape[1]), np.hanning(small_shape[2])).astype(np.float32)
    for ap in range(small_shape[0]):
        fixed_edge = label_boundary_2d(labels_small[ap]).astype(np.float32)
        moving_slice = exposure.rescale_intensity(moving_small[ap], out_range=(0, 1)).astype(np.float32)
        if fixed_edge.sum() < 32 or np.count_nonzero(moving_slice) < 32:
            continue
        moving_edge = filters.sobel(moving_slice)
        if float(moving_edge.max()) <= 0:
            continue
        fixed_img = fixed_edge * window
        moving_img = moving_edge * window
        try:
            shift, error, _ = phase_cross_correlation(fixed_img, moving_img, upsample_factor=1, normalization=None)
        except Exception:
            continue
        if not np.all(np.isfinite(shift)):
            continue
        raw_offsets[ap] = np.clip(shift[:2], -2.0, 2.0)
        valid[ap] = True
        errors.append(float(error))
    offsets_small = fill_and_smooth_offsets(raw_offsets, valid, sigma=2.0)
    ap_full = np.arange(TARGET_SHAPE[0]) / factor
    offsets_full = np.column_stack([
        np.interp(ap_full, np.arange(small_shape[0]), offsets_small[:, 0]) * factor,
        np.interp(ap_full, np.arange(small_shape[0]), offsets_small[:, 1]) * factor,
    ]).astype(np.float32)
    offsets_full = np.clip(offsets_full, -8.0, 8.0)
    metrics = {
        "edge_refine_factor": factor,
        "edge_refine_valid_slices": int(valid.sum()),
        "edge_refine_offset_si_lr_min": [float(offsets_full[:, 0].min()), float(offsets_full[:, 1].min())],
        "edge_refine_offset_si_lr_max": [float(offsets_full[:, 0].max()), float(offsets_full[:, 1].max())],
        "edge_refine_phase_error_mean": float(np.mean(errors)) if errors else None,
    }
    return offsets_full, metrics


def apply_slice_offsets_chunked(base: np.ndarray, offsets: np.ndarray, chunk: int = 32) -> np.ndarray:
    out = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    si = np.arange(TARGET_SHAPE[1])
    lr = np.arange(TARGET_SHAPE[2])
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = np.arange(start, stop)
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        sample = [coords[0], coords[1] - offsets[start:stop, 0, None, None], coords[2] - offsets[start:stop, 1, None, None]]
        out[start:stop] = ndimage.map_coordinates(base, sample, order=1, mode="constant", cval=0).astype(np.uint16)
    return out


def local_edge_flow_from_labels(fixed_labels: np.ndarray, moving_vol: np.ndarray, factor: int = 8) -> tuple[np.ndarray, dict]:
    small_shape = tuple(max(8, s // factor) for s in TARGET_SHAPE)
    zoom = np.asarray(small_shape) / np.asarray(TARGET_SHAPE)
    labels_small = ndimage.zoom(fixed_labels, zoom, order=0)
    moving_small = ndimage.zoom(moving_vol.astype(np.float32, copy=False), zoom, order=1)
    flow = np.zeros((small_shape[0], 2, small_shape[1], small_shape[2]), dtype=np.float32)
    valid = np.zeros(small_shape[0], dtype=bool)
    for ap in range(small_shape[0]):
        fixed_edge = label_boundary_2d(labels_small[ap]).astype(np.float32)
        moving_slice = exposure.rescale_intensity(moving_small[ap], out_range=(0, 1)).astype(np.float32)
        if fixed_edge.sum() < 48 or np.count_nonzero(moving_slice) < 48:
            continue
        moving_edge = filters.sobel(moving_slice).astype(np.float32)
        if float(moving_edge.max()) <= 0:
            continue
        fixed_edge = ndimage.gaussian_filter(fixed_edge, sigma=0.8)
        moving_edge = ndimage.gaussian_filter(moving_edge / max(float(moving_edge.max()), 1e-6), sigma=0.8)
        try:
            v, u = optical_flow_tvl1(fixed_edge, moving_edge, attachment=20, tightness=0.3, num_warp=3, num_iter=10)
        except Exception:
            continue
        flow[ap, 0] = np.clip(v, -0.75, 0.75)
        flow[ap, 1] = np.clip(u, -0.75, 0.75)
        valid[ap] = True
    for axis in range(2):
        flow[:, axis] = ndimage.gaussian_filter(flow[:, axis], sigma=(1.0, 1.0, 1.0))
    metrics = {
        "micro_warp_factor": factor,
        "micro_warp_valid_slices": int(valid.sum()),
        "micro_warp_flow_si_lr_min": [float(flow[:, 0].min() * factor), float(flow[:, 1].min() * factor)],
        "micro_warp_flow_si_lr_max": [float(flow[:, 0].max() * factor), float(flow[:, 1].max() * factor)],
        "micro_warp_note": "Conservative optional 2D slice-local edge-flow refinement; full-resolution flow is clipped to about +/-6 voxels before smoothing.",
    }
    return flow, metrics


def apply_local_flow_chunked(base: np.ndarray, flow_small: np.ndarray, factor: int, chunk: int = 16) -> np.ndarray:
    out = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    ap_small = np.arange(flow_small.shape[0])
    si_small = np.arange(flow_small.shape[2])
    lr_small = np.arange(flow_small.shape[3])
    si = np.arange(TARGET_SHAPE[1])
    lr = np.arange(TARGET_SHAPE[2])
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = np.arange(start, stop)
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        q_ap = np.clip(coords[0] / factor, 0, flow_small.shape[0] - 1)
        q_si = np.clip(coords[1] / factor, 0, flow_small.shape[2] - 1)
        q_lr = np.clip(coords[2] / factor, 0, flow_small.shape[3] - 1)
        flow_si = ndimage.map_coordinates(flow_small[:, 0], [q_ap, q_si, q_lr], order=1, mode="nearest") * factor
        flow_lr = ndimage.map_coordinates(flow_small[:, 1], [q_ap, q_si, q_lr], order=1, mode="nearest") * factor
        sample = [coords[0], coords[1] + flow_si, coords[2] + flow_lr]
        out[start:stop] = ndimage.map_coordinates(base, sample, order=1, mode="constant", cval=0).astype(np.uint16)
    return out


def run_auto_micro_warp() -> int:
    if not WARP_PATH.exists():
        raise FileNotFoundError(f"Warp candidate is missing: {rel(WARP_PATH)}. Run auto-warp first.")
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    fixed_labels, _ = orient_fixed_labels(tifffile.imread(annotation_path), annotation_path)
    base = tifffile.imread(WARP_PATH)
    flow_small, metrics = local_edge_flow_from_labels(fixed_labels, base, factor=8)
    refined = apply_local_flow_chunked(base, flow_small, factor=8)
    write_tiff(WARP_PATH, refined)
    qc_slices(refined, REPORT_DIR / "qc_warp", "ch03_auto_micro_warp")
    qc_overlay_slices(refined, fixed_mask, REPORT_DIR / "qc_warp", "ch03_auto_micro_warp")
    write_json({"auto_micro_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, **metrics}})
    return 0

def map_auto_warp_chunked(
    base: np.ndarray,
    delta: np.ndarray,
    source_ap: np.ndarray | None = None,
    slice_scale: np.ndarray | None = None,
    fixed_center: np.ndarray | None = None,
    moving_center: np.ndarray | None = None,
    chunk: int = 32,
) -> np.ndarray:
    out = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    si = np.arange(TARGET_SHAPE[1])
    lr = np.arange(TARGET_SHAPE[2])
    if source_ap is None:
        source_ap = np.arange(TARGET_SHAPE[0], dtype=np.float32)
    if slice_scale is None:
        slice_scale = np.ones((TARGET_SHAPE[0], 2), dtype=np.float32)
    if fixed_center is None:
        fixed_center = np.column_stack([
            np.arange(TARGET_SHAPE[0], dtype=np.float32) * 0 + (TARGET_SHAPE[1] - 1) / 2,
            np.arange(TARGET_SHAPE[0], dtype=np.float32) * 0 + (TARGET_SHAPE[2] - 1) / 2,
        ])
    if moving_center is None:
        moving_center = fixed_center - delta[:, 1:3]
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = source_ap[start:stop]
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        fc = fixed_center[start:stop]
        mc = moving_center[start:stop]
        sc = np.clip(slice_scale[start:stop], 0.80, 1.25)
        sample = [
            coords[0],
            mc[:, 0, None, None] + (coords[1] - fc[:, 0, None, None]) / sc[:, 0, None, None],
            mc[:, 1, None, None] + (coords[2] - fc[:, 1, None, None]) / sc[:, 1, None, None],
        ]
        out[start:stop] = ndimage.map_coordinates(base, sample, order=1, mode="constant", cval=0).astype(np.uint16)
    return out


def map_auto_warp_chunked_nearest(
    base: np.ndarray,
    delta: np.ndarray,
    source_ap: np.ndarray | None = None,
    slice_scale: np.ndarray | None = None,
    fixed_center: np.ndarray | None = None,
    moving_center: np.ndarray | None = None,
    chunk: int = 32,
) -> np.ndarray:
    out = np.zeros(TARGET_SHAPE, dtype=base.dtype)
    si = np.arange(TARGET_SHAPE[1])
    lr = np.arange(TARGET_SHAPE[2])
    if source_ap is None:
        source_ap = np.arange(TARGET_SHAPE[0], dtype=np.float32)
    if slice_scale is None:
        slice_scale = np.ones((TARGET_SHAPE[0], 2), dtype=np.float32)
    if fixed_center is None:
        fixed_center = np.column_stack([
            np.arange(TARGET_SHAPE[0], dtype=np.float32) * 0 + (TARGET_SHAPE[1] - 1) / 2,
            np.arange(TARGET_SHAPE[0], dtype=np.float32) * 0 + (TARGET_SHAPE[2] - 1) / 2,
        ])
    if moving_center is None:
        moving_center = fixed_center - delta[:, 1:3]
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = source_ap[start:stop]
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        fc = fixed_center[start:stop]
        mc = moving_center[start:stop]
        sc = np.clip(slice_scale[start:stop], 0.80, 1.25)
        sample = [
            coords[0],
            mc[:, 0, None, None] + (coords[1] - fc[:, 0, None, None]) / sc[:, 0, None, None],
            mc[:, 1, None, None] + (coords[2] - fc[:, 1, None, None]) / sc[:, 1, None, None],
        ]
        out[start:stop] = ndimage.map_coordinates(base, sample, order=0, mode="constant", cval=0).astype(base.dtype)
    return out



def slice_bbox_size(mask2d: np.ndarray) -> tuple[float, float] | None:
    coords = np.argwhere(mask2d)
    if coords.size == 0:
        return None
    span = coords.max(axis=0) - coords.min(axis=0) + 1
    return float(span[0]), float(span[1])


def slice_bbox_sizes(mask: np.ndarray) -> np.ndarray:
    sizes = np.full((mask.shape[0], 2), np.nan, dtype=np.float32)
    for ap in range(mask.shape[0]):
        size = slice_bbox_size(mask[ap])
        if size is not None:
            sizes[ap] = size
    return sizes


def label_mask_slice_warp_parameters(fixed_mask: np.ndarray, moving_mask: np.ndarray, factor: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
    fixed_small = downsample_mask(fixed_mask, factor=factor)
    moving_small = downsample_mask(moving_mask, factor=factor)
    source_ap_small = ap_dtw_mapping(fixed_small, moving_small)
    fixed_com = np.asarray([center_of_mass(fixed_small[i]) if fixed_small[i].any() else (np.nan, np.nan) for i in range(fixed_small.shape[0])])
    moving_com = np.asarray([center_of_mass(moving_small[i]) if moving_small[i].any() else (np.nan, np.nan) for i in range(moving_small.shape[0])])
    moving_valid = np.isfinite(moving_com[:, 0])
    fixed_valid = np.isfinite(fixed_com[:, 0])
    if fixed_valid.sum() < 8 or moving_valid.sum() < 8:
        raise ValueError(f"Label-volume warp needs at least 8 AP slices with fixed and moving labels; fixed={int(fixed_valid.sum())}, moving={int(moving_valid.sum())}.")
    moving_idx = np.flatnonzero(moving_valid)
    fixed_idx = np.flatnonzero(fixed_valid)
    mapped_moving_com = np.column_stack([
        np.interp(source_ap_small, moving_idx, moving_com[moving_valid, axis])
        for axis in range(2)
    ])
    delta_small = np.zeros((fixed_small.shape[0], 2), dtype=np.float32)
    delta_small[fixed_valid] = fixed_com[fixed_valid] - mapped_moving_com[fixed_valid]
    fixed_sizes = slice_bbox_sizes(fixed_small)
    moving_sizes = slice_bbox_sizes(moving_small)
    mapped_moving_sizes = np.column_stack([
        np.interp(source_ap_small, moving_idx, moving_sizes[moving_valid, axis])
        for axis in range(2)
    ])
    size_valid = fixed_valid & np.all(np.isfinite(fixed_sizes), axis=1)
    scale_valid = size_valid & np.all(mapped_moving_sizes > 1, axis=1)
    scale_small = np.ones((fixed_small.shape[0], 2), dtype=np.float32)
    scale_small[scale_valid] = fixed_sizes[scale_valid] / mapped_moving_sizes[scale_valid]
    fixed_com_filled = np.zeros_like(fixed_com, dtype=np.float32)
    for axis in range(2):
        fixed_com_filled[:, axis] = np.interp(np.arange(len(fixed_com)), fixed_idx, fixed_com[fixed_valid, axis])
        delta_small[:, axis] = np.interp(np.arange(len(delta_small)), fixed_idx, delta_small[fixed_valid, axis])
        delta_small[:, axis] = ndimage.gaussian_filter1d(delta_small[:, axis], sigma=2.0)
        valid_scale_idx = np.flatnonzero(scale_valid)
        if valid_scale_idx.size >= 2:
            scale_small[:, axis] = np.interp(np.arange(len(scale_small)), valid_scale_idx, scale_small[scale_valid, axis])
        scale_small[:, axis] = ndimage.gaussian_filter1d(np.clip(scale_small[:, axis], 0.80, 1.25), sigma=2.0)
    ap_scale = TARGET_SHAPE[0] / fixed_small.shape[0]
    si_scale = TARGET_SHAPE[1] / fixed_small.shape[1]
    lr_scale = TARGET_SHAPE[2] / fixed_small.shape[2]
    full_ap_index = np.arange(TARGET_SHAPE[0]) / ap_scale
    source_ap = np.interp(full_ap_index, np.arange(fixed_small.shape[0]), source_ap_small) * ap_scale
    delta = np.column_stack([
        np.zeros(TARGET_SHAPE[0]),
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), delta_small[:, 0]) * si_scale,
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), delta_small[:, 1]) * lr_scale,
    ])
    fixed_center_full = np.column_stack([
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), fixed_com_filled[:, 0]) * si_scale,
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), fixed_com_filled[:, 1]) * lr_scale,
    ])
    moving_center_full = fixed_center_full - delta[:, 1:3]
    slice_scale_full = np.column_stack([
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), scale_small[:, 0]),
        np.interp(full_ap_index, np.arange(fixed_small.shape[0]), scale_small[:, 1]),
    ])
    metrics = {
        "warp_downsample_factor": factor,
        "fixed_valid_ap_slices": int(fixed_valid.sum()),
        "moving_valid_ap_slices": int(moving_valid.sum()),
        "slice_scale_si_lr_min": [float(slice_scale_full[:, 0].min()), float(slice_scale_full[:, 1].min())],
        "slice_scale_si_lr_max": [float(slice_scale_full[:, 0].max()), float(slice_scale_full[:, 1].max())],
        "ap_source_min_max": [float(source_ap.min()), float(source_ap.max())],
        "ap_source_samples": [float(source_ap[i]) for i in np.linspace(0, TARGET_SHAPE[0] - 1, 9, dtype=int)],
    }
    return source_ap, delta, slice_scale_full, fixed_center_full, moving_center_full, metrics


def ap_profile(mask: np.ndarray) -> np.ndarray:
    profile = mask.sum(axis=(1, 2)).astype(np.float64)
    profile = ndimage.gaussian_filter1d(profile, sigma=1.0)
    if profile.max() > profile.min():
        profile = (profile - profile.min()) / (profile.max() - profile.min())
    return profile


def ap_extent_from_profile(profile: np.ndarray, fraction: float = 0.02) -> tuple[int, int]:
    if profile.max() <= 0:
        raise ValueError("Cannot determine AP extent from an empty profile.")
    active = np.flatnonzero(profile >= profile.max() * fraction)
    if active.size == 0:
        active = np.flatnonzero(profile > 0)
    if active.size == 0:
        raise ValueError("Cannot determine AP extent from an empty AP profile.")
    return int(active[0]), int(active[-1])


def ap_dtw_mapping(fixed_small: np.ndarray, moving_small: np.ndarray) -> np.ndarray:
    fixed_full = ap_profile(fixed_small)
    moving_full = ap_profile(moving_small)
    fixed_start, fixed_end = ap_extent_from_profile(fixed_full)
    moving_start, moving_end = ap_extent_from_profile(moving_full)
    fixed = fixed_full[fixed_start:fixed_end + 1]
    moving = moving_full[moving_start:moving_end + 1]
    n, m = len(fixed), len(moving)
    cost = (fixed[:, None] - moving[None, :]) ** 2
    dp = np.full((n, m), np.inf)
    prev = np.zeros((n, m, 2), dtype=np.int16) - 1
    dp[0, 0] = cost[0, 0]
    for i in range(n):
        for j in range(m):
            if i == 0 and j == 0:
                continue
            options = []
            if i > 0 and j > 0:
                options.append((dp[i - 1, j - 1], i - 1, j - 1))
            if i > 0:
                options.append((dp[i - 1, j] + 0.05, i - 1, j))
            if j > 0:
                options.append((dp[i, j - 1] + 0.05, i, j - 1))
            best = min(options, key=lambda x: x[0])
            dp[i, j] = cost[i, j] + best[0]
            prev[i, j] = (best[1], best[2])
    pairs = []
    i, j = n - 1, m - 1
    while i >= 0 and j >= 0:
        pairs.append((i + fixed_start, j + moving_start))
        pi, pj = prev[i, j]
        if pi < 0 or pj < 0:
            break
        i, j = int(pi), int(pj)
    by_fixed = {i: [] for i in range(len(fixed_full))}
    for i, j in pairs:
        by_fixed[i].append(j)
    known_i = [fixed_start, fixed_end]
    known_j = [float(moving_start), float(moving_end)]
    for i in range(len(fixed_full)):
        if by_fixed[i]:
            known_i.append(i)
            known_j.append(float(np.mean(by_fixed[i])))
    order = np.argsort(known_i)
    known_i = np.asarray(known_i)[order]
    known_j = np.asarray(known_j)[order]
    mapping = np.interp(np.arange(len(fixed_full)), known_i, known_j)
    return np.maximum.accumulate(mapping).astype(np.float32)


def ap_profile_cdf(mask: np.ndarray) -> np.ndarray:
    profile = mask.sum(axis=(1, 2)).astype(np.float64)
    profile = ndimage.gaussian_filter1d(profile, sigma=1.0)
    profile = np.maximum(profile, 0) + 1e-6
    cdf = np.cumsum(profile)
    return cdf / cdf[-1]


def ap_cdf_mapping(fixed_small: np.ndarray, moving_small: np.ndarray) -> np.ndarray:
    fixed_cdf = ap_profile_cdf(fixed_small)
    moving_cdf = ap_profile_cdf(moving_small)
    moving_index = np.arange(len(moving_cdf), dtype=np.float32)
    return np.interp(fixed_cdf, moving_cdf, moving_index).astype(np.float32)


def map_displacement_chunked(base: np.ndarray, disp: np.ndarray, chunk: int = 32) -> np.ndarray:
    out = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    si = np.arange(TARGET_SHAPE[1])
    lr = np.arange(TARGET_SHAPE[2])
    for start in range(0, TARGET_SHAPE[0], chunk):
        stop = min(start + chunk, TARGET_SHAPE[0])
        ap = np.arange(start, stop)
        coords = np.meshgrid(ap, si, lr, indexing="ij")
        sample = [coords[i] - disp[i, start:stop] for i in range(3)]
        out[start:stop] = ndimage.map_coordinates(base, sample, order=1, mode="constant", cval=0).astype(np.uint16)
    return out




def structure_term_to_ids(path: Path) -> dict[str, set[int]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("structures", [])
    out: dict[str, set[int]] = {}
    for item in rows:
        try:
            sid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        for key in ("acronym", "name"):
            term = str(item.get(key, "")).strip().lower()
            if term and term not in {"root", "whole brain", "background"}:
                out.setdefault(term, set()).add(sid)
    return out


def orient_moving_labels_like_source(labels: np.ndarray, extra_flips: Iterable[int]) -> np.ndarray:
    if labels.ndim != 3:
        raise ValueError(f"Expected 3D WHS annotation, got shape {labels.shape} at {WHS_ANNOTATION_PATH}")
    out = np.transpose(labels, PERM)
    out = np.rot90(out, k=PRE_RESIZE_ROT90, axes=(1, 2))
    for axis in TARGET_FLIPS:
        out = np.flip(out, axis=axis)
    out = apply_extra_flips(out, extra_flips)
    if out.shape != TARGET_SHAPE:
        out = transform.resize(out, TARGET_SHAPE, order=0, preserve_range=True, anti_aliasing=False).astype(labels.dtype)
    return out.astype(np.int32, copy=False)


def label_centroids(labels: np.ndarray, ids: set[int]) -> tuple[np.ndarray, int] | None:
    ids = {int(i) for i in ids if int(i) != 0}
    if not ids:
        return None
    mask = np.isin(labels, list(ids))
    count = int(mask.sum())
    if count < 32:
        return None
    return center_of_mass(mask), count


@dataclass
class LabelLandmarkSet:
    names: list[str]
    fixed: np.ndarray
    moving: np.ndarray
    weights: np.ndarray


def build_label_landmarks(min_count: int = 12, max_count: int = 160) -> tuple[LabelLandmarkSet, dict, np.ndarray, np.ndarray, Path]:
    fixed_labels_path = find_annotation_path(required=True)
    fixed_labels, fixed_orientation = orient_fixed_labels(tifffile.imread(fixed_labels_path), fixed_labels_path)
    if not WHS_ANNOTATION_PATH.exists():
        raise FileNotFoundError(f"Missing WHS annotation for label-guided registration: {WHS_ANNOTATION_PATH}. Set V53_WHS_ANNOTATION if needed.")
    fixed_mask = fixed_labels > 0
    source, mat, context = load_source_for_auto_affine_context(fixed_mask)
    extra_flips = context.get("extra_flips_after_v53_orientation", [])
    if not extra_flips and isinstance(context.get("source_orientation"), dict):
        extra_flips = ((context["source_orientation"].get("selected") or {}).get("extra_flips_after_v53_orientation", []))
    moving_labels = orient_moving_labels_like_source(tifffile.imread(WHS_ANNOTATION_PATH), extra_flips)
    fixed_terms = structure_term_to_ids(fixed_labels_path.parent / "structures.json")
    moving_terms = structure_term_to_ids(WHS_ANNOTATION_PATH.parent / "structures.json")
    common = sorted(set(fixed_terms) & set(moving_terms))
    names: list[str] = []
    fixed_pts: list[np.ndarray] = []
    moving_pts: list[np.ndarray] = []
    weights: list[float] = []
    for term in common:
        fc = label_centroids(fixed_labels, fixed_terms[term])
        mc = label_centroids(moving_labels, moving_terms[term])
        if fc is None or mc is None:
            continue
        fpt, fcount = fc
        mpt, mcount = mc
        names.append(term)
        fixed_pts.append(fpt)
        moving_pts.append(mpt)
        weights.append(float(np.sqrt(min(fcount, mcount))))
    if len(names) < min_count:
        raise ValueError(f"Need at least {min_count} matched WHS/Paxinos label centroids; found {len(names)}. Check WHS annotation/structures paths.")
    order = np.argsort(weights)[::-1][:max_count]
    lm = LabelLandmarkSet(
        [names[i] for i in order],
        np.asarray([fixed_pts[i] for i in order], dtype=np.float32),
        np.asarray([moving_pts[i] for i in order], dtype=np.float32),
        np.asarray([weights[i] for i in order], dtype=np.float32),
    )
    meta = {
        "fixed_annotation": str(fixed_labels_path),
        "moving_annotation": str(WHS_ANNOTATION_PATH),
        "common_term_count": len(common),
        "matched_centroid_count": len(names),
        "used_centroid_count": len(lm.names),
        "fixed_orientation": fixed_orientation,
        "affine_context": context,
        "mask_affine_matrix_moving_to_fixed": mat.tolist(),
        "example_terms": lm.names[:25],
    }
    return lm, meta, source, fixed_mask, fixed_labels_path




def robust_weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = np.maximum(weights[order], 1e-6)
    cdf = np.cumsum(weights) / np.sum(weights)
    return float(np.interp(q / 100.0, cdf, values))


def bounded_axis_affine_from_points(moving: np.ndarray, fixed: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, dict]:
    """Fit a no-shear, no-reflection AP/SI/LR affine from label centroids.

    Full affine fits are too dangerous with cross-atlas label correspondences: a
    few ambiguous bilateral/common acronyms can introduce shears or reflections
    and collapse one axis. This constrained fit keeps anatomy upright and only
    estimates robust per-axis scale plus translation.
    """
    mat = np.eye(4, dtype=np.float64)
    metrics = {"axis_scale_ap_si_lr": [], "axis_translation_ap_si_lr": [], "axis_fit_mode": "weighted_p05_p95_scale_weighted_median_translation_no_shear_no_reflection"}
    for axis in range(3):
        m05 = robust_weighted_percentile(moving[:, axis], weights, 5)
        m95 = robust_weighted_percentile(moving[:, axis], weights, 95)
        f05 = robust_weighted_percentile(fixed[:, axis], weights, 5)
        f95 = robust_weighted_percentile(fixed[:, axis], weights, 95)
        raw_scale = (f95 - f05) / max(m95 - m05, 1.0)
        scale = float(np.clip(raw_scale, 0.60, 1.70))
        residual = fixed[:, axis] - scale * moving[:, axis]
        translation = robust_weighted_percentile(residual, weights, 50)
        mat[axis, axis] = scale
        mat[axis, 3] = translation
        metrics["axis_scale_ap_si_lr"].append(scale)
        metrics["axis_translation_ap_si_lr"].append(float(translation))
    return mat, metrics

def weighted_affine_from_points(moving: np.ndarray, fixed: np.ndarray, weights: np.ndarray) -> np.ndarray:
    w = np.sqrt(np.maximum(weights, 1e-6))[:, None]
    a = np.c_[moving, np.ones(len(moving))] * w
    b = fixed * w
    coeff, *_ = np.linalg.lstsq(a, b, rcond=None)
    mat = np.eye(4)
    mat[:3, :3] = coeff[:3, :].T
    mat[:3, 3] = coeff[3, :]
    return mat


def run_labels_affine() -> int:
    lm, meta, source, fixed_mask, _ = build_label_landmarks()
    label_mat, fit_metrics = bounded_axis_affine_from_points(lm.moving, lm.fixed, lm.weights)
    mat = np.asarray(meta["mask_affine_matrix_moving_to_fixed"], dtype=np.float64)
    out = apply_affine(source, mat)
    write_tiff(AFFINE_PATH, out)
    qc_slices(out, REPORT_DIR / "qc_affine", "ch03_labels_affine")
    qc_overlay_slices(out, fixed_mask, REPORT_DIR / "qc_affine", "ch03_labels_affine")
    residual = (np.c_[lm.moving, np.ones(len(lm.moving))] @ mat.T)[:, :3] - lm.fixed
    write_json({"labels_affine": {"candidate": rel(AFFINE_PATH), "method": "mask_affine_with_whs_paxinos_label_centroid_audit", "matrix_moving_to_fixed": mat.tolist(), "label_centroid_bounded_axis_matrix_diagnostic": label_mat.tolist(), "label_centroid_bounded_axis_diagnostic": fit_metrics, "rms_error_voxels": float(np.sqrt(np.average(np.sum(residual ** 2, axis=1), weights=lm.weights))), **meta}})
    return 0


def run_labels_warp() -> int:
    # Important safety choice:
    # Matched WHS/Paxinos structure names are useful for a reproducible global
    # placement, but they are not reliable enough to drive a dense non-linear
    # TPS deformation. In real local tests, ambiguous cross-atlas correspondences
    # produced folded/tilted Ch03 volumes even with clipped displacement fields.
    # The label-centroid affine itself is also only diagnostic because real
    # testing showed it can drive AP/SI/LR scales to the safety clip limits.
    # Therefore this command writes the mask-based affine result to the "warp"
    # candidate path, so downstream accept/install commands can still use the
    # usual workflow without risking catastrophic local folding. Use explicit
    # hand-picked landmarks for any later non-linear deformation.
    lm, meta, source, fixed_mask, _ = build_label_landmarks()
    label_mat, fit_metrics = bounded_axis_affine_from_points(lm.moving, lm.fixed, lm.weights)
    mat = np.asarray(meta["mask_affine_matrix_moving_to_fixed"], dtype=np.float64)
    base = apply_affine(source, mat)
    aff_moving = (np.c_[lm.moving, np.ones(len(lm.moving))] @ mat.T)[:, :3]
    residual = aff_moving - lm.fixed
    write_tiff(WARP_PATH, base)
    qc_slices(base, REPORT_DIR / "qc_warp", "ch03_labels_warp_safe_affine")
    qc_overlay_slices(base, fixed_mask, REPORT_DIR / "qc_warp", "ch03_labels_warp_safe_affine")
    write_json({"labels_warp": {"candidate": rel(WARP_PATH), "method": "mask_affine_only_with_whs_paxinos_label_centroid_audit_no_tps_safety", "matrix_moving_to_fixed": mat.tolist(), "label_centroid_bounded_axis_matrix_diagnostic": label_mat.tolist(), "label_centroid_bounded_axis_diagnostic": fit_metrics, "nonlinear_tps_disabled": True, "safety_note": "Automatic WHS/Paxinos label-name centroids are audited but not trusted to set the transform; this command writes the mask-based affine result to avoid bad label-centroid scales or folded Ch03 volumes.", "rms_error_voxels": float(np.sqrt(np.average(np.sum(residual ** 2, axis=1), weights=lm.weights))), **meta}})
    return 0


def run_labels_volume_affine() -> int:
    """Transform the WHS annotation itself into Paxinos space for label-vs-label QC.

    This does not write or modify the Paxinos annotation. It is a diagnostic pass:
    if the WHS label volume cannot be placed plausibly onto the Paxinos label
    volume, then applying the same parameters to Nissl cannot yield a trustworthy
    region-wise Ch03 result.
    """
    fixed_labels_path = find_annotation_path(required=True)
    fixed_labels, fixed_orientation = orient_fixed_labels(tifffile.imread(fixed_labels_path), fixed_labels_path)
    fixed_mask = fixed_labels > 0
    if not WHS_ANNOTATION_PATH.exists():
        raise FileNotFoundError(f"Missing WHS annotation for label-volume registration: {WHS_ANNOTATION_PATH}. Set V53_WHS_ANNOTATION if needed.")
    moving_labels, mat, label_orientation = choose_best_label_volume_affine(tifffile.imread(WHS_ANNOTATION_PATH), fixed_mask)
    moved_labels = apply_affine_nearest(moving_labels, mat)
    write_tiff(WHS_LABEL_AFFINE_PATH, moved_labels.astype(np.uint16, copy=False))
    qc_label_overlap_slices(moved_labels, fixed_labels, REPORT_DIR / "qc_label_volume", "whs_labels_to_paxinos_affine")
    write_json({"labels_volume_affine": {
        "candidate": rel(WHS_LABEL_AFFINE_PATH),
        "method": "transform_whs_annotation_to_paxinos_with_mask_affine_nearest_neighbor",
        "fixed_annotation": str(fixed_labels_path),
        "moving_annotation": str(WHS_ANNOTATION_PATH),
        "fixed_orientation": fixed_orientation,
        "label_orientation": label_orientation,
        "matrix_moving_to_fixed": mat.tolist(),
        "qc_dir": rel(REPORT_DIR / "qc_label_volume"),
        "safety_note": "Diagnostic only; Paxinos annotation is not modified. Green=WHS transformed labels, red=Paxinos labels. Orientation and affine are selected from WHS-label-mask to Paxinos-label-mask overlap, not from Nissl intensity.",
    }})
    return 0


def run_labels_volume_warp() -> int:
    fixed_labels_path = find_annotation_path(required=True)
    fixed_labels, fixed_orientation = orient_fixed_labels(tifffile.imread(fixed_labels_path), fixed_labels_path)
    fixed_mask = fixed_labels > 0
    if not WHS_LABEL_AFFINE_PATH.exists():
        run_labels_volume_affine()
    moved_affine = tifffile.imread(WHS_LABEL_AFFINE_PATH)
    moving_mask = moved_affine > 0
    factor = 8
    source_ap, delta, slice_scale, fixed_center, moving_center, metrics = label_mask_slice_warp_parameters(fixed_mask, moving_mask, factor=factor)
    warped_labels = map_auto_warp_chunked_nearest(
        moved_affine,
        delta,
        source_ap=source_ap,
        slice_scale=slice_scale,
        fixed_center=fixed_center,
        moving_center=moving_center,
    )
    write_tiff(WHS_LABEL_WARP_PATH, warped_labels.astype(np.uint16, copy=False))
    qc_label_overlap_slices(warped_labels, fixed_labels, REPORT_DIR / "qc_label_volume_warp", "whs_labels_to_paxinos_warp")
    write_json({"labels_volume_warp": {
        "candidate": rel(WHS_LABEL_WARP_PATH),
        "method": "affine_plus_ap_dtw_slice_center_scale_whs_labels_to_paxinos_labels",
        "fixed_annotation": str(fixed_labels_path),
        "moving_annotation_affine_candidate": rel(WHS_LABEL_AFFINE_PATH),
        "fixed_orientation": fixed_orientation,
        "qc_dir": rel(REPORT_DIR / "qc_label_volume_warp"),
        "safety_note": "Diagnostic only; Paxinos annotation is not modified. This tests whether WHS labels can be aligned before applying any equivalent transform to Nissl.",
        **metrics,
    }})
    return 0


def run_affine() -> int:
    lm = read_landmarks(4); vol = load_oriented_source(); mat = affine_matrix(lm); out = apply_affine(vol, mat)
    write_tiff(AFFINE_PATH, out); qc_slices(out, REPORT_DIR / "qc_affine", "ch03_affine", lm)
    residual = (np.c_[lm.moving, np.ones(len(lm.moving))] @ mat.T)[:, :3] - lm.fixed
    write_json({"affine": {"candidate": rel(AFFINE_PATH), "landmark_count": len(lm.names), "matrix_moving_to_fixed": mat.tolist(), "rms_error_voxels": float(np.sqrt(np.mean(residual ** 2)))}})
    return 0


def run_warp() -> int:
    lm = read_landmarks(6)
    base = tifffile.imread(AFFINE_PATH) if AFFINE_PATH.exists() else apply_affine(load_oriented_source(), affine_matrix(lm))
    mat = affine_matrix(lm); aff_moving = (np.c_[lm.moving, np.ones(len(lm.moving))] @ mat.T)[:, :3]
    displacement = lm.fixed - aff_moving
    grid_shape = tuple(max(8, s // 16) for s in TARGET_SHAPE)
    axes = [np.linspace(0, s - 1, n) for s, n in zip(TARGET_SHAPE, grid_shape)]
    mesh = np.meshgrid(*axes, indexing="ij"); pts = np.column_stack([m.ravel() for m in mesh])
    rbf = RBFInterpolator(lm.fixed, displacement, kernel="thin_plate_spline", smoothing=1.0 / np.mean(lm.weights))
    disp_small = rbf(pts).reshape(*grid_shape, 3)
    disp = np.stack([ndimage.zoom(disp_small[..., i], np.array(TARGET_SHAPE) / np.array(grid_shape), order=1) for i in range(3)]).astype(np.float32)
    warped = map_displacement_chunked(base, disp)
    write_tiff(WARP_PATH, warped); qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_warp", lm)
    write_json({"warp": {"candidate": rel(WARP_PATH), "landmark_count": len(lm.names), "method": "affine_plus_downsampled_thin_plate_rbf", "control_grid_shape": grid_shape}})
    return 0


def accept(kind: str) -> int:
    src = {"affine": AFFINE_PATH, "warp": WARP_PATH}.get(kind)
    if src is None:
        raise ValueError("landmarks-accept requires 'affine' or 'warp'.")
    if not src.exists():
        raise FileNotFoundError(f"Candidate is missing: {rel(src)}")
    ensure_dirs(); shutil.copy2(src, ACTIVE_PATH); print(f"Accepted {kind}: {rel(ACTIVE_PATH)}")
    write_json({"accepted": {"kind": kind, "source": rel(src), "active": rel(ACTIVE_PATH)}})
    return 0


def write_ch03_nifti(active: np.ndarray, atlas_dir: Path, target_name: str) -> Path:
    annotation_nii = atlas_dir / "annotation.nii.gz"
    if annotation_nii.exists():
        ann_img = nib.load(str(annotation_nii))
        affine = ann_img.affine
        header = ann_img.header.copy()
    else:
        affine = np.diag([0.04, 0.04, 0.04, 1.0])
        header = None
    out = atlas_dir / f"{target_name}.nii.gz"
    img = nib.Nifti1Image(active.astype(np.uint16, copy=False), affine=affine, header=header)
    nib.save(img, str(out))
    return out


def install_one_ch03_target(atlas_dir: Path, index: int, active: np.ndarray) -> dict:
    metadata_path = atlas_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"metadata.json is missing in atlas dir: {atlas_dir}")
    target_name = "waxholm_anatomy_reference"
    target_tiff = atlas_dir / f"{target_name}.tiff"
    target_nii = atlas_dir / f"{target_name}.nii.gz"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    backup = REPORT_DIR / f"metadata_before_ch03_install_{index}.json"
    shutil.copy2(metadata_path, backup)
    shutil.copy2(ACTIVE_PATH, target_tiff)
    target_nii = write_ch03_nifti(active, atlas_dir, target_name)
    refs = metadata.get("additional_references", [])
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list):
        refs = []
    if target_name not in refs:
        refs.append(target_name)
    metadata["additional_references"] = refs
    files = metadata.get("files") if isinstance(metadata.get("files"), dict) else {}
    files[f"{target_name}_tiff"] = f"{target_name}.tiff"
    files[f"{target_name}_nifti"] = f"{target_name}.nii.gz"
    metadata["files"] = files
    metadata["v53_optional_ch03"] = {
        "installed": True,
        "reference_name": target_name,
        "source_active_asset": rel(ACTIVE_PATH),
        "installed_tiff": str(target_tiff),
        "installed_nifti": str(target_nii),
        "metadata_backup": rel(backup),
        "note": "Optional experimental Ch03 WHS/Nissl reference installed by explicit ch03-install command.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "atlas_dir": str(atlas_dir),
        "installed_tiff": str(target_tiff),
        "installed_nifti": str(target_nii),
        "metadata_json": str(metadata_path),
        "metadata_backup": rel(backup),
        "additional_references": refs,
    }




def export_whs_slices() -> int:
    """Export native WHS/Nissl 39 µm AP planes without spatial resampling.

    Only deterministic axis orientation is applied.  ABBA receives the original
    number and in-plane shape of WHS planes and must place them at +0.039 mm.  ABBA
    is then responsible for registration/resampling into the 40 µm Paxinos atlas.
    """
    vol = load_native_oriented_source().astype(np.uint16, copy=False)
    outdir = OPTIONAL_DIR / "whs_nissl_slices_39um_ap"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    # The oriented WHS array runs caudal -> rostral, whereas the Paxinos target
    # index runs rostral -> caudal (target AP 0 is anterior).  Export in target
    # order so an alphabetically/numerically stacked ABBA result cannot silently
    # become AP-reversed.  Keep the original source index in the manifest for a
    # complete audit trail.
    index_width = max(3, len(str(vol.shape[0] - 1)))
    manifest_lines = ["export_ap_index,source_oriented_ap_index,recommended_axis_offset_mm,filename"]
    for export_ap in range(vol.shape[0]):
        source_ap = vol.shape[0] - 1 - export_ap
        name = f"whs_nissl_ap_{export_ap:0{index_width}d}.tiff"
        tifffile.imwrite(outdir / name, vol[source_ap], bigtiff=False)
        manifest_lines.append(f"{export_ap},{source_ap},{export_ap * 0.039:.3f},{name}")
    (outdir / "manifest.csv").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    write_json({"export_whs_slices": {"source": str(SOURCE_PATH), "outdir": rel(outdir), "native_shape_ap_si_lr": [int(v) for v in vol.shape], "slice_count": int(vol.shape[0]), "slice_shape_si_lr": [int(vol.shape[1]), int(vol.shape[2])], "filename_index_width": index_width, "spatial_resampling_applied": False, "orientation_only": {"perm": list(PERM), "rot90": PRE_RESIZE_ROT90, "flips": list(TARGET_FLIPS)}, "source_voxel_size_mm": 0.039, "recommended_abba_axis_increment_mm": 0.039, "axis_increment_embedded_in_slice_tiffs": False, "axis_position_note": "The TIFFs are plain native-resolution 2D planes. ABBA/QuPath must assign the +0.039 mm increment during import; add one common start offset to move the whole series.", "export_ap_direction": "the lowest filename AP index is rostral/anterior; the highest filename AP index is caudal/posterior", "source_ap_mapping": "source_oriented_ap_index = slice_count - 1 - export_ap_index", "note": "No resize or spatial resampling is applied. ABBA must register the native 39 um WHS series into the 40 um Paxinos target atlas."}})
    print(f"Exported {vol.shape[0]} WHS/Nissl AP slices to: {rel(outdir)}")
    print(f"Slice shape SI/LR: {vol.shape[1]} x {vol.shape[2]}")
    print(f"AP direction: whs_nissl_ap_{0:0{index_width}d}.tiff is anterior; whs_nissl_ap_{vol.shape[0] - 1:0{index_width}d}.tiff is posterior")
    print("Spatial resampling: NONE (native WHS plane count and in-plane shape preserved)")
    print("Recommended ABBA axis increment: +0.039 mm")
    print("IMPORTANT: axis positions are not embedded in the 2D TIFFs; ABBA/QuPath must assign the increment during import. See manifest.csv for expected relative offsets.")
    return 0


def export_whs_paxinos_slices() -> int:
    """Export the complete WHS/Nissl AP extent on a regular 40 µm grid.

    No anterior or posterior planes are discarded, including completely black
    planes.  The native 39 µm AP axis is sampled every 40 µm across its full
    physical extent. In-plane WHS pixels are not resized. The images are written
    as display-safe 8-bit TIFFs because ABBA/BigDataViewer commonly opens raw
    16-bit bright-field exports with an unsuitable 8-bit display range.
    """
    source = load_native_oriented_source()[::-1].astype(np.float32, copy=False)
    if source.shape[0] == 0 or not np.any(source):
        raise ValueError("Native WHS/Nissl source contains no non-black AP planes.")
    intensity_view = source.reshape(-1)
    sample_step = max(1, intensity_view.size // 2_000_000)
    intensity_sample = intensity_view[::sample_step]
    intensity_sample = intensity_sample[intensity_sample > 0]
    if intensity_sample.size == 0:
        raise ValueError("Native WHS/Nissl source contains no positive intensity samples.")
    intensity_low, intensity_high = np.percentile(intensity_sample, [0.5, 99.5])
    if not np.isfinite(intensity_low) or not np.isfinite(intensity_high) or intensity_high <= intensity_low:
        intensity_low = float(intensity_sample.min())
        intensity_high = float(intensity_sample.max())
    if intensity_high <= intensity_low:
        raise ValueError("Native WHS/Nissl source has no usable intensity range for registration images.")

    # Preserve the complete physical AP extent.  A 40 µm output plane at
    # position x samples the native 39 µm volume at x / 39 µm.  Do not use
    # the non-black range or the Paxinos label range to shorten the series.
    source_ap = np.arange(
        int(np.floor((source.shape[0] - 1) * 39.0 / 40.0)) + 1,
        dtype=np.float64,
    ) * (40.0 / 39.0)
    outdir = OPTIONAL_DIR / "whs_nissl_slices_paxinos_40um_ap"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    index_width = max(3, len(str(int(source_ap.size - 1))))
    manifest_lines = [
        "series_index,axis_offset_mm,source_native_ap_float,filename"
    ]
    for series_index, moving_ap in enumerate(source_ap):
        lo = int(np.floor(moving_ap))
        hi = int(np.ceil(moving_ap))
        alpha = np.float32(moving_ap - lo)
        image = source[lo] if lo == hi else source[lo] * (1.0 - alpha) + source[hi] * alpha
        positive = image > 0
        normalized = np.clip((image - intensity_low) / (intensity_high - intensity_low), 0.0, 1.0)
        image = np.zeros(image.shape, dtype=np.uint8)
        image[positive] = 1 + np.round(normalized[positive] * 254.0).astype(np.uint8)
        name = f"whs_nissl_40um_ap_{series_index:0{index_width}d}.tiff"
        tifffile.imwrite(
            outdir / name,
            image,
            bigtiff=False,
            photometric="minisblack",
            resolution=(10000.0 / 39.0, 10000.0 / 39.0),
            resolutionunit="CENTIMETER",
            ome=True,
            metadata={
                "axes": "YX",
                "PhysicalSizeX": 39.0,
                "PhysicalSizeXUnit": "µm",
                "PhysicalSizeY": 39.0,
                "PhysicalSizeYUnit": "µm",
            },
        )
        manifest_lines.append(
            f"{series_index},{series_index * 0.040:.3f},{moving_ap:.6f},{name}"
        )
    (outdir / "manifest.csv").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    write_json({"export_whs_paxinos_slices": {
        "source": str(SOURCE_PATH),
        "outdir": rel(outdir),
        "method": "complete_native_whs_extent_linear_ap_resample_39um_to_40um",
        "source_native_shape_ap_si_lr": [int(v) for v in source.shape],
        "source_native_ap_min_max": [0, int(source.shape[0] - 1)],
        "source_sampled_ap_min_max": [float(source_ap[0]), float(source_ap[-1])],
        "exported_plane_count": int(source_ap.size),
        "exported_slice_shape_si_lr": [int(source.shape[1]), int(source.shape[2])],
        "output_spacing_mm": 0.040,
        "embedded_pixel_size_um": [39.0, 39.0],
        "output_dtype": "uint8",
        "intensity_normalization": "global_nonzero_p0.5_p99.5_to_display_safe_uint8",
        "intensity_source_low_high": [float(intensity_low), float(intensity_high)],
        "recommended_abba_display_range": [0, 255],
        "in_plane_spatial_resampling_applied": False,
        "ap_resampling_applied": True,
        "black_end_planes_preserved": True,
        "note": "The complete WHS AP extent is exported at 40 um without cropping black ends. ABBA should import the numerically sorted series at +0.040 mm and set its common AP offset there.",
    }})
    print(f"Exported {source_ap.size} WHS/Nissl images to: {rel(outdir)}")
    print(f"Complete native WHS AP range retained: 0-{source.shape[0] - 1} (black end planes included)")
    print("AP resampling: complete native 39 um WHS span -> regular 40 um planes")
    print("In-plane resampling: NONE (ABBA performs the remaining 2D registration)")
    print("In-plane calibration embedded in TIFF/OME metadata: 39 x 39 um per pixel")
    print(f"Intensity normalization: global non-zero p0.5={intensity_low:.3f} to p99.5={intensity_high:.3f} -> display-safe uint8")
    print("ABBA display range: use min=0 and max=255; then narrow max only if more contrast is needed")
    print("Recommended ABBA axis increment: +0.040 mm")
    return 0


WHS_EXPORT_INDEX_RE = re.compile(r"whs_nissl_(?:40um_)?ap_(\d+)", re.IGNORECASE)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def summarize_bdv_json(path: Path) -> dict:
    """Summarize an ABBA/BDV JSON export without interpreting Java transforms."""
    result = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "valid_json": False,
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["parse_error"] = str(exc)
        return result

    type_counts: Counter[str] = Counter()
    source_classes: Counter[str] = Counter()
    source_names: list[str] = []
    source_ids: list[int] = []
    spline_point_counts: list[dict] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            type_name = value.get("type")
            if isinstance(type_name, str):
                type_counts[type_name] += 1
                if type_name == "ThinplateSplineTransform":
                    src = value.get("srcPts")
                    tgt = value.get("tgtPts")
                    spline_point_counts.append({
                        "src_rows": len(src) if isinstance(src, list) else None,
                        "tgt_rows": len(tgt) if isinstance(tgt, list) else None,
                    })
            source_class = value.get("source_class")
            if isinstance(source_class, str):
                source_classes[source_class] += 1
            source_name = value.get("source_name")
            if isinstance(source_name, str):
                source_names.append(source_name)
            source_id = value.get("source_id")
            if isinstance(source_id, int):
                source_ids.append(source_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    indexes = sorted({int(match.group(1)) for name in source_names for match in WHS_EXPORT_INDEX_RE.finditer(name)})
    result.update({
        "valid_json": True,
        "root_type": type(payload).__name__,
        "root_item_count": len(payload) if isinstance(payload, (list, dict)) else None,
        "source_name_count": len(source_names),
        "unique_source_name_count": len(set(source_names)),
        "source_id_min_max": [min(source_ids), max(source_ids)] if source_ids else None,
        "source_classes": dict(source_classes.most_common()),
        "transform_types": dict(type_counts.most_common()),
        "thinplate_spline_count": type_counts.get("ThinplateSplineTransform", 0),
        "thinplate_spline_point_shapes": spline_point_counts,
        "whs_export_indexes": indexes,
        "whs_export_index_min_max": [indexes[0], indexes[-1]] if indexes else None,
        "whs_export_index_count": len(indexes),
        "missing_indexes_within_min_max": (
            sorted(set(range(indexes[0], indexes[-1] + 1)) - set(indexes)) if indexes else []
        ),
    })
    return result


def inspect_tiff_header(path: Path) -> dict:
    result = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            result.update({
                "shape": [int(v) for v in series.shape],
                "axes": series.axes,
                "dtype": str(series.dtype),
                "page_count": len(tif.pages),
                "is_imagej": bool(tif.is_imagej),
                "is_ome": bool(tif.is_ome),
                "imagej_metadata": tif.imagej_metadata,
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def inspect_abba_package(package_path: str) -> int:
    """Inventory a portable ABBA/QuPath export before attempting reconstruction."""
    package = Path(package_path).expanduser().resolve()
    if not package.is_dir():
        suggestions: list[Path] = []
        # Windows Explorer displays a drive label before ``(G:)``. That label is
        # not a directory component. If the supplied path accidentally includes
        # it, suggest the same final folder directly below the drive root.
        if package.anchor and package.name:
            direct_on_drive = Path(package.anchor) / package.name
            if direct_on_drive.is_dir():
                suggestions.append(direct_on_drive)
        detail = f"ABBA package folder does not exist: {package}"
        if suggestions:
            detail += "\nDid you mean: " + " or ".join(str(path) for path in suggestions)
        detail += (
            "\nTip: in Windows Explorer, text such as 'Dominik_different_projects (G:)' "
            "may be the drive label. In that case the folder is G:\\nissl_registration, "
            "not G:\\Dominik_different_projects\\nissl_registration."
        )
        raise NotADirectoryError(detail)

    files = [path for path in package.rglob("*") if path.is_file()]
    suffix_counts = Counter(path.suffix.lower() or "<no_suffix>" for path in files)
    source_indexes = sorted({
        int(match.group(1))
        for path in files
        for match in [WHS_EXPORT_INDEX_RE.search(path.name)]
        if match
    })
    # ABBA's BDV JSON exporter may create a valid JSON file without a .json
    # suffix. Include reasonably sized files whose names identify them as BDV
    # exports; summarize_bdv_json will report (rather than hide) parse failures.
    json_files = [
        path for path in files
        if path.suffix.lower() == ".json"
        or ("bdv" in path.name.lower() and path.stat().st_size <= 128 * 1024 * 1024)
    ]
    state_files = [path for path in files if path.suffix.lower() == ".abba"]
    project_files = [path for path in files if path.suffix.lower() in {".qpproj", ".backup"}]
    # Opening every original 2-D source TIFF is unnecessarily expensive. Inspect
    # only likely stack/registered TIFFs or files larger than 32 MiB.
    tiff_candidates = [
        path for path in files
        if path.suffix.lower() in {".tif", ".tiff"}
        and ("stack" in path.name.lower() or "registered" in path.name.lower() or path.stat().st_size >= 32 * 1024 * 1024)
    ]
    report = {
        "package": str(package),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "suffix_counts": dict(sorted(suffix_counts.items())),
        "state_files": [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in state_files],
        "qupath_project_files": [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in project_files],
        "bdv_json_files": [summarize_bdv_json(path) for path in json_files],
        "registered_tiff_candidates": [inspect_tiff_header(path) for path in tiff_candidates],
        "source_filename_indexes": source_indexes,
        "source_filename_index_min_max": [source_indexes[0], source_indexes[-1]] if source_indexes else None,
        "source_filename_index_count": len(source_indexes),
        "expected_retained_index_range": [189, 776],
        "expected_retained_slice_count": 588,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    ensure_dirs()
    destination = REPORT_DIR / "abba_package_inventory.json"
    destination.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_json({"abba_package_inspect": {"package": str(package), "inventory": rel(destination)}})
    print(f"ABBA package inventory written: {rel(destination)}")
    print(f"Files: {report['file_count']} | bytes: {report['total_bytes']}")
    print(f"ABBA states: {len(state_files)} | JSON exports: {len(json_files)} | registered TIFF candidates: {len(tiff_candidates)}")
    for item in report["registered_tiff_candidates"]:
        print(f"TIFF: {item['path']} | shape={item.get('shape')} axes={item.get('axes')} dtype={item.get('dtype')}")
    for item in report["bdv_json_files"]:
        print(
            f"JSON: {item['path']} | valid={item.get('valid_json')} "
            f"WHS indexes={item.get('whs_export_index_count')} "
            f"TPS={item.get('thinplate_spline_count')}"
        )
    return 0


def import_imagej_registered_stack(source_path: str, stack_order: str) -> int:
    """Strictly map a registered ImageJ stack onto non-empty Paxinos AP planes.

    This intentionally refuses spatial guessing. The stack must contain exactly
    one plane per non-empty Paxinos annotation plane and each plane must already
    have the target SI/LR shape (or its unambiguous transpose).
    """
    src = Path(source_path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Registered ImageJ TIFF stack is missing: {src}")
    fixed_mask, annotation_path, fixed_orientation = load_fixed_label_mask()
    fixed_ap = np.flatnonzero(np.any(fixed_mask, axis=(1, 2)))
    if fixed_ap.size == 0:
        raise ValueError(f"Paxinos annotation has no labeled AP planes: {annotation_path}")

    try:
        stack = tifffile.memmap(src)
    except (ValueError, OSError):
        stack = tifffile.imread(src)
    stack = np.squeeze(np.asarray(stack))
    if stack.ndim != 3:
        raise ValueError(f"ImageJ stack must be three-dimensional after squeezing; got {stack.shape}")
    matching_axes = [axis for axis, size in enumerate(stack.shape) if size == fixed_ap.size]
    if len(matching_axes) != 1:
        raise ValueError(
            f"Cannot identify stack plane axis: stack shape={stack.shape}, expected exactly one axis "
            f"with {fixed_ap.size} non-empty Paxinos planes. Run abba-package-inspect first."
        )
    stack = np.moveaxis(stack, matching_axes[0], 0)
    if stack_order == "posterior-to-anterior":
        stack = stack[::-1]
    elif stack_order != "anterior-to-posterior":
        raise ValueError(f"Unsupported stack order: {stack_order}")

    if stack.shape[1:] == TARGET_SHAPE[1:]:
        pass
    elif stack.shape[1:] == TARGET_SHAPE[1:][::-1]:
        stack = stack.transpose(0, 2, 1)
    else:
        raise ValueError(
            f"Registered stack planes have SI/LR shape {stack.shape[1:]}; expected {TARGET_SHAPE[1:]} "
            f"or transpose {TARGET_SHAPE[1:][::-1]}. Automatic resize/crop is deliberately disabled."
        )

    if np.issubdtype(stack.dtype, np.floating):
        stack_u16 = normalize_uint16(stack.astype(np.float32, copy=False))
    elif stack.dtype == np.uint8:
        stack_u16 = stack.astype(np.uint16) * 257
    elif np.issubdtype(stack.dtype, np.integer):
        stack_u16 = np.clip(stack, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    else:
        raise ValueError(f"Unsupported ImageJ stack dtype: {stack.dtype}")

    volume = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    volume[fixed_ap] = stack_u16
    ensure_dirs()
    tifffile.imwrite(ACTIVE_PATH, volume, bigtiff=True)
    qc_slices(volume, REPORT_DIR / "qc_imported_imagej", "ch03_imported_imagej")
    report = {
        "source": str(src),
        "source_sha256": sha256_file(src),
        "source_shape": [int(v) for v in stack.shape],
        "source_dtype": str(stack.dtype),
        "stack_order": stack_order,
        "mapped_target_ap_min_max": [int(fixed_ap[0]), int(fixed_ap[-1])],
        "mapped_plane_count": int(fixed_ap.size),
        "target_shape_ap_si_lr": list(TARGET_SHAPE),
        "active": rel(ACTIVE_PATH),
        "annotation": str(annotation_path),
        "fixed_orientation": fixed_orientation,
        "mapping": "one exported stack plane per non-empty Paxinos annotation AP plane; no inferred resize/crop",
    }
    write_json({"ch03_import_imagej_stack": report})
    print(f"Imported registered ImageJ stack as active Ch03: {rel(ACTIVE_PATH)}")
    print(f"Mapped {fixed_ap.size} planes to Paxinos AP {fixed_ap[0]}-{fixed_ap[-1]}; target shape={TARGET_SHAPE}")
    return 0


def import_active_ch03(source_path: str) -> int:
    """Import an externally registered Ch03 TIFF as the active optional Ch03 asset.

    This supports the manual ABBA/BigWarp-style workflow: register/export the
    WHS/Nissl volume outside this script, then copy the final AP/SI/LR TIFF into
    resources/optional_ch03/waxholm_anatomy_reference.tiff after strict shape
    validation. The stable Paxinos annotation and structures are not modified.
    """
    src = Path(source_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"Imported Ch03 TIFF is missing: {src}")
    vol = tifffile.imread(src)
    if tuple(vol.shape) != TARGET_SHAPE:
        raise ValueError(f"Imported Ch03 TIFF has shape {vol.shape}; expected AP/SI/LR {TARGET_SHAPE}: {src}")
    if not np.issubdtype(vol.dtype, np.integer):
        vol = normalize_uint16(vol.astype(np.float32, copy=False))
    ensure_dirs()
    tifffile.imwrite(ACTIVE_PATH, vol.astype(np.uint16, copy=False), bigtiff=True)
    qc_slices(vol.astype(np.uint16, copy=False), REPORT_DIR / "qc_imported_active", "ch03_imported_active")
    write_json({"ch03_import_active": {"source": str(src), "active": rel(ACTIVE_PATH), "shape": list(vol.shape), "dtype": str(vol.dtype), "qc_dir": rel(REPORT_DIR / "qc_imported_active"), "note": "Externally registered Ch03 TIFF imported after strict AP/SI/LR shape validation; Paxinos annotation and structures were not modified."}})
    print(f"Imported externally registered Ch03 TIFF as active asset: {rel(ACTIVE_PATH)}")
    return 0

def uninstall_ch03() -> int:
    """Remove only the optional Ch03 install artifacts from discovered Paxinos atlas folders.

    This is the conservative finalization path when Ch03 QC is not good enough:
    keep the stable Paxinos atlas, remove waxholm_anatomy_reference from atlas
    metadata/additional_references, and leave annotation/structures untouched.
    """
    ensure_dirs()
    target_name = "waxholm_anatomy_reference"
    touched = []
    for index, atlas_dir in enumerate(all_atlas_candidates(), start=1):
        metadata_path = atlas_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        item = {"atlas_dir": str(atlas_dir), "removed_files": [], "metadata_updated": False, "metadata_backup": None}
        for suffix in [".tiff", ".nii.gz"]:
            path = atlas_dir / f"{target_name}{suffix}"
            if path.exists():
                path.unlink()
                item["removed_files"].append(str(path))
                print(f"Removed installed optional Ch03 file: {path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        changed = False
        refs = metadata.get("additional_references", [])
        if isinstance(refs, str):
            refs = [refs]
        if isinstance(refs, list) and target_name in refs:
            refs = [r for r in refs if r != target_name]
            metadata["additional_references"] = refs
            changed = True
        files = metadata.get("files")
        if isinstance(files, dict):
            for key in [f"{target_name}_tiff", f"{target_name}_nifti"]:
                if key in files:
                    del files[key]
                    changed = True
            metadata["files"] = files
        if metadata.get("v53_optional_ch03"):
            metadata["v53_optional_ch03"] = {
                "installed": False,
                "reference_name": target_name,
                "note": "Optional experimental Ch03 removed by ch03-uninstall/finalize-stable because QC was not accepted as final.",
            }
            changed = True
        if changed:
            backup = REPORT_DIR / f"metadata_before_ch03_uninstall_{index}.json"
            shutil.copy2(metadata_path, backup)
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
            item["metadata_updated"] = True
            item["metadata_backup"] = rel(backup)
            print(f"Removed optional Ch03 metadata from: {metadata_path}")
        if item["removed_files"] or item["metadata_updated"]:
            touched.append(item)
    write_json({"ch03_uninstall": {"target_count": len(touched), "targets": touched, "stable_finalization_note": "Paxinos annotation.tiff, annotation.nii.gz, and structures.json were not modified."}})
    if not touched:
        print("No installed optional Ch03 artifacts found. Stable atlas remains unchanged.")
    else:
        print("Optional Ch03 artifacts removed. Restart ABBA/Fiji before checking the stable atlas.")
    return 0


def finalize_stable() -> int:
    """Finalize the realistic stable path by withdrawing Ch03 and reporting the stable atlas."""
    result = uninstall_ch03()
    print("Final decision recorded: Ch03 remains experimental; use the stable 3-channel Paxinos atlas as final.")
    return result


def install_ch03() -> int:
    if not ACTIVE_PATH.exists():
        raise FileNotFoundError(f"Active Ch03 asset is missing: {rel(ACTIVE_PATH)}. Run landmarks-accept affine/warp first.")
    active = tifffile.imread(ACTIVE_PATH)
    if tuple(active.shape) != TARGET_SHAPE:
        raise ValueError(f"Active Ch03 asset has shape {active.shape}; expected {TARGET_SHAPE}: {rel(ACTIVE_PATH)}")
    ensure_dirs()
    targets = []
    for atlas_dir in all_atlas_candidates():
        if (atlas_dir / "annotation.tiff").exists() and (atlas_dir / "metadata.json").exists():
            targets.append(atlas_dir)
    if not targets:
        raise FileNotFoundError("No installable Paxinos atlas directory found with annotation.tiff and metadata.json.")
    installs = [install_one_ch03_target(atlas_dir, index, active) for index, atlas_dir in enumerate(targets, start=1)]
    write_json({"ch03_install": {"installed_targets": installs, "target_count": len(installs)}})
    for item in installs:
        print(f"Installed Ch03 TIFF into atlas: {item['installed_tiff']}")
        print(f"Installed Ch03 NIfTI into atlas: {item['installed_nifti']}")
    print("Updated metadata additional_references with: waxholm_anatomy_reference")
    print("Restart ABBA or reload/reinstall the atlas if the channel is not visible immediately.")
    return 0


def reset() -> int:
    for path in [AFFINE_PATH, WARP_PATH, WHS_LABEL_AFFINE_PATH, WHS_LABEL_WARP_PATH, REPORT_JSON]:
        if path.exists():
            path.unlink(); print(f"Removed {rel(path)}")
    for folder in [REPORT_DIR / "qc_affine", REPORT_DIR / "qc_warp", REPORT_DIR / "qc_template", REPORT_DIR / "qc_label_volume", REPORT_DIR / "qc_label_volume_warp"]:
        if folder.exists():
            shutil.rmtree(folder); print(f"Removed {rel(folder)}")
    print("Reset complete. Landmark CSV and accepted active Ch03 asset were left untouched.")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V53 Ch03 landmark-guided registration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ["landmarks-status", "landmarks-template", "landmarks-affine", "landmarks-warp", "auto-affine", "auto-warp", "auto-micro-warp", "region-template", "region-qc", "region-list", "region-warp", "auto-region-warp", "bregma-template", "bregma-init-affine", "bregma-warp", "labels-volume-affine", "labels-volume-warp", "labels-affine", "labels-warp", "export-whs-slices", "export-whs-paxinos-slices", "ch03-install", "ch03-uninstall", "finalize-stable", "landmarks-reset"]:
        sub.add_parser(name)
    add = sub.add_parser("region-add")
    add.add_argument("name")
    add.add_argument("target_ap", type=float)
    add.add_argument("target_si", type=float)
    add.add_argument("target_lr", type=float)
    add.add_argument("current_ap", type=float)
    add.add_argument("current_si", type=float)
    add.add_argument("current_lr", type=float)
    add.add_argument("radius", type=float, nargs="?", default=45.0)
    add.add_argument("weight", type=float, nargs="?", default=1.0)
    add.add_argument("--notes", default="")
    acc = sub.add_parser("landmarks-accept"); acc.add_argument("kind", choices=["affine", "warp"])
    imp = sub.add_parser("ch03-import-active"); imp.add_argument("source_tiff")
    inspect_package = sub.add_parser("abba-package-inspect")
    inspect_package.add_argument("package_folder")
    imagej_import = sub.add_parser("ch03-import-imagej-stack")
    imagej_import.add_argument("source_tiff")
    imagej_import.add_argument("stack_order", choices=["anterior-to-posterior", "posterior-to-anterior"])
    args = parser.parse_args(argv)
    try:
        commands = {
            "landmarks-status": status,
            "landmarks-template": create_template,
            "landmarks-affine": run_affine,
            "landmarks-warp": run_warp,
            "auto-affine": run_auto_affine,
            "auto-warp": run_auto_warp,
            "auto-micro-warp": run_auto_micro_warp,
            "region-template": create_region_template,
            "region-qc": run_region_qc,
            "region-list": list_region_corrections,
            "region-warp": run_region_warp,
            "auto-region-warp": run_auto_region_warp,
            "bregma-template": create_bregma_template,
            "bregma-init-affine": init_bregma_from_affine,
            "bregma-warp": run_bregma_warp,
            "labels-volume-affine": run_labels_volume_affine,
            "labels-volume-warp": run_labels_volume_warp,
            "labels-affine": run_labels_affine,
            "labels-warp": run_labels_warp,
            "region-add": lambda: append_region_correction(args),
            "export-whs-slices": export_whs_slices,
            "export-whs-paxinos-slices": export_whs_paxinos_slices,
            "abba-package-inspect": lambda: inspect_abba_package(args.package_folder),
            "ch03-import-imagej-stack": lambda: import_imagej_registered_stack(args.source_tiff, args.stack_order),
            "ch03-install": install_ch03,
            "ch03-import-active": lambda: import_active_ch03(args.source_tiff),
            "ch03-uninstall": uninstall_ch03,
            "finalize-stable": finalize_stable,
            "landmarks-reset": reset,
        }
        return commands.get(args.cmd, lambda: accept(args.kind))()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
