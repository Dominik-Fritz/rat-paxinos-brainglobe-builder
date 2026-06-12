from __future__ import annotations

import configparser
import json
import math
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import nibabel as nib
import numpy as np
import tifffile

try:
    from skimage.transform import resize
except Exception as exc:  # pragma: no cover
    resize = None
    SKIMAGE_IMPORT_ERROR = repr(exc)
else:
    SKIMAGE_IMPORT_ERROR = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "bluebrainheadmodels"
OFFICIAL_BASE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
OUT_DIR = PROJECT_ROOT / "reports" / "v32_7_waxholm_reference_test_atlases"

WAXHOLM_MRI = RAW_DIR / "Waxholm_Atlas_MRI.nii.gz"
WAXHOLM_MASK = RAW_DIR / "Waxholm_Atlas_Mask.nii.gz"

ATLAS_VERSION = "1.0"

TESTS = [
    {
        "rank": 1,
        "atlas_name": "paxinos_watson_rat_40um_waxholm_ref_rank01_test",
        "perm": (2, 1, 0),
        "flips": (True, True, False),
        "dice_v32_6": 0.7023575287089042,
    },
    {
        "rank": 2,
        "atlas_name": "paxinos_watson_rat_40um_waxholm_ref_rank02_test",
        "perm": (2, 1, 0),
        "flips": (True, True, True),
        "dice_v32_6": 0.7020001916504296,
    },
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def robust_uint16(data: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    arr = np.asarray(data, dtype=np.float32)
    finite = np.isfinite(arr)
    valid = finite
    if mask is not None:
        valid = valid & (mask > 0)
    if not np.any(valid):
        return np.zeros(arr.shape, dtype=np.uint16)
    vals = arr[valid]
    lo, hi = np.percentile(vals, [0.5, 99.7])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(vals))
        hi = float(np.nanmax(vals))
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint16)
    scaled = (arr - lo) / (hi - lo)
    scaled[~finite] = 0
    scaled = np.clip(scaled, 0, 1)
    return np.round(scaled * 65535).astype(np.uint16)


