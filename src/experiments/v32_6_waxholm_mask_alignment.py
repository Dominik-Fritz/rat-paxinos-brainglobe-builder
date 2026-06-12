from __future__ import annotations

import csv
import itertools
import json
import math
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import nibabel as nib
except Exception as e:
    raise RuntimeError("Missing nibabel. Install project requirements first.") from e

try:
    from skimage.transform import resize
except Exception as e:
    raise RuntimeError("Missing scikit-image. Install project requirements first.") from e

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception as e:
    raise RuntimeError("Missing matplotlib. Install matplotlib or run from project venv.") from e


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW = PROJECT_ROOT / "data" / "raw" / "bluebrainheadmodels"
OFFICIAL = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
OUT = PROJECT_ROOT / "reports" / "v32_6_waxholm_mask_alignment"

TARGET_ANN = OFFICIAL / "annotation.nii.gz"
WAX_MRI = RAW / "Waxholm_Atlas_MRI.nii.gz"
WAX_MASK_CANDIDATES = [
    RAW / "Waxholm_Atlas_Mask.nii.gz",
    RAW / "Waxholm_Atlas_Labels.nii.gz",
    RAW / "Waxholm_Atlas.nii.gz",
]

LOW_FACTOR_TARGET = 4
LOW_FACTOR_MOVING = 4
TOP_N = 8


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def robust_norm(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.float32)
    # Ignore zeros for contrast if possible.
    nz = finite[finite > 0]
    sample = nz if nz.size > 100 else finite
    lo, hi = np.percentile(sample, [1.0, 99.7])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(finite)), float(np.nanmax(finite))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.float32)
    return np.clip((arr - lo) / (hi - lo), 0, 1).astype(np.float32)


def load_nifti_info(path: Path) -> dict:
    img = nib.load(str(path))
    return {
        "path": str(path),
        "shape": list(map(int, img.shape[:3])),
        "dtype": str(np.asanyarray(img.dataobj).dtype),
        "orientation": "".join(nib.aff2axcodes(img.affine)),
        "voxel_size": [float(x) for x in img.header.get_zooms()[:3]],
        "affine": [[float(x) for x in row] for row in img.affine.tolist()],
    }


def pick_waxholm_mask() -> Path:
    for p in WAX_MASK_CANDIDATES:
        if p.exists() and p.stat().st_size > 0:
            return p
    raise FileNotFoundError(
        "No Waxholm mask/label image found. Expected one of:\n"
        + "\n".join(str(p) for p in WAX_MASK_CANDIDATES)
    )


def load_downsampled_mask(path: Path, factor: int) -> np.ndarray:
    img = nib.load(str(path))
    sl = tuple(slice(None, None, factor) for _ in range(3))
    arr = np.asanyarray(img.dataobj[sl])
    arr = np.nan_to_num(arr, nan=0.0)
    return (arr > 0).astype(np.uint8)


