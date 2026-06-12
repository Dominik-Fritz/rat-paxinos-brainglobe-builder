from __future__ import annotations

import argparse
import configparser
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

try:
    import nibabel as nib
except Exception:
    nib = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    plt = None


SOURCE_ATLAS_NAME = "paxinos_watson_rat_40um"
SOURCE_VERSION = "1.0"
SOURCE_FOLDER_NAME = f"{SOURCE_ATLAS_NAME}_v{SOURCE_VERSION}"

TEST_ATLAS_NAME = "paxinos_watson_rat_40um_sag_ap_lr_test"
TEST_VERSION = "1.0"
TEST_FOLDER_NAME = f"{TEST_ATLAS_NAME}_v{TEST_VERSION}"

PERM = (0, 2, 1)
FLIPS = (False, False, False)
OLD_ORIENTATION = "LPI"
NEW_ORIENTATION = "LIP"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_brainglobe_dir() -> Path:
    try:
        from brainglobe_atlasapi import config
        return Path(config.get_brainglobe_dir())
    except Exception:
        return Path.home() / ".brainglobe"


def transform_array(arr: np.ndarray) -> np.ndarray:
    out = np.transpose(arr, PERM)
    for ax, do_flip in enumerate(FLIPS):
        if do_flip:
            out = np.flip(out, axis=ax)
    return np.ascontiguousarray(out)


def transform_affine_for_permutation(old_affine: np.ndarray, old_shape: tuple[int, int, int]) -> np.ndarray:
    """Return affine for new_data = old_data.transpose(PERM), no flips.

    new voxel coordinates [i,j,k] map to old coordinates:
      old[PERM[n]] = new[n]
    Example PERM=(0,2,1):
      old = [new0, new2, new1]
    """
    m = np.eye(4, dtype=float)
    m[:3, :3] = 0.0
    for new_axis, old_axis in enumerate(PERM):
        m[old_axis, new_axis] = 1.0
    return old_affine @ m


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def patch_metadata(dst: Path, src: Path) -> dict[str, Any]:
    mp = dst / "metadata.json"
    meta = load_json(mp)

    old_shape = meta.get("shape")
    new_shape = [int(x) for x in tifffile.imread(str(dst / "reference.tiff")).shape]

    meta["name"] = TEST_ATLAS_NAME
    meta["atlas_name"] = TEST_ATLAS_NAME
    meta["title"] = meta.get("title", "Paxinos-Watson Rat Brain Atlas") + " - sagittal AP-left-right display test"
    meta["version"] = TEST_VERSION
    meta["shape"] = new_shape
    meta["reference_shape"] = new_shape
    meta["annotation_shape"] = new_shape
    meta["orientation"] = NEW_ORIENTATION
    meta["reference_file"] = "reference.tiff"
    meta["annotation_file"] = "annotation.tiff"
    meta["hemispheres_file"] = "hemispheres.tiff"
    meta["additional_references"] = []

    meta["display_orientation_test"] = {
        "created_at": now(),
        "source_atlas_name": SOURCE_ATLAS_NAME,
        "source_folder": str(src),
        "old_shape": old_shape,
        "new_shape": new_shape,
        "old_orientation": meta.get("display_orientation_test", {}).get("old_orientation", OLD_ORIENTATION),
        "new_orientation": NEW_ORIENTATION,
        "perm": list(PERM),
        "flips": list(FLIPS),
        "purpose": "Sagittal display test: AP axis should appear left-right, with anterior left and posterior right.",
        "warning": "This is a display-orientation test copy. It is not a nonlinear registration and does not fix the synthetic reference problem.",
    }

    meta["files"] = meta.get("files", {})
    meta["files"]["reference_tiff"] = "reference.tiff"
    meta["files"]["annotation_tiff"] = "annotation.tiff"
    meta["files"]["hemispheres"] = "hemispheres.tiff"
    meta["files"]["reference_nifti"] = "reference.nii.gz"
    meta["files"]["annotation_nifti"] = "annotation.nii.gz"

    save_json(mp, meta)
    return {
        "metadata_path": str(mp),
        "old_shape": old_shape,
        "new_shape": new_shape,
        "new_orientation": NEW_ORIENTATION,
    }


def transform_tiffs(dst: Path) -> dict[str, Any]:
    out = {}
    for name in ["reference.tiff", "annotation.tiff", "hemispheres.tiff"]:
        p = dst / name
        if not p.exists():
            out[name] = {"exists": False, "transformed": False}
            continue
        arr = tifffile.imread(str(p))
        arr_t = transform_array(arr)
        tifffile.imwrite(str(p), arr_t.astype(arr.dtype, copy=False), photometric="minisblack")
        out[name] = {
            "exists": True,
            "old_shape": list(arr.shape),
            "new_shape": list(arr_t.shape),
            "dtype": str(arr_t.dtype),
            "min": int(np.min(arr_t)),
            "max": int(np.max(arr_t)),
            "transformed": True,
        }
    return out