def bbox(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    coords = np.argwhere(mask > 0)
    if coords.size == 0:
        raise ValueError("empty mask; cannot compute bounding box")
    return coords.min(axis=0), coords.max(axis=0) + 1


def transform_array(arr: np.ndarray, perm: Iterable[int], flips: Iterable[bool]) -> np.ndarray:
    out = np.transpose(arr, tuple(perm))
    for axis, do_flip in enumerate(tuple(flips)):
        if do_flip:
            out = np.flip(out, axis=axis)
    return out


def ensure_same_shape(a: np.ndarray, b: np.ndarray, a_name: str, b_name: str) -> None:
    if tuple(a.shape) != tuple(b.shape):
        raise ValueError(f"shape mismatch: {a_name}={a.shape}, {b_name}={b.shape}")


def copy_base_candidate(test_atlas_name: str) -> Path:
    if not OFFICIAL_BASE.exists():
        raise FileNotFoundError(f"official base candidate not found: {OFFICIAL_BASE}")
    target = OFFICIAL_BASE.parent / test_atlas_name
    if target.exists():
        backup = target.with_name(target.name + f"_backup_v32_7_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.move(str(target), str(backup))
    shutil.copytree(OFFICIAL_BASE, target)
    return target


def patch_metadata(candidate_dir: Path, atlas_name: str, test: Dict[str, Any], ref_stats: Dict[str, Any]) -> Dict[str, Any]:
    meta_path = candidate_dir / "metadata.json"
    meta = load_json(meta_path)
    meta["name"] = atlas_name
    meta["atlas_name"] = atlas_name
    meta["title"] = f"Paxinos-Watson Rat Brain Atlas with Waxholm MRI reference test rank {test['rank']}"
    meta["status"] = "v32_7_waxholm_reference_test_not_final"
    meta["reference_strategy"] = "v32_7_waxholm_mri_bbox_fit_from_v32_6_mask_alignment_test"
    meta["warning"] = (
        "Reference background is a bbox-fit Waxholm MRI test. It is not a final nonlinear registration "
        "and must not be used for quantitative anatomical mapping without visual/manual QC."
    )
    meta["additional_references"] = []
    files = meta.setdefault("files", {})
    files.pop("hemispheres", None)
    files["reference"] = "reference.nii.gz"
    files["reference_tiff"] = "reference.tiff"
    files["reference_nifti"] = "reference.nii.gz"
    meta["v32_7_waxholm_reference_test"] = {
        "applied": True,
        "generated_at": now(),
        "rank": test["rank"],
        "source_mri": str(WAXHOLM_MRI),
        "source_mask": str(WAXHOLM_MASK),
        "perm": list(test["perm"]),
        "flips": list(test["flips"]),
        "dice_v32_6_lowres": test["dice_v32_6"],
        "method": "transform Waxholm MRI/mask by rank permutation/flips, crop to transformed Waxholm mask bbox, resize to Paxinos annotation bbox, place into V32.2 Paxinos space",
        "reference_stats": ref_stats,
    }
    write_json(meta_path, meta)
    return meta


def patch_candidate_manifest(candidate_dir: Path, atlas_name: str, test: Dict[str, Any]) -> None:
    manifest_path = candidate_dir / "candidate_manifest.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
    else:
        manifest = {}
    manifest["atlas_name"] = atlas_name
    manifest["status"] = "v32_7_waxholm_reference_test_not_final"
    manifest["v32_7_note"] = "Separate test candidate; stable atlas is not modified."
    manifest["v32_7_rank"] = test["rank"]
    manifest["v32_7_perm"] = list(test["perm"])
    manifest["v32_7_flips"] = list(test["flips"])
    write_json(manifest_path, manifest)


def install_to_brainglobe(candidate_dir: Path, atlas_name: str) -> Dict[str, Any]:
    bg_dir = Path.home() / ".brainglobe"
    bg_dir.mkdir(parents=True, exist_ok=True)
    target = bg_dir / f"{atlas_name}_v{ATLAS_VERSION}"
    backup = None
    if target.exists():
        backup = bg_dir / f"_{atlas_name}_backup_v32_7_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.move(str(target), str(backup))
    shutil.copytree(candidate_dir, target)

    conf_path = bg_dir / "last_versions.conf"
    conf_backup = None
    parser = configparser.ConfigParser()
    if conf_path.exists():
        conf_backup = conf_path.with_name(conf_path.name + f".backup_v32_7_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(conf_path, conf_backup)
        parser.read(conf_path)
    if not parser.has_section("atlases"):
        parser.add_section("atlases")
    parser.set("atlases", atlas_name, ATLAS_VERSION)
    with conf_path.open("w", encoding="utf-8") as f:
        parser.write(f)

    native_manifest = {
        "generated_at": now(),
        "atlas_name": atlas_name,
        "version": ATLAS_VERSION,
        "versioned_full_name": f"{atlas_name}_v{ATLAS_VERSION}",
        "target": str(target),
        "source_candidate": str(candidate_dir),
        "note": "Installed by V32.7 Waxholm reference test atlas builder.",
    }
    write_json(target / "native_install_manifest.json", native_manifest)
    return {
        "cache_dir": str(target),
        "existing_cache_backup": str(backup) if backup else None,
        "last_versions_conf": str(conf_path),
        "last_versions_backup": str(conf_backup) if conf_backup else None,
    }


def stats(arr: np.ndarray) -> Dict[str, Any]:
    arr_np = np.asarray(arr)
    finite = np.isfinite(arr_np) if np.issubdtype(arr_np.dtype, np.floating) else np.ones(arr_np.shape, dtype=bool)
    return {
        "shape": list(arr_np.shape),
        "dtype": str(arr_np.dtype),
        "min": float(np.nanmin(arr_np)) if arr_np.size else None,
        "max": float(np.nanmax(arr_np)) if arr_np.size else None,
        "mean": float(np.nanmean(arr_np)) if arr_np.size else None,
        "nonzero_fraction": float(np.count_nonzero(arr_np) / arr_np.size) if arr_np.size else None,
        "finite_fraction": float(np.count_nonzero(finite) / arr_np.size) if arr_np.size else None,
    }


def save_preview(path: Path, reference: np.ndarray, mask: np.ndarray, title: str) -> Dict[str, Any]:
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        return {"attempted": True, "written": None, "error": f"matplotlib import failed: {exc!r}"}

    fig, axes = plt.subplots(3, 2, figsize=(12, 16))
    for axis in range(3):
        idx = reference.shape[axis] // 2
        ref_slice = np.take(reference, idx, axis=axis)
        mask_slice = np.take(mask, idx, axis=axis)
        axes[axis, 0].imshow(ref_slice.T, cmap="gray", origin="lower")
        axes[axis, 0].set_title(f"Waxholm reference axis{axis}_mid")
        axes[axis, 0].axis("off")
        axes[axis, 1].imshow(ref_slice.T, cmap="gray", origin="lower")
        axes[axis, 1].imshow(mask_slice.T, alpha=0.35, origin="lower")
        axes[axis, 1].set_title(f"Reference + Paxinos mask axis{axis}_mid")
        axes[axis, 1].axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return {"attempted": True, "written": str(path), "error": None}


def build_one(test: Dict[str, Any], wax_mri: np.ndarray, wax_mask: np.ndarray, fixed_img: nib.Nifti1Image, fixed_mask: np.ndarray) -> Dict[str, Any]:
    atlas_name = test["atlas_name"]
    perm = test["perm"]
    flips = test["flips"]

    candidate_dir = copy_base_candidate(atlas_name)

    # Important: transform mask and MRI identically. transform_array returns views where possible.
    moving_mask_t = transform_array(wax_mask > 0, perm, flips)
    moving_mri_t = transform_array(wax_mri, perm, flips)

    moving_min, moving_max = bbox(moving_mask_t)
    fixed_min, fixed_max = bbox(fixed_mask)
    moving_slices = tuple(slice(int(a), int(b)) for a, b in zip(moving_min, moving_max))
    fixed_slices = tuple(slice(int(a), int(b)) for a, b in zip(fixed_min, fixed_max))

    moving_crop = np.asarray(moving_mri_t[moving_slices], dtype=np.float32)
    moving_mask_crop = np.asarray(moving_mask_t[moving_slices], dtype=bool)
    fixed_bbox_shape = tuple(int(b - a) for a, b in zip(fixed_min, fixed_max))

    if resize is None:
        raise RuntimeError(f"scikit-image resize unavailable: {SKIMAGE_IMPORT_ERROR}")

    # Normalize before resize to keep memory reasonable and contrast stable.
    moving_crop_u16 = robust_uint16(moving_crop, moving_mask_crop)
    del moving_crop

    resized = resize(
        moving_crop_u16,
        fixed_bbox_shape,
        order=1,
        preserve_range=True,
        anti_aliasing=True,
    ).astype(np.uint16)
    del moving_crop_u16

    reference = np.zeros(fixed_mask.shape, dtype=np.uint16)
    reference[fixed_slices] = resized
    del resized

    # Keep outside the target mask dimmed but not forcibly zeroed. A visible surrounding MRI slab can help QC.
    # For ABBA background usability, however, fully black outside the fixed bbox is intentional.
    ref_stats = stats(reference)

    ref_nifti = nib.Nifti1Image(reference, affine=fixed_img.affine, header=fixed_img.header.copy())
    ref_nifti.set_data_dtype(np.uint16)
    nib.save(ref_nifti, str(candidate_dir / "reference.nii.gz"))
    tifffile.imwrite(candidate_dir / "reference.tiff", reference, compression="zlib")

    meta = patch_metadata(candidate_dir, atlas_name, test, ref_stats)
    patch_candidate_manifest(candidate_dir, atlas_name, test)

    preview_path = OUT_DIR / f"v32_7_{atlas_name}_preview.png"
    preview = save_preview(preview_path, reference, fixed_mask, f"V32.7 {atlas_name}")

    install = install_to_brainglobe(candidate_dir, atlas_name)

    return {
        "rank": test["rank"],
        "atlas_name": atlas_name,
        "candidate_dir": str(candidate_dir),
        "cache": install,
        "perm": list(perm),
        "flips": list(flips),
        "dice_v32_6_lowres": test["dice_v32_6"],
        "moving_bbox_after_transform_min": moving_min.astype(int).tolist(),
        "moving_bbox_after_transform_max": moving_max.astype(int).tolist(),
        "fixed_bbox_min": fixed_min.astype(int).tolist(),
        "fixed_bbox_max": fixed_max.astype(int).tolist(),
        "reference_stats": ref_stats,
        "metadata_name": meta.get("name"),
        "metadata_orientation": meta.get("orientation"),
        "metadata_shape": meta.get("shape"),
        "preview": preview,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {
        "generated_at": now(),
        "project_root": str(PROJECT_ROOT),
        "out_dir": str(OUT_DIR),
        "official_base": str(OFFICIAL_BASE),
        "waxholm_mri": str(WAXHOLM_MRI),
        "waxholm_mask": str(WAXHOLM_MASK),
        "passed": False,
        "tests": [],
        "errors": [],
    }

    try:
        for p in [OFFICIAL_BASE, WAXHOLM_MRI, WAXHOLM_MASK, OFFICIAL_BASE / "annotation.nii.gz"]:
            if not p.exists():
                raise FileNotFoundError(str(p))

        fixed_img = nib.load(str(OFFICIAL_BASE / "annotation.nii.gz"))
        fixed_ann = np.asanyarray(fixed_img.dataobj)
        fixed_mask = fixed_ann > 0
        del fixed_ann
        report["target"] = {
            "shape": list(fixed_img.shape),
            "orientation": "".join(nib.aff2axcodes(fixed_img.affine)),
            "voxel_size": [float(x) for x in fixed_img.header.get_zooms()[:3]],
            "mask_nonzero_fraction": float(np.count_nonzero(fixed_mask) / fixed_mask.size),
        }

        wax_mri_img = nib.load(str(WAXHOLM_MRI))
        wax_mask_img = nib.load(str(WAXHOLM_MASK))
        wax_mri = np.asanyarray(wax_mri_img.dataobj).astype(np.float32, copy=False)
        wax_mask = np.asanyarray(wax_mask_img.dataobj)
        ensure_same_shape(wax_mri, wax_mask, "Waxholm MRI", "Waxholm mask")
        report["waxholm"] = {
            "mri_shape": list(wax_mri.shape),
            "mask_shape": list(wax_mask.shape),
            "orientation": "".join(nib.aff2axcodes(wax_mri_img.affine)),
            "voxel_size": [float(x) for x in wax_mri_img.header.get_zooms()[:3]],
        }

        for test in TESTS:
            try:
                report["tests"].append(build_one(test, wax_mri, wax_mask, fixed_img, fixed_mask))
            except Exception as exc:
                report["errors"].append({
                    "rank": test["rank"],
                    "atlas_name": test["atlas_name"],
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                })

        report["passed"] = bool(report["tests"]) and not report["errors"]

    except Exception as exc:
        report["errors"].append({"error": repr(exc), "traceback": traceback.format_exc()})
        report["passed"] = False

    write_json(OUT_DIR / "v32_7_waxholm_reference_test_atlases_report.json", report)

    lines = []
    lines.append("V32.7 Waxholm reference test atlases")
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"PASSED: {report['passed']}")
    lines.append(f"Project root: {PROJECT_ROOT}")
    lines.append(f"Output dir: {OUT_DIR}")
    lines.append("")
    if "target" in report:
        lines.append("Target Paxinos:")
        lines.append(f"- shape: {report['target']['shape']}")
        lines.append(f"- orientation: {report['target']['orientation']}")
        lines.append(f"- mask_nonzero_fraction: {report['target']['mask_nonzero_fraction']:.6f}")
        lines.append("")
    if "waxholm" in report:
        lines.append("Waxholm source:")
        lines.append(f"- MRI: {WAXHOLM_MRI}")
        lines.append(f"- Mask: {WAXHOLM_MASK}")
        lines.append(f"- shape: {report['waxholm']['mri_shape']}")
        lines.append(f"- orientation: {report['waxholm']['orientation']}")
        lines.append("")
    lines.append("Built test atlases:")
    for item in report.get("tests", []):
        lines.append(f"- {item['atlas_name']}")
        lines.append(f"  rank={item['rank']} dice_v32_6_lowres={item['dice_v32_6_lowres']:.6f}")
        lines.append(f"  perm={item['perm']} flips={item['flips']}")
        lines.append(f"  candidate={item['candidate_dir']}")
        lines.append(f"  cache={item['cache']['cache_dir']}")
        lines.append(f"  reference_stats={item['reference_stats']}")
        if item.get("preview", {}).get("written"):
            lines.append(f"  preview={item['preview']['written']}")
    if report.get("errors"):
        lines.append("")
        lines.append("Errors:")
        for err in report["errors"]:
            lines.append(f"- {err.get('atlas_name','general')}: {err.get('error')}")
    lines.append("")
    lines.append("ABBA test:")
    lines.append("- Restart Fiji/ABBA completely.")
    lines.append("- Open paxinos_watson_rat_40um_waxholm_ref_rank01_test.")
    lines.append("- Open paxinos_watson_rat_40um_waxholm_ref_rank02_test.")
    lines.append("- Compare reference background under borders; do not promote before visual QC.")
    (OUT_DIR / "v32_7_waxholm_reference_test_atlases_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
