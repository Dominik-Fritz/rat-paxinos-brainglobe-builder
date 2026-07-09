"""V53 local landmark-guided registration for optional Ch03 WHS/Nissl data.

This module intentionally leaves the stable Paxinos/ABBA builder and annotation assets
untouched. Runtime products are confined to resources/optional_ch03 and
reports/v53_ch03_landmarks.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL_DIR = ROOT / "resources" / "optional_ch03"
REPORT_DIR = ROOT / "reports" / "v53_ch03_landmarks"
CSV_PATH = OPTIONAL_DIR / "waxholm_to_paxinos_landmarks.csv"
SOURCE_PATH = Path(os.environ.get("V53_WHS_NISSL_SOURCE", r"C:\Users\49152\.brainglobe\whs_sd_rat_39um_v1.2\reference.tiff"))
ACTIVE_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference.tiff"
AFFINE_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference_landmarks_affine.tiff"
WARP_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference_landmarks_warp.tiff"
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
        "landmark_csv": CSV_PATH,
        "paxinos_annotation": find_annotation_path(required=False) or Path("<not found>"),
        "affine_candidate": AFFINE_PATH,
        "warp_candidate": WARP_PATH,
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


def load_oriented_source() -> np.ndarray:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing WHS/Nissl source: {SOURCE_PATH}. This command requires local WHS data and will not download it.")
    vol = tifffile.imread(SOURCE_PATH)
    if vol.ndim != 3:
        raise ValueError(f"Expected a 3D TIFF source, got shape {vol.shape}")
    vol = np.transpose(vol, PERM)
    vol = np.rot90(vol, k=PRE_RESIZE_ROT90, axes=(1, 2))
    for axis in TARGET_FLIPS:
        vol = np.flip(vol, axis=axis)
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
    fixed_small = downsample_mask(fixed_mask, factor=16)
    moving_small = downsample_mask(moving_mask, factor=16)
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
    write_tiff(WARP_PATH, warped)
    qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_auto_warp")
    qc_overlay_slices(warped, fixed_mask, REPORT_DIR / "qc_warp", "ch03_auto_warp")
    write_json({"auto_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "fixed_orientation": fixed_orientation, "method": "affine_plus_ap_dtw_profile_centerline_and_slice_size_warp", "fixed_valid_ap_slices": int(fixed_valid.sum()), "moving_valid_ap_slices": int(moving_valid.sum()), "slice_scale_si_lr_min": [float(slice_scale_full[:, 0].min()), float(slice_scale_full[:, 1].min())], "slice_scale_si_lr_max": [float(slice_scale_full[:, 0].max()), float(slice_scale_full[:, 1].max())], "ap_source_min_max": [float(source_ap.min()), float(source_ap.max())], "ap_source_samples": [float(source_ap[i]) for i in np.linspace(0, TARGET_SHAPE[0] - 1, 9, dtype=int)]}})
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


def write_tiff(path: Path, vol: np.ndarray) -> None:
    ensure_dirs(); tifffile.imwrite(path, vol, bigtiff=True)
    print(f"Wrote {rel(path)} shape={vol.shape} dtype={vol.dtype}")


def qc_slices(vol: np.ndarray, outdir: Path, prefix: str, lm: LandmarkSet | None = None) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for ap in np.linspace(60, TARGET_SHAPE[0] - 61, 6, dtype=int):
        img = exposure.rescale_intensity(vol[ap], out_range=(0, 1))
        fig, ax = plt.subplots(figsize=(6, 5)); ax.imshow(img, cmap="gray"); ax.set_title(f"{prefix} AP {ap}")
        if lm is not None:
            near = np.abs(lm.fixed[:, 0] - ap) <= 3
            ax.scatter(lm.fixed[near, 2], lm.fixed[near, 1], s=25, c="red")
        ax.set_axis_off(); fig.tight_layout(); fig.savefig(outdir / f"{prefix}_ap_{ap:03d}.png", dpi=120); plt.close(fig)


def qc_overlay_slices(vol: np.ndarray, fixed_mask: np.ndarray, outdir: Path, prefix: str) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
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
    for path in [AFFINE_PATH, WARP_PATH, REPORT_JSON]:
        if path.exists():
            path.unlink(); print(f"Removed {rel(path)}")
    for folder in [REPORT_DIR / "qc_affine", REPORT_DIR / "qc_warp", REPORT_DIR / "qc_template"]:
        if folder.exists():
            shutil.rmtree(folder); print(f"Removed {rel(folder)}")
    print("Reset complete. Landmark CSV and accepted active Ch03 asset were left untouched.")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V53 Ch03 landmark-guided registration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ["landmarks-status", "landmarks-template", "landmarks-affine", "landmarks-warp", "auto-affine", "auto-warp", "ch03-install", "landmarks-reset"]:
        sub.add_parser(name)
    acc = sub.add_parser("landmarks-accept"); acc.add_argument("kind", choices=["affine", "warp"])
    args = parser.parse_args(argv)
    try:
        return {"landmarks-status": status, "landmarks-template": create_template, "landmarks-affine": run_affine, "landmarks-warp": run_warp, "auto-affine": run_auto_affine, "auto-warp": run_auto_warp, "ch03-install": install_ch03, "landmarks-reset": reset}.get(args.cmd, lambda: accept(args.kind))()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
