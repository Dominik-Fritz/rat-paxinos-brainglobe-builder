from __future__ import annotations

import argparse
import configparser
import json
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import tifffile
from rich.console import Console
from rich.table import Table

from utils_paths import ATLAS_NAME, REPORTS_DIR, official_candidate_folder

console = Console()

ATLAS_VERSION = "1.0"
VERSIONED_FULL_NAME = f"{ATLAS_NAME}_v{ATLAS_VERSION}"
ROOT_ID = 997000


def default_brainglobe_dir() -> Path:
    try:
        from brainglobe_atlasapi import config
        return Path(config.get_brainglobe_dir())
    except Exception:
        return Path.home() / ".brainglobe"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_metadata_and_tiffs(folder: Path) -> dict[str, Any]:
    metadata_path = folder / "metadata.json"
    annotation_path = folder / "annotation.nii.gz"
    reference_path = folder / "reference.nii.gz"
    reference_tiff = folder / "reference.tiff"
    annotation_tiff = folder / "annotation.tiff"

    metadata = load_json(metadata_path)
    ann_img = nib.load(str(annotation_path))
    ref_img = nib.load(str(reference_path))
    shape = [int(x) for x in ann_img.shape]

    metadata["shape"] = shape
    metadata["annotation_shape"] = shape
    metadata["reference_shape"] = [int(x) for x in ref_img.shape]
    metadata["resolution"] = metadata.get("resolution", [40, 40, 40])
    metadata["orientation"] = metadata.get("orientation", "LPI")
    metadata["species"] = metadata.get("species", "Rattus norvegicus")
    metadata["symmetric"] = metadata.get("symmetric", False)
    metadata["root_id"] = metadata.get("root_id", ROOT_ID)
    metadata["version"] = ATLAS_VERSION
    metadata["atlas_name"] = ATLAS_NAME
    metadata["reference_atlas_preference"] = "waxholm_rat"
    metadata["reference_file"] = "reference.tiff"
    metadata["annotation_file"] = "annotation.tiff"
    metadata["files"] = metadata.get("files", {})
    metadata["files"]["reference_tiff"] = "reference.tiff"
    metadata["files"]["annotation_tiff"] = "annotation.tiff"
    metadata["files"]["reference_nifti"] = "reference.nii.gz"
    metadata["files"]["annotation_nifti"] = "annotation.nii.gz"

    save_json(metadata_path, metadata)

    if not reference_tiff.exists():
        ref = np.asarray(np.asanyarray(ref_img.dataobj))
        if ref.dtype != np.uint16:
            ref = ref.astype(np.float32)
            finite = ref[np.isfinite(ref)]
            if finite.size and float(np.max(finite)) > float(np.min(finite)):
                ref = (np.clip((ref - float(np.min(finite))) / (float(np.max(finite)) - float(np.min(finite))), 0, 1) * 65535).astype(np.uint16)
            else:
                ref = np.zeros(ref.shape, dtype=np.uint16)
        tifffile.imwrite(str(reference_tiff), ref, photometric="minisblack")

    if not annotation_tiff.exists():
        ann = np.asarray(np.asanyarray(ann_img.dataobj))
        if np.max(ann) <= np.iinfo(np.uint16).max:
            ann = np.round(ann).astype(np.uint16)
        else:
            ann = np.round(ann).astype(np.uint32)
        tifffile.imwrite(str(annotation_tiff), ann, photometric="minisblack")

    return {
        "metadata_path": str(metadata_path),
        "shape": shape,
        "has_shape": True,
        "reference_tiff_exists": reference_tiff.exists(),
        "annotation_tiff_exists": annotation_tiff.exists(),
    }


