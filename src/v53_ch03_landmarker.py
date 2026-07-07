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



def find_annotation_path(required: bool = True) -> Path | None:
    for atlas_dir in ATLAS_CANDIDATES:
        path = atlas_dir / "annotation.tiff"
        if path.exists():
            return path
    if required:
        searched = "\n  - ".join(str(p / "annotation.tiff") for p in ATLAS_CANDIDATES)
        raise FileNotFoundError(
            "Could not find Paxinos annotation.tiff for automated label-guided registration. "
            "Run the normal atlas builder locally first, or install the atlas in the BrainGlobe cache. "
            f"Searched:\n  - {searched}"
        )
    return None


def load_fixed_label_mask() -> tuple[np.ndarray, Path]:
    path = find_annotation_path(required=True)
    labels = tifffile.imread(path)
    if labels.ndim != 3:
        raise ValueError(f"Expected 3D annotation.tiff, got shape {labels.shape} at {path}")
    if labels.shape != TARGET_SHAPE:
        raise ValueError(f"Expected annotation.tiff shape {TARGET_SHAPE}, got {labels.shape} at {path}")
    mask = labels > 0
    if int(mask.sum()) == 0:
        raise ValueError(f"annotation.tiff contains no non-zero labels: {path}")
    return mask, path


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
    scale = np.clip(f_size / m_size, 0.75, 1.35)
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


def run_auto_affine() -> int:
    vol = load_oriented_source()
    fixed_mask, annotation_path = load_fixed_label_mask()
    mat, metrics = automatic_affine_matrix(vol, fixed_mask)
    out = apply_affine(vol, mat)
    write_tiff(AFFINE_PATH, out)
    qc_slices(out, REPORT_DIR / "qc_affine", "ch03_auto_affine")
    write_json({"auto_affine": {"candidate": rel(AFFINE_PATH), "annotation_tiff": str(annotation_path), "matrix_moving_to_fixed": mat.tolist(), **metrics}})
    return 0


def run_auto_warp() -> int:
    fixed_mask, annotation_path = load_fixed_label_mask()
    base = tifffile.imread(AFFINE_PATH) if AFFINE_PATH.exists() else None
    if base is None:
        run_auto_affine()
        base = tifffile.imread(AFFINE_PATH)
    moving_mask = moving_brain_mask(base)
    fixed_small = downsample_mask(fixed_mask, factor=16)
    moving_small = downsample_mask(moving_mask, factor=16)
    fixed_com = np.asarray([center_of_mass(fixed_small[i]) if fixed_small[i].any() else (np.nan, np.nan) for i in range(fixed_small.shape[0])])
    moving_com = np.asarray([center_of_mass(moving_small[i]) if moving_small[i].any() else (np.nan, np.nan) for i in range(moving_small.shape[0])])
    valid = np.isfinite(fixed_com[:, 0]) & np.isfinite(moving_com[:, 0])
    if valid.sum() < 8:
        raise ValueError(f"Automated warp needs at least 8 overlapping AP slices with label and Nissl masks; found {int(valid.sum())}.")
    delta_small = np.zeros((fixed_small.shape[0], 2), dtype=np.float32)
    delta_small[valid] = fixed_com[valid] - moving_com[valid]
    for axis in range(2):
        delta_small[:, axis] = np.interp(np.arange(len(delta_small)), np.flatnonzero(valid), delta_small[valid, axis])
        delta_small[:, axis] = ndimage.gaussian_filter1d(delta_small[:, axis], sigma=2.0)
    ap_scale = TARGET_SHAPE[0] / fixed_small.shape[0]
    si_scale = TARGET_SHAPE[1] / fixed_small.shape[1]
    lr_scale = TARGET_SHAPE[2] / fixed_small.shape[2]
    delta = np.column_stack([np.zeros(TARGET_SHAPE[0]), np.interp(np.arange(TARGET_SHAPE[0]) / ap_scale, np.arange(fixed_small.shape[0]), delta_small[:, 0]) * si_scale, np.interp(np.arange(TARGET_SHAPE[0]) / ap_scale, np.arange(fixed_small.shape[0]), delta_small[:, 1]) * lr_scale])
    coords = np.meshgrid(*[np.arange(s) for s in TARGET_SHAPE], indexing="ij")
    warped = ndimage.map_coordinates(base, [coords[0], coords[1] - delta[:, 1, None, None], coords[2] - delta[:, 2, None, None]], order=1, mode="constant", cval=0).astype(np.uint16)
    write_tiff(WARP_PATH, warped)
    qc_slices(warped, REPORT_DIR / "qc_warp", "ch03_auto_warp")
    write_json({"auto_warp": {"candidate": rel(WARP_PATH), "annotation_tiff": str(annotation_path), "method": "affine_plus_slice_centerline_mask_warp", "valid_ap_slices": int(valid.sum())}})
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
    disp = np.stack([ndimage.zoom(disp_small[..., i], np.array(TARGET_SHAPE) / np.array(grid_shape), order=1) for i in range(3)])
    coords = np.meshgrid(*[np.arange(s) for s in TARGET_SHAPE], indexing="ij")
    warped = ndimage.map_coordinates(base, [coords[i] - disp[i] for i in range(3)], order=1, mode="constant", cval=0).astype(np.uint16)
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
    for name in ["landmarks-status", "landmarks-template", "landmarks-affine", "landmarks-warp", "auto-affine", "auto-warp", "landmarks-reset"]:
        sub.add_parser(name)
    acc = sub.add_parser("landmarks-accept"); acc.add_argument("kind", choices=["affine", "warp"])
    args = parser.parse_args(argv)
    try:
        return {"landmarks-status": status, "landmarks-template": create_template, "landmarks-affine": run_affine, "landmarks-warp": run_warp, "auto-affine": run_auto_affine, "auto-warp": run_auto_warp, "landmarks-reset": reset}.get(args.cmd, lambda: accept(args.kind))()
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
