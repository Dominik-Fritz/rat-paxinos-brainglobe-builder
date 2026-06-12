from __future__ import annotations

import configparser
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

try:
    import nibabel as nib
except Exception:
    nib = None


SOURCE_ATLAS_NAME = "paxinos_watson_rat_40um"
SOURCE_VERSIONED = "paxinos_watson_rat_40um_v1.0"

TEST_ATLAS_NAME = "paxinos_watson_rat_40um_abba_coronal_upright_test"
TEST_VERSION = "1.0"
TEST_VERSIONED = f"{TEST_ATLAS_NAME}_v{TEST_VERSION}"

# Critical transform:
# original V32 array axes are interpreted as [LR, AP, SI]
# ABBA button mapping wants axis 0 for coronal/AP-fixed.
# Coronal display must have rows=SI and columns=LR.
# Therefore new axes are [AP, SI, LR].
PERM = (1, 2, 0)
NEW_ORIENTATION = "PIL"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def reports_dir() -> Path:
    p = project_root() / "reports" / "orientation_coronal_upright_test"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_brainglobe_dir() -> Path:
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


def transform_tiff_file(src: Path, dst: Path) -> dict[str, Any]:
    arr = tifffile.imread(str(src))
    out = transform_array(arr)
    tifffile.imwrite(str(dst), out.astype(arr.dtype, copy=False), photometric="minisblack")
    return {
        "source": str(src),
        "target": str(dst),
        "old_shape": list(arr.shape),
        "new_shape": list(out.shape),
        "dtype": str(out.dtype),
        "min": int(np.min(out)) if np.issubdtype(out.dtype, np.integer) else float(np.nanmin(out)),
        "max": int(np.max(out)) if np.issubdtype(out.dtype, np.integer) else float(np.nanmax(out)),
    }


def transform_nifti_file(src: Path, dst: Path) -> dict[str, Any]:
    if nib is None:
        return {
            "source": str(src),
            "target": str(dst),
            "skipped": True,
            "reason": "nibabel not available",
        }
    if not src.exists():
        return {
            "source": str(src),
            "target": str(dst),
            "skipped": True,
            "reason": "source_missing",
        }

    img = nib.load(str(src))
    arr = np.asanyarray(img.dataobj)
    out = transform_array(np.asarray(arr))

    # Permute affine columns consistently with the array permutation.
    # This is not a new registration. It is only an axis-order display test.
    new_affine = img.affine.copy()
    new_affine[:3, :3] = img.affine[:3, list(PERM)]

    out_img = nib.Nifti1Image(out.astype(arr.dtype, copy=False), new_affine, header=img.header.copy())
    out_img.set_data_dtype(out.dtype)
    nib.save(out_img, str(dst))

    try:
        orientation = "".join(nib.aff2axcodes(new_affine))
    except Exception:
        orientation = None

    return {
        "source": str(src),
        "target": str(dst),
        "old_shape": list(arr.shape),
        "new_shape": list(out.shape),
        "dtype": str(out.dtype),
        "new_orientation_from_affine": orientation,
        "skipped": False,
    }


def patch_metadata(folder: Path, source_folder: Path, array_report: dict[str, Any]) -> dict[str, Any]:
    mp = folder / "metadata.json"
    metadata = load_json(mp)

    old_name = metadata.get("name")
    old_orientation = metadata.get("orientation")
    old_shape = metadata.get("shape")

    # shape from transformed reference.tiff, which ABBA/BrainGlobe actually displays
    ref_shape = array_report["reference.tiff"]["new_shape"]

    metadata["name"] = TEST_ATLAS_NAME
    metadata["atlas_name"] = TEST_ATLAS_NAME
    metadata["title"] = "Paxinos-Watson Rat Brain Atlas, ABBA coronal upright orientation test"
    metadata["version"] = TEST_VERSION
    metadata["orientation"] = NEW_ORIENTATION
    metadata["shape"] = ref_shape
    metadata["reference_shape"] = ref_shape
    metadata["annotation_shape"] = ref_shape
    metadata["reference_strategy"] = (
        "orientation_display_test_perm_1_2_0_from_v32_edge_reference"
    )
    metadata["status"] = "experimental_orientation_display_test"
    metadata["warning"] = (
        "Experimental ABBA display-orientation test. Do not use for analysis until validated. "
        "Transforms arrays from original [LR, AP, SI] to [AP, SI, LR] with perm=(1,2,0)."
    )
    metadata["orientation_display_test"] = {
        "source_atlas_folder": str(source_folder),
        "source_name": old_name,
        "source_orientation": old_orientation,
        "source_shape": old_shape,
        "new_orientation": NEW_ORIENTATION,
        "perm": list(PERM),
        "old_axis_interpretation": ["LR", "AP", "SI"],
        "new_axis_interpretation": ["AP", "SI", "LR"],
        "purpose": (
            "Fix ABBA button mapping and coronal plane display: Coronal should be AP-fixed, "
            "with superior/inferior vertical and left/right horizontal."
        ),
    }
    metadata["additional_references"] = []
    metadata["reference_file"] = "reference.tiff"
    metadata["annotation_file"] = "annotation.tiff"
    metadata["hemispheres_file"] = "hemispheres.tiff"
    metadata["files"] = metadata.get("files", {})
    metadata["files"]["reference_tiff"] = "reference.tiff"
    metadata["files"]["annotation_tiff"] = "annotation.tiff"
    metadata["files"]["hemispheres"] = "hemispheres.tiff"
    metadata["files"]["reference_nifti"] = "reference.nii.gz"
    metadata["files"]["annotation_nifti"] = "annotation.nii.gz"

    save_json(mp, metadata)
    return {
        "metadata_path": str(mp),
        "old_name": old_name,
        "new_name": TEST_ATLAS_NAME,
        "old_orientation": old_orientation,
        "new_orientation": NEW_ORIENTATION,
        "old_shape": old_shape,
        "new_shape": ref_shape,
    }


