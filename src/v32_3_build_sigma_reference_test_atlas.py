"""
V32.3: Build a separate SIGMA-reference test atlas for ABBA/BrainGlobe.

Purpose
-------
This script keeps the validated V32.2 Paxinos orientation/annotation/ontology and
only replaces the synthetic edge reference with a resampled SIGMA anatomical image.
It writes and installs a separate test atlas:

    paxinos_watson_rat_40um_sigma_reference_test

It does NOT overwrite the stable atlas:

    paxinos_watson_rat_40um

Expected local project root:
    G:\rat-paxinos-brainglobe-builder

The script is intentionally conservative. It copies the current official candidate,
resamples SIGMA into the already oriented V32.2 Paxinos geometry, writes previews,
backs up any previous test atlas cache, patches last_versions.conf, and writes a
human-readable report.
"""

from __future__ import annotations

import configparser
import json
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

try:
    import nibabel as nib
    from nibabel.processing import resample_from_to
except Exception as exc:  # pragma: no cover - user environment diagnostic
    print("ERROR: nibabel could not be imported, or nibabel.processing is unavailable.")
    print("Install requirements in the project venv, then retry.")
    print("Original exception:", repr(exc))
    raise

try:
    import tifffile
except Exception as exc:  # pragma: no cover
    print("ERROR: tifffile could not be imported. Install requirements, then retry.")
    print("Original exception:", repr(exc))
    raise


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
OFFICIAL_ATLAS_NAME = "paxinos_watson_rat_40um"
TEST_ATLAS_NAME = "paxinos_watson_rat_40um_sigma_reference_test"
VERSION = "1.0"
OFFICIAL_CANDIDATE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / OFFICIAL_ATLAS_NAME
TEST_CANDIDATE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / TEST_ATLAS_NAME
REPORT_DIR = PROJECT_ROOT / "reports" / "v32_3_sigma_reference_test"
BG_DIR = Path.home() / ".brainglobe"
TEST_CACHE_DIR = BG_DIR / f"{TEST_ATLAS_NAME}_v{VERSION}"

SIGMA_NAME_HINTS = (
    "SIGMA_Anatomical_Brain_Atlas.nii",
    "SIGMA_Anatomical_Brain_Atlas.nii.gz",
    "sigma_anatomical_brain_atlas.nii",
    "sigma_anatomical_brain_atlas.nii.gz",
)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def die(message: str, code: int = 1) -> None:
    print("ERROR:", message)
    raise SystemExit(code)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def find_sigma_file() -> Optional[Path]:
    candidates: List[Path] = []

    for sub in [
        RAW_DIR / "bluebrainheadmodels",
        RAW_DIR / "BlueBrainHeadModels",
        RAW_DIR,
    ]:
        for name in SIGMA_NAME_HINTS:
            candidates.append(sub / name)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    patterns = ["*SIGMA*Anatomical*.nii*", "*sigma*anatomical*.nii*", "*SIGMA*.nii*", "*sigma*.nii*"]
    for pattern in patterns:
        matches = sorted(RAW_DIR.rglob(pattern)) if RAW_DIR.exists() else []
        for match in matches:
            if match.is_file() and ".nii" in match.name.lower():
                return match

    return None


