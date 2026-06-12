from __future__ import annotations

import configparser
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import tifffile

SOURCE_ATLAS_NAME = "paxinos_watson_rat_40um"
SOURCE_VERSIONED = "paxinos_watson_rat_40um_v1.0"
TEST_ATLAS_NAME = "paxinos_watson_rat_40um_abba_buttons_test"
TEST_VERSION = "1.0"
TEST_VERSIONED = f"{TEST_ATLAS_NAME}_v{TEST_VERSION}"

# Goal: make ABBA buttons match anatomical planes if ABBA maps:
#   Coronal    -> array axis 0
#   Sagittal   -> array axis 1
#   Horizontal -> array axis 2
# Required axis order:
#   axis 0 = AP, axis 1 = LR, axis 2 = SI
# Original V32 atlas axis order from LPI:
#   axis 0 = LR, axis 1 = AP, axis 2 = SI
# Therefore new = old[AP, LR, SI] = perm (1, 0, 2)
PERM = (1, 0, 2)
NEW_ORIENTATION = "PLI"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def brainglobe_dir() -> Path:
    try:
        from brainglobe_atlasapi import config
        return Path(config.get_brainglobe_dir())
    except Exception:
        return Path.home() / ".brainglobe"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def transform_array(arr: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(np.transpose(arr, PERM))


def transform_nifti(src_path: Path, dst_path: Path, dtype: np.dtype | None = None) -> dict[str, Any]:
    img = nib.load(str(src_path))
    data = np.asanyarray(img.dataobj)
    data_t = transform_array(np.asarray(data))
    if dtype is not None:
        data_t = data_t.astype(dtype)

    old_aff = img.affine.copy()
    new_aff = old_aff.copy()
    # New voxel axis i corresponds to old voxel axis PERM[i].
    for new_axis, old_axis in enumerate(PERM):
        new_aff[:3, new_axis] = old_aff[:3, old_axis]
    # Translation stays identical: voxel origin is not shifted by a pure transpose.

    header = img.header.copy()
    zooms = img.header.get_zooms()[:3]
    try:
        header.set_zooms(tuple(float(zooms[i]) for i in PERM))
    except Exception:
        pass

    out = nib.Nifti1Image(data_t, new_aff, header=header)
    if dtype is not None:
        out.set_data_dtype(dtype)
    nib.save(out, str(dst_path))

    return {
        "source": str(src_path),
        "target": str(dst_path),
        "old_shape": list(img.shape),
        "new_shape": list(data_t.shape),
        "old_orientation": "".join(nib.aff2axcodes(old_aff)),
        "new_orientation": "".join(nib.aff2axcodes(new_aff)),
        "old_affine": old_aff.tolist(),
        "new_affine": new_aff.tolist(),
    }


def patch_last_versions(bg_dir: Path) -> dict[str, Any]:
    conf = bg_dir / "last_versions.conf"
    backup = bg_dir / f"last_versions.conf.backup_abba_buttons_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    parser = configparser.ConfigParser()
    parser.optionxform = str
    existed = conf.exists()
    if existed:
        shutil.copy2(conf, backup)
        parser.read(conf, encoding="utf-8")
    if not parser.has_section("atlases"):
        parser.add_section("atlases")
    before = parser.get("atlases", TEST_ATLAS_NAME, fallback=None)
    parser.set("atlases", TEST_ATLAS_NAME, TEST_VERSION)
    with conf.open("w", encoding="utf-8") as f:
        parser.write(f)
    return {
        "conf": str(conf),
        "backup": str(backup) if existed else None,
        "existed_before": existed,
        "entry_before": before,
        "entry_after": TEST_VERSION,
        "patched": True,
    }


def main() -> int:
    bg_dir = brainglobe_dir()
    src = bg_dir / SOURCE_VERSIONED
    dst = bg_dir / TEST_VERSIONED
    report_dir = Path.cwd() / "reports" / "abba_button_mapping_test"
    report_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"Missing source atlas folder: {src}")

    required = [
        "reference.tiff",
        "annotation.tiff",
        "hemispheres.tiff",
        "reference.nii.gz",
        "annotation.nii.gz",
        "metadata.json",
        "structures.json",
    ]
    missing = [name for name in required if not (src / name).exists()]
    if missing:
        raise FileNotFoundError(f"Source atlas is missing required files: {missing}")

    if dst.exists():
        backup = bg_dir / f"_{TEST_VERSIONED}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.move(str(dst), str(backup))
    else:
        backup = None
    dst.mkdir(parents=True, exist_ok=True)

    # Copy structure/meta side files first.
    for name in ["structures.json", "structures.csv", "README.md", "candidate_manifest.json", "native_install_manifest.json"]:
        p = src / name
        if p.exists():
            shutil.copy2(p, dst / name)

    # TIFF transforms.
    tiff_reports = {}
    for name in ["reference.tiff", "annotation.tiff", "hemispheres.tiff"]:
        arr = tifffile.imread(str(src / name))
        arr_t = transform_array(arr)
        tifffile.imwrite(str(dst / name), arr_t.astype(arr.dtype), photometric="minisblack")
        tiff_reports[name] = {
            "old_shape": list(arr.shape),
            "new_shape": list(arr_t.shape),
            "dtype": str(arr.dtype),
            "unique_sample": [int(x) for x in np.unique(arr_t)[:20]] if name != "reference.tiff" else None,
        }

    # NIfTI transforms with affine permutation.
    nifti_reports = {
        "reference.nii.gz": transform_nifti(src / "reference.nii.gz", dst / "reference.nii.gz", np.uint16),
        "annotation.nii.gz": transform_nifti(src / "annotation.nii.gz", dst / "annotation.nii.gz", np.uint16),
    }

    # Metadata patch.
    meta = load_json(src / "metadata.json")
    old_meta = dict(meta)
    new_shape = tiff_reports["reference.tiff"]["new_shape"]
    meta["name"] = TEST_ATLAS_NAME
    meta["atlas_name"] = TEST_ATLAS_NAME
    meta["title"] = "Paxinos-Watson Rat Brain Atlas, ABBA button mapping test"
    meta["version"] = TEST_VERSION
    meta["orientation"] = NEW_ORIENTATION
    meta["shape"] = new_shape
    meta["reference_shape"] = new_shape
    meta["annotation_shape"] = new_shape
    meta["reference_strategy"] = "v32_edge_reference_with_abba_button_mapping_axis_order_test"
    meta["status"] = "experimental_abba_button_mapping_test"
    meta["warning"] = (
        "Experimental display-axis test atlas. Axis order changed with perm=(1,0,2): "
        "old [LR,AP,SI] -> new [AP,LR,SI]. Do not treat as validated until ABBA buttons are checked."
    )
    meta["display_axis_test"] = {
        "source_atlas": SOURCE_VERSIONED,
        "new_atlas": TEST_VERSIONED,
        "perm": list(PERM),
        "old_orientation_expected": old_meta.get("orientation"),
        "new_orientation": NEW_ORIENTATION,
        "intended_abba_mapping": {
            "Coronal_button": "coronal plane, fixed AP axis 0",
            "Sagittal_button": "sagittal plane, fixed LR axis 1",
            "Horizontal_button": "horizontal plane, fixed SI axis 2",
        },
        "desired_sagittal_display": "anterior left, posterior right; superior top, inferior bottom",
    }
    meta["additional_references"] = []
    meta["reference_file"] = "reference.tiff"
    meta["annotation_file"] = "annotation.tiff"
    meta["hemispheres_file"] = "hemispheres.tiff"
    meta["files"] = meta.get("files", {})
    meta["files"].update({
        "reference": "reference.nii.gz",
        "annotation": "annotation.nii.gz",
        "reference_tiff": "reference.tiff",
        "annotation_tiff": "annotation.tiff",
        "hemispheres": "hemispheres.tiff",
        "structures": "structures.json",
    })
    save_json(dst / "metadata.json", meta)

    # Candidate manifest patch if present.
    cand_path = dst / "candidate_manifest.json"
    if cand_path.exists():
        cand = load_json(cand_path)
        cand["atlas_name"] = TEST_ATLAS_NAME
        cand["status"] = "experimental_abba_button_mapping_test"
        cand["metadata"] = meta
        cand["note"] = "Created as a separate non-destructive ABBA button mapping test atlas."
        save_json(cand_path, cand)

    manifest = {
        "generated_at": now(),
        "atlas_name": TEST_ATLAS_NAME,
        "version": TEST_VERSION,
        "versioned_full_name": TEST_VERSIONED,
        "source": str(src),
        "target": str(dst),
        "perm": list(PERM),
        "orientation": NEW_ORIENTATION,
        "backup_of_previous_test_target": str(backup) if backup else None,
        "tiff_reports": tiff_reports,
        "nifti_reports": nifti_reports,
    }
    save_json(dst / "abba_button_mapping_test_manifest.json", manifest)

    last_versions = patch_last_versions(bg_dir)

    report = {
        "generated_at": now(),
        "source_atlas": str(src),
        "target_atlas": str(dst),
        "test_atlas_name": TEST_ATLAS_NAME,
        "perm": list(PERM),
        "new_orientation": NEW_ORIENTATION,
        "new_shape": new_shape,
        "last_versions": last_versions,
        "passed": True,
        "next_step": "Restart Fiji/ABBA and open paxinos_watson_rat_40um_abba_buttons_test. Check Coronal/Sagittal/Horizontal buttons.",
    }
    save_json(report_dir / "abba_button_mapping_test_report.json", report)

    lines = [
        "ABBA button mapping test atlas report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Source atlas: {src}",
        f"Target atlas: {dst}",
        f"Atlas name in ABBA: {TEST_ATLAS_NAME}",
        f"Permutation: {PERM}",
        f"New orientation: {NEW_ORIENTATION}",
        f"New shape: {new_shape}",
        "",
        "Expected if correct:",
        "- Coronal button -> coronal plane",
        "- Sagittal button -> sagittal plane",
        "- Horizontal button -> horizontal plane",
        "- Sagittal display: anterior left, posterior right",
        "",
        "This is a separate test atlas. The original paxinos_watson_rat_40um_v1.0 was not overwritten.",
    ]
    (report_dir / "abba_button_mapping_test_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print("Created ABBA button mapping test atlas:", dst)
    print("Open in ABBA:", TEST_ATLAS_NAME)
    print("Report:", report_dir / "abba_button_mapping_test_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