def patch_manifest(folder: Path) -> dict[str, Any]:
    manifest_path = folder / "candidate_manifest.json"
    if not manifest_path.exists():
        manifest = {}
    else:
        manifest = load_json(manifest_path)

    manifest["atlas_name"] = TEST_ATLAS_NAME
    manifest["status"] = "experimental_orientation_display_test"
    manifest["note"] = (
        "ABBA orientation display test. Arrays transformed by perm=(1,2,0), "
        "expected axes [AP, SI, LR]."
    )
    if (folder / "metadata.json").exists():
        manifest["metadata"] = load_json(folder / "metadata.json")

    save_json(manifest_path, manifest)
    return {"manifest_path": str(manifest_path), "patched": True}


def copy_and_transform(source: Path, target: Path) -> dict[str, Any]:
    if not source.exists():
        raise FileNotFoundError(f"Missing source atlas: {source}")

    if target.exists():
        backup = target.with_name(target.name + "_backup_" + now_stamp())
        shutil.move(str(target), str(backup))
    else:
        backup = None

    shutil.copytree(source, target)

    array_report = {}
    for name in ["reference.tiff", "annotation.tiff", "hemispheres.tiff"]:
        src = source / name
        dst = target / name
        if src.exists():
            array_report[name] = transform_tiff_file(src, dst)
        else:
            array_report[name] = {"source": str(src), "target": str(dst), "skipped": True, "reason": "missing"}

    nifti_report = {}
    for name in ["reference.nii.gz", "annotation.nii.gz"]:
        src = source / name
        dst = target / name
        nifti_report[name] = transform_nifti_file(src, dst)

    metadata_report = patch_metadata(target, source, array_report)
    manifest_report = patch_manifest(target)

    return {
        "source": str(source),
        "target": str(target),
        "backup_previous_target": str(backup) if backup else None,
        "array_report": array_report,
        "nifti_report": nifti_report,
        "metadata_report": metadata_report,
        "manifest_report": manifest_report,
    }


def patch_last_versions(bg_dir: Path) -> dict[str, Any]:
    conf = bg_dir / "last_versions.conf"
    backup = bg_dir / f"last_versions.conf.backup_coronal_upright_{now_stamp()}"

    parser = configparser.ConfigParser()
    parser.optionxform = str

    existed_before = conf.exists()
    if existed_before:
        shutil.copy2(conf, backup)
        parser.read(conf, encoding="utf-8")

    if not parser.has_section("atlases"):
        parser.add_section("atlases")

    before = parser.get("atlases", TEST_ATLAS_NAME) if parser.has_option("atlases", TEST_ATLAS_NAME) else None
    parser.set("atlases", TEST_ATLAS_NAME, TEST_VERSION)

    with conf.open("w", encoding="utf-8") as f:
        parser.write(f)

    return {
        "conf": str(conf),
        "backup": str(backup) if existed_before else None,
        "entry_before": before,
        "entry_after": TEST_VERSION,
        "patched": True,
    }


def write_report(result: dict[str, Any]) -> None:
    rd = reports_dir()
    save_json(rd / "orientation_coronal_upright_test_report.json", result)

    lines = [
        "Orientation coronal-upright ABBA display test",
        "=" * 72,
        f"Generated: {result['generated_at']}",
        f"Source atlas: {result['source_atlas']}",
        f"Test atlas: {result['test_atlas_name']}",
        f"Test versioned folder: {result['test_versioned_folder']}",
        "",
        "Transform:",
        "- old axes: [LR, AP, SI]",
        "- new axes: [AP, SI, LR]",
        "- perm: (1, 2, 0)",
        "- metadata orientation: PIL",
        "",
        "Expected ABBA:",
        "- Coronal button: coronal, dorsal/top vertical, left-right horizontal",
        "- Sagittal button: sagittal",
        "- Horizontal button: horizontal",
        "",
        "Array reports:",
    ]
    for name, rep in result["copy_transform"]["array_report"].items():
        lines.append(f"[{name}]")
        for k, v in rep.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines += [
        "Last versions patch:",
    ]
    for k, v in result["last_versions"].items():
        lines.append(f"- {k}: {v}")

    (rd / "orientation_coronal_upright_test_report.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    bg_dir = default_brainglobe_dir()
    source = bg_dir / SOURCE_VERSIONED
    target = bg_dir / TEST_VERSIONED

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_atlas": str(source),
        "source_exists": source.exists(),
        "test_atlas_name": TEST_ATLAS_NAME,
        "test_version": TEST_VERSION,
        "test_versioned_folder": str(target),
        "perm": list(PERM),
        "new_orientation": NEW_ORIENTATION,
    }

    result["copy_transform"] = copy_and_transform(source, target)
    result["last_versions"] = patch_last_versions(bg_dir)
    result["passed"] = target.exists() and (target / "reference.tiff").exists() and (target / "metadata.json").exists()

    write_report(result)

    print("")
    print("Created test atlas:")
    print(f"  {TEST_ATLAS_NAME}")
    print("")
    print("Folder:")
    print(f"  {target}")
    print("")
    print("Report:")
    print(f"  {reports_dir() / 'orientation_coronal_upright_test_report.txt'}")
    print("")
    print("Restart Fiji/ABBA completely before testing.")
    print("")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