def load_downsampled_image(path: Path, factor: int) -> np.ndarray:
    img = nib.load(str(path))
    sl = tuple(slice(None, None, factor) for _ in range(3))
    arr = np.asanyarray(img.dataobj[sl]).astype(np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return robust_norm(arr)


def bbox(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pts = np.argwhere(mask > 0)
    if pts.size == 0:
        raise ValueError("Empty mask")
    return pts.min(axis=0), pts.max(axis=0) + 1


def crop_to_bbox(arr: np.ndarray, bmin: np.ndarray, bmax: np.ndarray) -> np.ndarray:
    return arr[bmin[0]:bmax[0], bmin[1]:bmax[1], bmin[2]:bmax[2]]


def transform_arr(arr: np.ndarray, perm: tuple[int, int, int], flips: tuple[bool, bool, bool]) -> np.ndarray:
    out = np.transpose(arr, perm)
    for ax, flag in enumerate(flips):
        if flag:
            out = np.flip(out, axis=ax)
    return out


def resize_bool_to_shape(arr: np.ndarray, shape: tuple[int, int, int]) -> np.ndarray:
    if any(s <= 0 for s in shape):
        raise ValueError(f"Invalid target shape: {shape}")
    out = resize(
        arr.astype(np.float32),
        shape,
        order=0,
        mode="constant",
        cval=0,
        anti_aliasing=False,
        preserve_range=True,
    )
    return (out >= 0.5).astype(np.uint8)


def resize_float_to_shape(arr: np.ndarray, shape: tuple[int, int, int]) -> np.ndarray:
    out = resize(
        arr.astype(np.float32),
        shape,
        order=1,
        mode="constant",
        cval=0,
        anti_aliasing=True,
        preserve_range=True,
    )
    return np.clip(out, 0, 1).astype(np.float32)


def place_in_fixed(crop: np.ndarray, fixed_shape: tuple[int, int, int], fmin: np.ndarray, fmax: np.ndarray) -> np.ndarray:
    out = np.zeros(fixed_shape, dtype=crop.dtype)
    out[fmin[0]:fmax[0], fmin[1]:fmax[1], fmin[2]:fmax[2]] = crop
    return out


def score_candidate(fixed_mask: np.ndarray, moving_mask: np.ndarray, perm, flips, fmin, fmax) -> dict:
    mt = transform_arr(moving_mask, perm, flips)
    mmin, mmax = bbox(mt)
    mcrop = crop_to_bbox(mt, mmin, mmax)
    target_shape = tuple((fmax - fmin).astype(int).tolist())
    mcrop_resized = resize_bool_to_shape(mcrop, target_shape)
    placed = place_in_fixed(mcrop_resized, fixed_mask.shape, fmin, fmax)

    inter = int(np.logical_and(placed > 0, fixed_mask > 0).sum())
    fixed_sum = int((fixed_mask > 0).sum())
    moving_sum = int((placed > 0).sum())
    union = int(np.logical_or(placed > 0, fixed_mask > 0).sum())
    dice = 2 * inter / max(1, fixed_sum + moving_sum)
    jaccard = inter / max(1, union)
    coverage_fixed = inter / max(1, fixed_sum)
    extra_fraction = max(0, moving_sum - inter) / max(1, moving_sum)

    return {
        "perm": list(map(int, perm)),
        "flips": [bool(x) for x in flips],
        "dice": float(dice),
        "jaccard": float(jaccard),
        "coverage_fixed": float(coverage_fixed),
        "extra_fraction": float(extra_fraction),
        "intersection_voxels": inter,
        "fixed_voxels": fixed_sum,
        "moving_voxels_after_fit": moving_sum,
        "moving_bbox_after_transform_min": [int(x) for x in mmin.tolist()],
        "moving_bbox_after_transform_max": [int(x) for x in mmax.tolist()],
        "fixed_bbox_min": [int(x) for x in fmin.tolist()],
        "fixed_bbox_max": [int(x) for x in fmax.tolist()],
    }


def make_preview(
    fixed_mask: np.ndarray,
    moving_img: np.ndarray,
    moving_mask: np.ndarray,
    cand: dict,
    out_path: Path,
    title: str,
) -> None:
    perm = tuple(cand["perm"])
    flips = tuple(cand["flips"])
    fmin = np.array(cand["fixed_bbox_min"], dtype=int)
    fmax = np.array(cand["fixed_bbox_max"], dtype=int)
    mmin = np.array(cand["moving_bbox_after_transform_min"], dtype=int)
    mmax = np.array(cand["moving_bbox_after_transform_max"], dtype=int)

    img_t = transform_arr(moving_img, perm, flips)
    mask_t = transform_arr(moving_mask, perm, flips)
    img_crop = crop_to_bbox(img_t, mmin, mmax)
    mask_crop = crop_to_bbox(mask_t, mmin, mmax)

    target_shape = tuple((fmax - fmin).astype(int).tolist())
    img_resized = resize_float_to_shape(img_crop, target_shape)
    mask_resized = resize_bool_to_shape(mask_crop, target_shape)

    placed_img = place_in_fixed(img_resized, fixed_mask.shape, fmin, fmax)
    placed_mask = place_in_fixed(mask_resized, fixed_mask.shape, fmin, fmax)

    labels = ["axis0 mid", "axis1 mid", "axis2 mid"]
    mids = [fixed_mask.shape[0] // 2, fixed_mask.shape[1] // 2, fixed_mask.shape[2] // 2]

    fig, axes = plt.subplots(3, 3, figsize=(13, 13))
    for ax_i in range(3):
        if ax_i == 0:
            ref = placed_img[mids[0], :, :]
            movm = placed_mask[mids[0], :, :]
            fixm = fixed_mask[mids[0], :, :]
        elif ax_i == 1:
            ref = placed_img[:, mids[1], :]
            movm = placed_mask[:, mids[1], :]
            fixm = fixed_mask[:, mids[1], :]
        else:
            ref = placed_img[:, :, mids[2]]
            movm = placed_mask[:, :, mids[2]]
            fixm = fixed_mask[:, :, mids[2]]

        # rotate for human preview only; data are not modified by this visualization
        ref = np.rot90(ref)
        movm = np.rot90(movm)
        fixm = np.rot90(fixm)

        axes[ax_i, 0].imshow(ref, cmap="gray")
        axes[ax_i, 0].set_title(f"Waxholm MRI {labels[ax_i]}")
        axes[ax_i, 0].axis("off")

        axes[ax_i, 1].imshow(ref, cmap="gray")
        axes[ax_i, 1].imshow(fixm, alpha=0.35)
        axes[ax_i, 1].set_title("MRI + Paxinos mask")
        axes[ax_i, 1].axis("off")

        axes[ax_i, 2].imshow(fixm, alpha=0.55)
        axes[ax_i, 2].imshow(movm, alpha=0.45)
        axes[ax_i, 2].set_title("Paxinos mask + Waxholm mask")
        axes[ax_i, 2].axis("off")

    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    if not TARGET_ANN.exists():
        raise FileNotFoundError(f"Missing target annotation: {TARGET_ANN}")
    if not WAX_MRI.exists():
        raise FileNotFoundError(f"Missing Waxholm MRI: {WAX_MRI}")
    wax_mask_path = pick_waxholm_mask()

    fixed_mask = load_downsampled_mask(TARGET_ANN, LOW_FACTOR_TARGET)
    moving_mask = load_downsampled_mask(wax_mask_path, LOW_FACTOR_MOVING)

    fmin, fmax = bbox(fixed_mask)

    rows = []
    for perm in itertools.permutations((0, 1, 2)):
        for flips in itertools.product((False, True), repeat=3):
            try:
                rows.append(score_candidate(fixed_mask, moving_mask, perm, flips, fmin, fmax))
            except Exception as e:
                rows.append({
                    "perm": list(map(int, perm)),
                    "flips": [bool(x) for x in flips],
                    "dice": -1.0,
                    "jaccard": -1.0,
                    "coverage_fixed": 0.0,
                    "extra_fraction": 1.0,
                    "intersection_voxels": 0,
                    "fixed_voxels": int((fixed_mask > 0).sum()),
                    "moving_voxels_after_fit": 0,
                    "moving_bbox_after_transform_min": "",
                    "moving_bbox_after_transform_max": "",
                    "fixed_bbox_min": [int(x) for x in fmin.tolist()],
                    "fixed_bbox_max": [int(x) for x in fmax.tolist()],
                    "error": repr(e),
                })

    rows = sorted(rows, key=lambda r: (r.get("dice", -1), r.get("jaccard", -1)), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    # Reorder keys with rank first
    ordered = []
    for r in rows:
        nr = {"rank": r.pop("rank")}
        nr.update(r)
        ordered.append(nr)
    rows = ordered

    write_csv(OUT / "waxholm_mask_alignment_ranked_candidates.csv", rows)

    moving_img = load_downsampled_image(WAX_MRI, LOW_FACTOR_MOVING)
    preview_paths = []
    for r in rows[:TOP_N]:
        p = OUT / f"waxholm_mask_alignment_rank_{int(r['rank']):02d}_dice_{float(r['dice']):.4f}.png"
        make_preview(
            fixed_mask=fixed_mask,
            moving_img=moving_img,
            moving_mask=moving_mask,
            cand=r,
            out_path=p,
            title=f"Rank {r['rank']} | dice={float(r['dice']):.4f} | perm={r['perm']} | flips={r['flips']}",
        )
        preview_paths.append(str(p))

    report = {
        "generated_at": now(),
        "passed": True,
        "project_root": str(PROJECT_ROOT),
        "out_dir": str(OUT),
        "target_annotation": str(TARGET_ANN),
        "waxholm_mri": str(WAX_MRI),
        "waxholm_mask": str(wax_mask_path),
        "low_factor_target": LOW_FACTOR_TARGET,
        "low_factor_moving": LOW_FACTOR_MOVING,
        "target_info": load_nifti_info(TARGET_ANN),
        "waxholm_mri_info": load_nifti_info(WAX_MRI),
        "waxholm_mask_info": load_nifti_info(wax_mask_path),
        "fixed_lowres_shape": list(map(int, fixed_mask.shape)),
        "moving_lowres_shape": list(map(int, moving_mask.shape)),
        "fixed_lowres_mask_fraction": float(fixed_mask.mean()),
        "moving_lowres_mask_fraction": float(moving_mask.mean()),
        "best_candidate": rows[0],
        "top_candidates": rows[:TOP_N],
        "preview_paths": preview_paths,
        "notes": [
            "This is mask-to-mask orientation/flip/bbox-fit scoring only.",
            "It does not modify the atlas and is not a final anatomical registration.",
            "Use the best-ranked previews to decide whether the initial Waxholm alignment is worth promoting to a real test atlas.",
        ],
    }

    (OUT / "v32_6_waxholm_mask_alignment_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V32.6 Waxholm mask alignment search",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"PASSED: {report['passed']}",
        "",
        f"Target annotation: {TARGET_ANN}",
        f"Waxholm MRI:       {WAX_MRI}",
        f"Waxholm mask:      {wax_mask_path}",
        "",
        f"Fixed lowres shape:  {report['fixed_lowres_shape']} mask_fraction={report['fixed_lowres_mask_fraction']:.6f}",
        f"Moving lowres shape: {report['moving_lowres_shape']} mask_fraction={report['moving_lowres_mask_fraction']:.6f}",
        "",
        "Best candidate:",
        f"- rank: {rows[0]['rank']}",
        f"- dice: {float(rows[0]['dice']):.6f}",
        f"- jaccard: {float(rows[0]['jaccard']):.6f}",
        f"- coverage_fixed: {float(rows[0]['coverage_fixed']):.6f}",
        f"- extra_fraction: {float(rows[0]['extra_fraction']):.6f}",
        f"- perm: {rows[0]['perm']}",
        f"- flips: {rows[0]['flips']}",
        "",
        "Top preview PNGs:",
    ] + [f"- {p}" for p in preview_paths] + [
        "",
        "Main output files:",
        "- waxholm_mask_alignment_ranked_candidates.csv",
        "- v32_6_waxholm_mask_alignment_report.json",
        "- waxholm_mask_alignment_rank_*.png",
        "",
        "Interpretation:",
        "- This is still a diagnostic transform, not final atlas data.",
        "- If the best preview is anatomically plausible, the next step is a separate Waxholm-reference test atlas.",
        "- If it is not plausible, we need proper deformable registration or a closer reference.",
    ]
    (OUT / "v32_6_waxholm_mask_alignment_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        OUT.mkdir(parents=True, exist_ok=True)
        err = traceback.format_exc()
        (OUT / "v32_6_waxholm_mask_alignment_ERROR.txt").write_text(err, encoding="utf-8")
        print(err)
        raise SystemExit(1)