def validate_candidate(folder: Path) -> dict[str, Any]:
    required = ["reference.nii.gz", "annotation.nii.gz", "reference.tiff", "annotation.tiff", "structures.json", "metadata.json"]
    out: dict[str, Any] = {
        "folder": str(folder),
        "exists": folder.exists(),
        "required_files": {name: (folder / name).exists() for name in required},
        "passed": False,
        "errors": [],
        "nifti": {},
        "tiff": {},
        "structures": {},
        "metadata": {},
    }

    if not folder.exists():
        out["errors"].append("candidate_folder_missing")
        return out

    try:
        meta_fix = ensure_metadata_and_tiffs(folder)
    except Exception as exc:
        out["errors"].append(f"metadata_tiff_fix_failed:{exc!r}")
        return out

    out["required_files"] = {name: (folder / name).exists() for name in required}
    missing = [k for k, v in out["required_files"].items() if not v]
    if missing:
        out["errors"].append(f"missing_files:{missing}")
        return out

    try:
        ref = nib.load(str(folder / "reference.nii.gz"))
        ann = nib.load(str(folder / "annotation.nii.gz"))
        ann_data = np.asanyarray(ann.dataobj)
        ref_tiff = tifffile.imread(str(folder / "reference.tiff"))
        ann_tiff = tifffile.imread(str(folder / "annotation.tiff"))
        structures = load_json(folder / "structures.json")
        metadata = load_json(folder / "metadata.json")

        used_ids = set(np.unique(ann_data).astype(int).tolist())
        structure_ids = set(int(s["id"]) for s in structures)
        ids_without_structure = sorted(x for x in used_ids if x != 0 and x not in structure_ids)
        root_entries = [s for s in structures if s.get("acronym") == "root"]

        out["nifti"] = {
            "reference_shape": list(ref.shape),
            "annotation_shape": list(ann.shape),
            "same_shape": tuple(ref.shape) == tuple(ann.shape),
            "same_affine": bool(np.allclose(ref.affine, ann.affine, atol=1e-6)),
            "annotation_unique_count": int(np.unique(ann_data).size),
            "annotation_integer_like": bool(np.all(np.isclose(np.unique(ann_data), np.round(np.unique(ann_data))))),
        }
        out["tiff"] = {
            "reference_shape": list(ref_tiff.shape),
            "annotation_shape": list(ann_tiff.shape),
            "same_shape": tuple(ref_tiff.shape) == tuple(ann_tiff.shape),
            "reference_dtype": str(ref_tiff.dtype),
            "annotation_dtype": str(ann_tiff.dtype),
        }
        out["structures"] = {
            "count": len(structures),
            "ids_without_structure_count": len(ids_without_structure),
            "root_entry_count": len(root_entries),
            "has_root": len(root_entries) == 1,
        }
        out["metadata"] = {
            "name": metadata.get("name"),
            "species": metadata.get("species"),
            "resolution": metadata.get("resolution"),
            "orientation": metadata.get("orientation"),
            "shape": metadata.get("shape"),
            "has_shape": "shape" in metadata,
            "meta_fix": meta_fix,
        }

        if not out["nifti"]["same_shape"]:
            out["errors"].append("nifti_shape_mismatch")
        if not out["tiff"]["same_shape"]:
            out["errors"].append("tiff_shape_mismatch")
        if out["tiff"]["reference_shape"] != out["nifti"]["reference_shape"]:
            out["errors"].append("reference_tiff_nifti_shape_mismatch")
        if out["tiff"]["annotation_shape"] != out["nifti"]["annotation_shape"]:
            out["errors"].append("annotation_tiff_nifti_shape_mismatch")
        if not out["nifti"]["same_affine"]:
            out["errors"].append("affine_mismatch")
        if not out["nifti"]["annotation_integer_like"]:
            out["errors"].append("annotation_not_integer_like")
        if ids_without_structure:
            out["errors"].append("ids_without_structure")
        if len(root_entries) != 1:
            out["errors"].append("root_entry_count_not_one")
        if metadata.get("shape") != list(ann.shape):
            out["errors"].append("metadata_shape_mismatch")
    except Exception as exc:
        out["errors"].append(repr(exc))

    out["passed"] = len(out["errors"]) == 0
    return out


