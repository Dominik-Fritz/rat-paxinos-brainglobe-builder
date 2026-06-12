"""
V32.3: Build a NULL-reference ABBA debug atlas.

This creates a separate atlas where reference.tiff/reference.nii.gz are all zero,
while annotation/structures/metadata remain based on the current V32.2 official
candidate. It is useful to test whether ABBA is still displaying annotation/borders
or some internal overlay even when the reference channel is empty.
"""

from __future__ import annotations

import configparser
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import nibabel as nib
import numpy as np
import tifffile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_ATLAS_NAME = "paxinos_watson_rat_40um"
TEST_ATLAS_NAME = "paxinos_watson_rat_40um_null_reference_debug"
VERSION = "1.0"
OFFICIAL_CANDIDATE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / OFFICIAL_ATLAS_NAME
TEST_CANDIDATE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / TEST_ATLAS_NAME
REPORT_DIR = PROJECT_ROOT / "reports" / "v32_3_null_reference_debug"
BG_DIR = Path.home() / ".brainglobe"
TEST_CACHE_DIR = BG_DIR / f"{TEST_ATLAS_NAME}_v{VERSION}"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_candidate() -> None:
    if not OFFICIAL_CANDIDATE.exists():
        raise FileNotFoundError(f"Official candidate not found: {OFFICIAL_CANDIDATE}")
    if TEST_CANDIDATE.exists():
        backup = TEST_CANDIDATE.with_name(TEST_CANDIDATE.name + "_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.move(str(TEST_CANDIDATE), str(backup))
    shutil.copytree(OFFICIAL_CANDIDATE, TEST_CANDIDATE)


def patch_metadata() -> Dict[str, Any]:
    metadata_path = TEST_CANDIDATE / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["name"] = TEST_ATLAS_NAME
    metadata["atlas_name"] = TEST_ATLAS_NAME
    metadata["title"] = "Paxinos-Watson Rat Brain Atlas, NULL reference ABBA debug test"
    metadata["version"] = VERSION
    metadata["status"] = "v32_3_null_reference_debug"
    metadata["reference_strategy"] = "v32_3_null_zero_reference_for_abba_channel_diagnostics"
    metadata["warning"] = "Debug atlas only. reference.tiff is intentionally all zero. Do not use for analysis."
    metadata["additional_references"] = []
    files = metadata.get("files") or {}
    files.pop("hemispheres", None)
    metadata["files"] = files
    metadata["v32_3_null_reference_debug"] = {
        "generated_at": now(),
        "purpose": "If ABBA still displays atlas content with this reference hidden/zero, the visible content is not the reference image.",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def install_cache() -> Dict[str, Any]:
    ensure_dir(BG_DIR)
    backup_root = BG_DIR / ("_paxinos_v32_3_null_reference_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
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
        backup_conf = conf_path.with_suffix(conf_path.suffix + ".backup_v32_3_null_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(conf_path, backup_conf)
    with conf_path.open("w", encoding="utf-8") as f:
        parser.write(f)
    return {"cache_dir": str(TEST_CACHE_DIR), "existing_cache_backup": moved, "last_versions_conf": str(conf_path), "last_versions_backup": str(backup_conf) if backup_conf else None}


def main() -> int:
    ensure_dir(REPORT_DIR)
    report: Dict[str, Any] = {"generated_at": now(), "passed": False, "test_atlas_name": TEST_ATLAS_NAME}
    try:
        copy_candidate()
        annotation_img = nib.load(str(TEST_CANDIDATE / "annotation.nii.gz"))
        zeros = np.zeros(annotation_img.shape, dtype=np.uint16)
        zero_img = nib.Nifti1Image(zeros, annotation_img.affine, annotation_img.header)
        zero_img.set_data_dtype(np.uint16)
        nib.save(zero_img, str(TEST_CANDIDATE / "reference.nii.gz"))
        tifffile.imwrite(TEST_CANDIDATE / "reference.tiff", zeros, photometric="minisblack")
        metadata = patch_metadata()
        install_info = install_cache()
        report.update({
            "passed": True,
            "candidate": str(TEST_CANDIDATE),
            "cache": install_info,
            "shape": list(zeros.shape),
            "reference_max": int(zeros.max()),
            "metadata_name": metadata.get("name"),
            "orientation": metadata.get("orientation"),
        })
    except Exception as exc:
        report["exception"] = repr(exc)
        report["passed"] = False

    (REPORT_DIR / "v32_3_null_reference_debug_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "V32.3 NULL reference debug atlas report",
        "========================================================================",
        f"Generated: {report.get('generated_at')}",
        f"PASSED: {report.get('passed')}",
        f"Test atlas: {TEST_ATLAS_NAME}",
        f"Shape: {report.get('shape')}",
        f"Reference max: {report.get('reference_max')}",
        "",
        "ABBA interpretation:",
        "- Open paxinos_watson_rat_40um_null_reference_debug.",
        "- Set reference and borders to 0.",
        "- If anything still appears, it is ABBA's display/overlay behavior, not reference.tiff.",
        "- If only borders appear when borders >0, the stable atlas channel model is behaving as expected.",
    ]
    (REPORT_DIR / "v32_3_null_reference_debug_report.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