def robust_uint16(data: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    arr = np.asarray(data, dtype=np.float32)
    finite = np.isfinite(arr)
    positive = finite & (arr > 0)
    sample = arr[positive]
    if sample.size < 100:
        sample = arr[finite]
    if sample.size == 0:
        return np.zeros(arr.shape, dtype=np.uint16), {
            "warning": "No finite voxels available for normalization.",
            "p_low": None,
            "p_high": None,
        }

    p_low = float(np.percentile(sample, 1.0))
    p_high = float(np.percentile(sample, 99.5))
    if not np.isfinite(p_low) or not np.isfinite(p_high) or p_high <= p_low:
        p_low = float(np.nanmin(sample))
        p_high = float(np.nanmax(sample))
    if p_high <= p_low:
        return np.zeros(arr.shape, dtype=np.uint16), {
            "warning": "Degenerate intensity range after percentile normalization.",
            "p_low": p_low,
            "p_high": p_high,
        }

    out = np.clip((arr - p_low) / (p_high - p_low), 0, 1)
    out = np.round(out * 65535).astype(np.uint16)
    # Keep true zero/background zero if present in source after resampling.
    out[~finite] = 0
    return out, {"p_low": p_low, "p_high": p_high, "warning": None}


def copy_candidate() -> None:
    if not OFFICIAL_CANDIDATE.exists():
        die(f"Official candidate not found: {OFFICIAL_CANDIDATE}. Run V32.2 builder first.")
    if TEST_CANDIDATE.exists():
        backup = TEST_CANDIDATE.with_name(TEST_CANDIDATE.name + "_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.move(str(TEST_CANDIDATE), str(backup))
    shutil.copytree(OFFICIAL_CANDIDATE, TEST_CANDIDATE)


def patch_metadata(reference_stats: Dict[str, Any], sigma_path: Path) -> Dict[str, Any]:
    metadata_path = TEST_CANDIDATE / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    original_name = metadata.get("name")
    metadata["name"] = TEST_ATLAS_NAME
    metadata["atlas_name"] = TEST_ATLAS_NAME
    metadata["title"] = "Paxinos-Watson Rat Brain Atlas with SIGMA anatomical reference, V32.3 test"
    metadata["version"] = VERSION
    metadata["status"] = "v32_3_sigma_reference_test"
    metadata["warning"] = (
        "Test atlas. Annotation/structures come from the oriented Paxinos-Watson candidate; "
        "reference.tiff is SIGMA anatomical image resampled into Paxinos display geometry. "
        "Do not treat this as final anatomical validation without visual QC."
    )
    metadata["reference_strategy"] = "v32_3_sigma_anatomical_resampled_to_oriented_paxinos_geometry_test"
    metadata["reference_file"] = "reference.tiff"
    metadata["annotation_file"] = "annotation.tiff"
    metadata["additional_references"] = []
    files = metadata.get("files") or {}
    files["reference"] = "reference.nii.gz"
    files["annotation"] = "annotation.nii.gz"
    files["reference_tiff"] = "reference.tiff"
    files["annotation_tiff"] = "annotation.tiff"
    files["reference_nifti"] = "reference.nii.gz"
    files["annotation_nifti"] = "annotation.nii.gz"
    files["structures"] = "structures.json"
    files["structures_csv"] = "structures.csv"
    # Do not list hemispheres inside files as a normal display channel.
    files.pop("hemispheres", None)
    metadata["files"] = files
    metadata["v32_3_sigma_reference_test"] = {
        "generated_at": now(),
        "source_sigma_path": str(sigma_path),
        "copied_from_atlas_name": original_name,
        "target_geometry": "current V32.2 official candidate annotation.nii.gz",
        "normalization": reference_stats,
        "display_channel_goal": "reference is anatomical background; ABBA-generated borders remain a separate overlay",
    }

    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def write_manifest(metadata: Dict[str, Any], sigma_path: Path) -> None:
    manifest = {
        "generated_at": now(),
        "atlas_name": TEST_ATLAS_NAME,
        "version": VERSION,
        "source_candidate": str(OFFICIAL_CANDIDATE),
        "target_candidate": str(TEST_CANDIDATE),
        "sigma_path": str(sigma_path),
        "metadata_name": metadata.get("name"),
        "shape": metadata.get("shape"),
        "orientation": metadata.get("orientation"),
        "reference_strategy": metadata.get("reference_strategy"),
    }
    (TEST_CANDIDATE / "native_install_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def install_cache() -> Dict[str, Any]:
    ensure_dir(BG_DIR)
    backup_root = BG_DIR / ("_paxinos_v32_3_sigma_reference_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    moved = None
    if TEST_CACHE_DIR.exists():
        ensure_dir(backup_root)
        dest = backup_root / TEST_CACHE_DIR.name
        shutil.move(str(TEST_CACHE_DIR), str(dest))
        moved = str(dest)
    shutil.copytree(TEST_CANDIDATE, TEST_CACHE_DIR)

    conf_path = BG_DIR / "last_versions.conf"
    parser = configparser.ConfigParser()
    existed = conf_path.exists()
    if existed:
        parser.read(conf_path, encoding="utf-8")
    if not parser.has_section("atlases"):
        parser.add_section("atlases")
    parser.set("atlases", TEST_ATLAS_NAME, VERSION)
    backup_conf = None
    if existed:
        backup_conf = conf_path.with_suffix(conf_path.suffix + ".backup_v32_3_sigma_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(conf_path, backup_conf)
    with conf_path.open("w", encoding="utf-8") as f:
        parser.write(f)

    return {
        "cache_dir": str(TEST_CACHE_DIR),
        "existing_cache_backup": moved,
        "last_versions_conf": str(conf_path),
        "last_versions_backup": str(backup_conf) if backup_conf else None,
    }


def maybe_write_previews(reference: np.ndarray, annotation: np.ndarray) -> Dict[str, Any]:
    result: Dict[str, Any] = {"attempted": False, "written": [], "error": None}
    try:
        import matplotlib.pyplot as plt
    except Exception as exc:
        result["error"] = f"matplotlib unavailable: {exc!r}"
        return result

    result["attempted"] = True
    ensure_dir(REPORT_DIR)

    def mids(shape: Tuple[int, int, int]) -> Tuple[int, int, int]:
        return tuple(int(s // 2) for s in shape)  # type: ignore[return-value]

    m0, m1, m2 = mids(reference.shape)
    panels = [
        (reference[m0, :, :], annotation[m0, :, :], "axis0_mid"),
        (reference[:, m1, :], annotation[:, m1, :], "axis1_mid"),
        (reference[:, :, m2], annotation[:, :, m2], "axis2_mid"),
    ]

    fig, axes = plt.subplots(3, 2, figsize=(10, 12))
    for row, (ref_sl, ann_sl, name) in enumerate(panels):
        axes[row, 0].imshow(np.rot90(ref_sl), cmap="gray")
        axes[row, 0].set_title(f"SIGMA reference {name}")
        axes[row, 0].axis("off")
        axes[row, 1].imshow(np.rot90(ref_sl), cmap="gray")
        mask = np.rot90(ann_sl > 0)
        axes[row, 1].imshow(mask, alpha=0.25, cmap="autumn")
        axes[row, 1].set_title(f"reference + Paxinos mask {name}")
        axes[row, 1].axis("off")
    fig.tight_layout()
    out = REPORT_DIR / "sigma_reference_test_preview_panels.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    result["written"].append(str(out))
    return result


def main() -> int:
    ensure_dir(REPORT_DIR)
    report: Dict[str, Any] = {
        "generated_at": now(),
        "project_root": str(PROJECT_ROOT),
        "official_candidate": str(OFFICIAL_CANDIDATE),
        "test_candidate": str(TEST_CANDIDATE),
        "test_atlas_name": TEST_ATLAS_NAME,
        "passed": False,
        "steps": [],
    }

    try:
        sigma_path = find_sigma_file()
        if sigma_path is None:
            die(
                "Could not find SIGMA anatomical NIfTI under data/raw. Expected something like "
                "data/raw/bluebrainheadmodels/SIGMA_Anatomical_Brain_Atlas.nii"
            )
        report["sigma_path"] = str(sigma_path)

        copy_candidate()
        report["steps"].append("copied current official candidate into separate SIGMA test candidate")

        target_annotation_path = TEST_CANDIDATE / "annotation.nii.gz"
        if not target_annotation_path.exists():
            die(f"Target annotation missing after copy: {target_annotation_path}")
        target_img = nib.load(str(target_annotation_path))
        sigma_img = nib.load(str(sigma_path))

        report["target"] = {
            "shape": list(target_img.shape),
            "affine": np.asarray(target_img.affine).round(6).tolist(),
        }
        report["sigma"] = {
            "shape": list(sigma_img.shape),
            "affine": np.asarray(sigma_img.affine).round(6).tolist(),
        }

        print("Resampling SIGMA into V32.2 oriented Paxinos geometry...")
        resampled = resample_from_to(sigma_img, (target_img.shape, target_img.affine), order=1)
        ref_uint16, norm = robust_uint16(np.asanyarray(resampled.dataobj))
        report["normalization"] = norm
        report["reference_stats"] = {
            "shape": list(ref_uint16.shape),
            "dtype": str(ref_uint16.dtype),
            "min": int(ref_uint16.min()),
            "max": int(ref_uint16.max()),
            "nonzero_fraction": float(np.count_nonzero(ref_uint16) / ref_uint16.size),
        }
        if int(ref_uint16.max()) == 0:
            die("Resampled SIGMA reference is all zero. Do not install this as anatomical reference.")

        # Write reference NIfTI with target affine/header and reference TIFF.
        ref_img = nib.Nifti1Image(ref_uint16, target_img.affine, target_img.header)
        ref_img.set_data_dtype(np.uint16)
        nib.save(ref_img, str(TEST_CANDIDATE / "reference.nii.gz"))
        tifffile.imwrite(TEST_CANDIDATE / "reference.tiff", ref_uint16, photometric="minisblack")
        report["steps"].append("wrote SIGMA reference.nii.gz and reference.tiff")

        annotation = tifffile.imread(TEST_CANDIDATE / "annotation.tiff")
        preview_result = maybe_write_previews(ref_uint16, annotation)
        report["preview"] = preview_result

        metadata = patch_metadata(report["reference_stats"], sigma_path)
        write_manifest(metadata, sigma_path)
        report["metadata"] = {
            "name": metadata.get("name"),
            "shape": metadata.get("shape"),
            "orientation": metadata.get("orientation"),
            "reference_strategy": metadata.get("reference_strategy"),
            "additional_references": metadata.get("additional_references"),
            "files": metadata.get("files"),
        }

        install_info = install_cache()
        report["install"] = install_info
        report["passed"] = True

    except SystemExit:
        raise
    except Exception as exc:
        report["exception"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        print(report["traceback"])
        report["passed"] = False
    finally:
        ensure_dir(REPORT_DIR)
        (REPORT_DIR / "v32_3_sigma_reference_test_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        lines = [
            "V32.3 SIGMA reference test atlas report",
            "========================================================================",
            f"Generated: {report.get('generated_at')}",
            f"PASSED: {report.get('passed')}",
            f"Test atlas: {TEST_ATLAS_NAME}",
            f"SIGMA path: {report.get('sigma_path')}",
            f"Test candidate: {TEST_CANDIDATE}",
            f"Cache dir: {TEST_CACHE_DIR}",
            "",
            "Reference stats:",
            json.dumps(report.get("reference_stats"), indent=2),
            "",
            "Metadata:",
            json.dumps(report.get("metadata"), indent=2),
            "",
            "Interpretation:",
            "- This is a separate test atlas for ABBA visual QC.",
            "- If the reference background looks anatomically useful, it can be promoted later.",
            "- If it looks shifted/black/distorted, do not integrate it into the stable atlas.",
        ]
        (REPORT_DIR / "v32_3_sigma_reference_test_report.txt").write_text("\n".join(lines), encoding="utf-8")

    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