def backup_existing_paxinos_cache(bg_dir: Path, clean_install: bool) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = bg_dir / f"_paxinos_cleanup_backup_{timestamp}"
    result = {
        "clean_install_requested": clean_install,
        "bg_dir": str(bg_dir),
        "backup_root": str(backup_root),
        "matched_items": [],
        "moved_items": [],
        "skipped": False,
    }

    bg_dir.mkdir(parents=True, exist_ok=True)
    matches = sorted([p for p in bg_dir.iterdir() if p.name.startswith(ATLAS_NAME)], key=lambda p: p.name)
    result["matched_items"] = [str(p) for p in matches]

    if not clean_install:
        result["skipped"] = True
        return result

    if matches:
        backup_root.mkdir(parents=True, exist_ok=True)

    for p in matches:
        target = backup_root / p.name
        shutil.move(str(p), str(target))
        result["moved_items"].append({"from": str(p), "to": str(target)})

    return result


def patch_last_versions(bg_dir: Path, clean_install: bool) -> dict[str, Any]:
    conf_path = bg_dir / "last_versions.conf"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = bg_dir / f"last_versions.conf.backup_v14_{timestamp}"

    result: dict[str, Any] = {
        "conf_path": str(conf_path),
        "backup_path": str(backup_path),
        "existed_before": conf_path.exists(),
        "clean_install_requested": clean_install,
        "entry_before": None,
        "entry_after": ATLAS_VERSION,
        "patched": False,
        "sections": [],
    }

    parser = configparser.ConfigParser()
    parser.optionxform = str

    if conf_path.exists():
        shutil.copy2(conf_path, backup_path)
        parser.read(conf_path, encoding="utf-8")

    if not parser.has_section("atlases"):
        parser.add_section("atlases")

    if parser.has_option("atlases", ATLAS_NAME):
        result["entry_before"] = parser.get("atlases", ATLAS_NAME)
        if clean_install:
            parser.remove_option("atlases", ATLAS_NAME)

    parser.set("atlases", ATLAS_NAME, ATLAS_VERSION)
    result["sections"] = parser.sections()

    with conf_path.open("w", encoding="utf-8") as f:
        parser.write(f)

    result["patched"] = True
    return result