def transform_niftis(dst: Path) -> dict[str, Any]:
    out = {}
    if nib is None:
        return {"skipped": True, "reason": "nibabel is not installed"}
    for name in ["reference.nii.gz", "annotation.nii.gz"]:
        p = dst / name
        if not p.exists():
            out[name] = {"exists": False, "transformed": False}
            continue
        img = nib.load(str(p))
        data = np.asanyarray(img.dataobj)
        data_t = transform_array(np.asarray(data))
        new_affine = transform_affine_for_permutation(img.affine, tuple(data.shape))
        new_img = nib.Nifti1Image(data_t, new_affine, header=img.header.copy())
        new_img.set_data_dtype(data_t.dtype)
        nib.save(new_img, str(p))
        out[name] = {
            "exists": True,
            "old_shape": list(data.shape),
            "new_shape": list(data_t.shape),
            "old_orientation": "".join(nib.aff2axcodes(img.affine)),
            "new_orientation": "".join(nib.aff2axcodes(new_affine)),
            "dtype": str(data_t.dtype),
            "transformed": True,
        }
    return out


def patch_candidate_manifest(dst: Path, src: Path) -> dict[str, Any]:
    path = dst / "candidate_manifest.json"
    manifest = {
        "atlas_name": TEST_ATLAS_NAME,
        "candidate_format": "brain-globe-style-local-display-orientation-test",
        "status": "experimental_display_test",
        "generated_from": str(src),
        "created_at": now(),
        "orientation_transform": {
            "source_atlas_name": SOURCE_ATLAS_NAME,
            "old_orientation": OLD_ORIENTATION,
            "new_orientation": NEW_ORIENTATION,
            "perm": list(PERM),
            "flips": list(FLIPS),
        },
        "files": {
            "reference": "reference.nii.gz",
            "annotation": "annotation.nii.gz",
            "reference_tiff": "reference.tiff",
            "annotation_tiff": "annotation.tiff",
            "hemispheres": "hemispheres.tiff",
            "structures": "structures.json",
            "metadata": "metadata.json",
        },
        "note": "This is a non-destructive test atlas to evaluate sagittal AP left-right display in BrainGlobe/ABBA.",
    }
    save_json(path, manifest)
    return {"manifest_path": str(path)}


def patch_last_versions(bg_dir: Path) -> dict[str, Any]:
    conf = bg_dir / "last_versions.conf"
    backup = bg_dir / f"last_versions.conf.backup_orientation_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    parser = configparser.ConfigParser()
    parser.optionxform = str
    existed = conf.exists()
    if existed:
        shutil.copy2(conf, backup)
        parser.read(conf, encoding="utf-8")

    if not parser.has_section("atlases"):
        parser.add_section("atlases")
    before = parser.get("atlases", TEST_ATLAS_NAME) if parser.has_option("atlases", TEST_ATLAS_NAME) else None
    parser.set("atlases", TEST_ATLAS_NAME, TEST_VERSION)

    with conf.open("w", encoding="utf-8") as f:
        parser.write(f)

    return {
        "conf_path": str(conf),
        "backup_path": str(backup) if existed else None,
        "existed_before": existed,
        "entry_before": before,
        "entry_after": TEST_VERSION,
        "patched": True,
    }