def install_versioned_folder(candidate: Path, bg_dir: Path) -> dict[str, Any]:
    target = bg_dir / VERSIONED_FULL_NAME
    if target.exists():
        raise FileExistsError(f"Target still exists after cleanup: {target}")

    shutil.copytree(candidate, target)
    ensure_metadata_and_tiffs(target)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "version": ATLAS_VERSION,
        "versioned_full_name": VERSIONED_FULL_NAME,
        "target": str(target),
        "source_candidate": str(candidate),
        "note": "Clean native BrainGlobe cache install created by rat-paxinos-brainglobe-builder V14.",
    }
    (target / "native_install_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"target": str(target), "manifest": manifest}


def post_install_scan(bg_dir: Path) -> dict[str, Any]:
    matches = sorted([p for p in bg_dir.iterdir() if p.name.startswith(ATLAS_NAME)], key=lambda p: p.name)
    target = bg_dir / VERSIONED_FULL_NAME
    return {
        "matches": [str(p) for p in matches],
        "match_count": len(matches),
        "expected_target": str(target),
        "only_expected_target": len(matches) == 1 and matches[0].name == VERSIONED_FULL_NAME,
        "reference_tiff_exists": (target / "reference.tiff").exists(),
        "annotation_tiff_exists": (target / "annotation.tiff").exists(),
    }


def safe_attr(atlas: Any, attr: str) -> Any:
    try:
        value = getattr(atlas, attr)
        if hasattr(value, "shape"):
            return {"type": type(value).__name__, "shape": list(value.shape), "dtype": str(getattr(value, "dtype", ""))}
        return str(value)
    except Exception as exc:
        return f"<<ERROR {exc!r}>>"


def try_load_with_brain_globe(bg_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "attempted": True,
        "success": False,
        "atlas_name": ATLAS_NAME,
        "brainglobe_dir": str(bg_dir),
        "exception": None,
        "traceback": None,
        "object_summary": {},
    }

    try:
        from brainglobe_atlasapi import BrainGlobeAtlas
        atlas = BrainGlobeAtlas(
            ATLAS_NAME,
            brainglobe_dir=bg_dir,
            check_latest=False,
        )
        result["success"] = True
        result["object_summary"] = {
            "class": atlas.__class__.__name__,
            "repr": repr(atlas),
        }
        for attr in ["atlas_name", "name", "root_dir", "atlas_dir", "local_full_name", "local_version", "resolution", "orientation", "shape", "metadata"]:
            result["object_summary"][attr] = safe_attr(atlas, attr)

        # Now deliberately test actual data access.
        result["object_summary"]["reference"] = safe_attr(atlas, "reference")
        result["object_summary"]["annotation"] = safe_attr(atlas, "annotation")
        result["object_summary"]["structures"] = safe_attr(atlas, "structures")
    except Exception as exc:
        result["success"] = False
        result["exception"] = repr(exc)
        result["traceback"] = traceback.format_exc()
    return result


def write_reports(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v14_native_install_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    cleanup = report.get("cleanup", {})
    cleanup_lines = [
        "V14 BrainGlobe cache cleanup report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Clean install requested: {cleanup.get('clean_install_requested')}",
        f"BrainGlobe dir: {cleanup.get('bg_dir')}",
        f"Backup root: {cleanup.get('backup_root')}",
        "",
        "Matched items:",
    ]
    cleanup_lines += [f"- {x}" for x in cleanup.get("matched_items", [])] or ["- none"]
    cleanup_lines.append("")
    cleanup_lines.append("Moved items:")
    cleanup_lines += [f"- {x['from']} -> {x['to']}" for x in cleanup.get("moved_items", [])] or ["- none"]
    cleanup_lines.append("")
    cleanup_lines.append("Post-install scan:")
    scan = report.get("post_install_scan", {})
    for k, v in scan.items():
        cleanup_lines.append(f"- {k}: {v}")
    (REPORTS_DIR / "v14_cache_cleanup_report.txt").write_text("\n".join(cleanup_lines), encoding="utf-8")

    lines = [
        "V14 clean native BrainGlobe install report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Requested: {report['requested']}",
        f"Clean install requested: {report['clean_install_requested']}",
        f"Atlas: {ATLAS_NAME}",
        f"Version: {ATLAS_VERSION}",
        f"BrainGlobe dir: {report['brainglobe_dir']}",
        f"PASSED: {report['passed']}",
        "",
        "Candidate validation:",
    ]
    cv = report["candidate_validation"]
    for k in ["folder", "exists", "passed", "errors"]:
        lines.append(f"- {k}: {cv.get(k)}")
    lines.append(f"- metadata.shape: {cv.get('metadata', {}).get('shape')}")
    lines.append(f"- reference.tiff shape: {cv.get('tiff', {}).get('reference_shape')}")
    lines.append(f"- annotation.tiff shape: {cv.get('tiff', {}).get('annotation_shape')}")
    lines.append("")
    lines.append("Cleanup:")
    lines.append(f"- matched before: {len(cleanup.get('matched_items', []))}")
    lines.append(f"- moved: {len(cleanup.get('moved_items', []))}")
    lines.append(f"- backup_root: {cleanup.get('backup_root')}")
    lines.append("")
    lines.append("last_versions.conf patch:")
    for k, v in report.get("last_versions_patch", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Native install:")
    for k, v in report.get("native_install", {}).items():
        if k != "manifest":
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Post-install scan:")
    for k, v in scan.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Real BrainGlobe load:")
    load = report.get("real_load", {})
    for k, v in load.items():
        if k not in ("traceback", "object_summary"):
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Object summary:")
    for k, v in load.get("object_summary", {}).items():
        lines.append(f"- {k}: {v}")
    if load.get("traceback"):
        lines.append("")
        lines.append("Traceback:")
        lines.append(load["traceback"])
    text = "\n".join(lines)
    (REPORTS_DIR / "v14_native_install_report.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v14_real_load_report.txt").write_text(text, encoding="utf-8")

    final = [
        "V14 FINAL STATUS",
        "=" * 72,
        f"PASSED: {report['passed']}",
        f"Requested: {report['requested']}",
        f"Clean install requested: {report['clean_install_requested']}",
        f"Candidate validation passed: {report['candidate_validation'].get('passed')}",
        f"Metadata shape: {report['candidate_validation'].get('metadata', {}).get('shape')}",
        f"reference.tiff exists after install: {report.get('post_install_scan', {}).get('reference_tiff_exists')}",
        f"annotation.tiff exists after install: {report.get('post_install_scan', {}).get('annotation_tiff_exists')}",
        f"Only expected cache target after install: {report.get('post_install_scan', {}).get('only_expected_target')}",
        f"last_versions patched: {report.get('last_versions_patch', {}).get('patched')}",
        f"Real BrainGlobe load success: {report.get('real_load', {}).get('success')}",
        "",
        "Result:",
    ]
    if report["passed"]:
        final.append("- BrainGlobeAtlas loaded the clean installed Paxinos candidate and accessed TIFF-backed data.")
        final.append("- Next step: ABBA visibility/loading test.")
    elif not report["requested"]:
        final.append("- Native install was not requested. Use menu option 8 or --native-install --clean-install.")
    else:
        final.append("- Clean native installation was attempted but real load did not pass yet.")
        final.append("- Exception points to the next missing BrainGlobe expectation.")
        final.append(f"- Exception: {report.get('real_load', {}).get('exception')}")
    (REPORTS_DIR / "v14_final_status.txt").write_text("\n".join(final), encoding="utf-8")

    # Compatibility filenames
    (REPORTS_DIR / "v13_final_status.txt").write_text("\n".join(final), encoding="utf-8")
    (REPORTS_DIR / "v13_native_install_report.txt").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-install", action="store_true")
    parser.add_argument("--clean-install", action="store_true")
    args = parser.parse_args()

    candidate = official_candidate_folder()
    bg_dir = default_brainglobe_dir()
    validation = validate_candidate(candidate)

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested": args.native_install,
        "clean_install_requested": args.clean_install,
        "atlas_name": ATLAS_NAME,
        "atlas_version": ATLAS_VERSION,
        "versioned_full_name": VERSIONED_FULL_NAME,
        "candidate_folder": str(candidate),
        "brainglobe_dir": str(bg_dir),
        "candidate_validation": validation,
        "cleanup": {},
        "last_versions_patch": {},
        "native_install": {},
        "post_install_scan": {},
        "real_load": {"attempted": False, "success": False, "exception": "Native install not requested."},
        "passed": False,
    }

    if args.native_install:
        if not validation.get("passed"):
            report["real_load"] = {"attempted": False, "success": False, "exception": "Candidate validation failed."}
        else:
            report["cleanup"] = backup_existing_paxinos_cache(bg_dir, args.clean_install)
            report["last_versions_patch"] = patch_last_versions(bg_dir, args.clean_install)
            report["native_install"] = install_versioned_folder(candidate, bg_dir)
            report["post_install_scan"] = post_install_scan(bg_dir)
            report["real_load"] = try_load_with_brain_globe(bg_dir)

    report["passed"] = bool(validation.get("passed") and report.get("real_load", {}).get("success"))
    write_reports(report)

    table = Table(title="V14 clean native BrainGlobe install")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Requested", str(args.native_install))
    table.add_row("Clean install", str(args.clean_install))
    table.add_row("Candidate valid", str(validation.get("passed")))
    table.add_row("Metadata shape", str(validation.get("metadata", {}).get("shape")))
    table.add_row("reference.tiff", str(report.get("post_install_scan", {}).get("reference_tiff_exists")))
    table.add_row("annotation.tiff", str(report.get("post_install_scan", {}).get("annotation_tiff_exists")))
    table.add_row("Real load success", str(report.get("real_load", {}).get("success")))
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Exception", str(report.get("real_load", {}).get("exception"))[:100])
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v14_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v14_native_install_report.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v14_cache_cleanup_report.txt'}")

    if args.native_install and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