def normalize_slice(s: np.ndarray) -> np.ndarray:
    arr = np.asarray(s, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros_like(arr)
    lo, hi = np.percentile(finite, [1, 99.5])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros_like(arr)
    return np.clip((arr - lo) / (hi - lo), 0, 1)


def make_edge(label_slice: np.ndarray) -> np.ndarray:
    a = np.asarray(label_slice)
    e = np.zeros(a.shape, dtype=bool)
    e[:-1, :] |= a[:-1, :] != a[1:, :]
    e[1:, :] |= a[:-1, :] != a[1:, :]
    e[:, :-1] |= a[:, :-1] != a[:, 1:]
    e[:, 1:] |= a[:, :-1] != a[:, 1:]
    e &= a > 0
    return e


def make_overlay(ref_slice: np.ndarray, ann_slice: np.ndarray) -> np.ndarray:
    g = normalize_slice(ref_slice)
    rgb = np.dstack([g, g, g])
    rgb[make_edge(ann_slice)] = [1.0, 0.0, 0.0]
    return rgb


def write_preview(report_dir: Path, src: Path, dst: Path) -> dict[str, Any]:
    if plt is None:
        return {"skipped": True, "reason": "matplotlib is not installed"}

    old_ref = tifffile.imread(str(src / "reference.tiff"))
    old_ann = tifffile.imread(str(src / "annotation.tiff"))
    new_ref = tifffile.imread(str(dst / "reference.tiff"))
    new_ann = tifffile.imread(str(dst / "annotation.tiff"))

    x = old_ref.shape[0] // 2
    x2 = new_ref.shape[0] // 2

    fig, axes = plt.subplots(2, 2, figsize=(14, 7))
    axes[0, 0].imshow(old_ann[x, :, :] > 0, cmap="gray")
    axes[0, 0].set_title("Source sagittal\nrows=P, cols=I\nAP vertical")

    axes[0, 1].imshow(make_overlay(old_ref[x, :, :], old_ann[x, :, :]))
    axes[0, 1].set_title("Source reference + annotation edges")

    axes[1, 0].imshow(new_ann[x2, :, :] > 0, cmap="gray")
    axes[1, 0].set_title("Test sagittal\nrows=I, cols=P\nAP left-right")

    axes[1, 1].imshow(make_overlay(new_ref[x2, :, :], new_ann[x2, :, :]))
    axes[1, 1].set_title("Test reference + annotation edges")

    for ax in axes.ravel():
        ax.axis("off")
    fig.tight_layout()
    png = report_dir / "orientation_display_test_preview.png"
    fig.savefig(png, dpi=160)
    plt.close(fig)
    return {"preview_png": str(png), "skipped": False}


def create_test_atlas(project_root: Path, install: bool) -> dict[str, Any]:
    bg_dir = default_brainglobe_dir()
    src = bg_dir / SOURCE_FOLDER_NAME
    if not src.exists():
        raise FileNotFoundError(f"Missing installed source atlas: {src}")

    report_dir = project_root / "reports" / "orientation_display_test"
    report_dir.mkdir(parents=True, exist_ok=True)

    sandbox = report_dir / TEST_FOLDER_NAME
    if sandbox.exists():
        shutil.rmtree(sandbox)
    shutil.copytree(src, sandbox)

    tiff_report = transform_tiffs(sandbox)
    nifti_report = transform_niftis(sandbox)
    meta_report = patch_metadata(sandbox, src)
    manifest_report = patch_candidate_manifest(sandbox, src)
    preview_report = write_preview(report_dir, src, sandbox)

    install_report: dict[str, Any] = {"requested": install, "installed": False}
    if install:
        target = bg_dir / TEST_FOLDER_NAME
        if target.exists():
            backup = bg_dir / f"{TEST_FOLDER_NAME}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(target), str(backup))
            install_report["previous_backup"] = str(backup)
        shutil.copytree(sandbox, target)
        lv = patch_last_versions(bg_dir)
        install_report.update({
            "installed": True,
            "target": str(target),
            "last_versions": lv,
        })

    result = {
        "generated_at": now(),
        "source_atlas": str(src),
        "test_atlas_name": TEST_ATLAS_NAME,
        "test_folder_name": TEST_FOLDER_NAME,
        "sandbox_folder": str(sandbox),
        "installed": install_report,
        "orientation_transform": {
            "perm": list(PERM),
            "flips": list(FLIPS),
            "old_orientation": OLD_ORIENTATION,
            "new_orientation": NEW_ORIENTATION,
            "purpose": "Sagittal AP left-right display test.",
        },
        "tiffs": tiff_report,
        "niftis": nifti_report,
        "metadata": meta_report,
        "manifest": manifest_report,
        "preview": preview_report,
        "passed": (
            tiff_report.get("reference.tiff", {}).get("new_shape") == [409, 286, 608]
            and tiff_report.get("annotation.tiff", {}).get("new_shape") == [409, 286, 608]
            and install_report.get("installed") == install
        ),
    }

    json_path = report_dir / "orientation_display_test_report.json"
    txt_path = report_dir / "orientation_display_test_report.txt"
    save_json(json_path, result)

    lines = [
        "Orientation display test atlas report",
        "=" * 72,
        f"Generated: {result['generated_at']}",
        f"Source atlas: {src}",
        f"Test atlas name: {TEST_ATLAS_NAME}",
        f"Transform: perm={PERM}, flips={FLIPS}",
        f"Orientation: {OLD_ORIENTATION} -> {NEW_ORIENTATION}",
        f"Sandbox folder: {sandbox}",
        f"Installed: {install_report.get('installed')}",
        f"Passed: {result['passed']}",
        "",
        "TIFF files:",
    ]
    for k, v in tiff_report.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "NIfTI files:"]
    if isinstance(nifti_report, dict):
        for k, v in nifti_report.items():
            lines.append(f"- {k}: {v}")
    lines += ["", "Metadata:", str(meta_report), "", "Install:", str(install_report), "", "Preview:", str(preview_report)]
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--install", action="store_true", help="Install the test atlas into the BrainGlobe cache under a new name.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[1]

    try:
        result = create_test_atlas(project_root, args.install)
        print("Orientation display test created.")
        print("Test atlas:", TEST_ATLAS_NAME)
        print("Installed:", result["installed"].get("installed"))
        print("Report folder:", project_root / "reports" / "orientation_display_test")
        print("Passed:", result["passed"])
        return 0 if result["passed"] else 1
    except Exception as exc:
        report_dir = project_root / "reports" / "orientation_display_test"
        report_dir.mkdir(parents=True, exist_ok=True)
        err = {
            "generated_at": now(),
            "error": repr(exc),
            "traceback": traceback.format_exc(),
            "passed": False,
        }
        save_json(report_dir / "orientation_display_test_error.json", err)
        (report_dir / "orientation_display_test_error.txt").write_text(err["traceback"], encoding="utf-8")
        print("ERROR:", repr(exc))
        print(err["traceback"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
