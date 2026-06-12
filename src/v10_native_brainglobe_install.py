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


def validate_candidate(folder: Path) -> dict[str, Any]:
    required = ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json"]
    out: dict[str, Any] = {
        "folder": str(folder),
        "exists": folder.exists(),
        "required_files": {name: (folder / name).exists() for name in required},
        "passed": False,
        "errors": [],
        "nifti": {},
        "structures": {},
        "metadata": {},
    }

    if not folder.exists():
        out["errors"].append("candidate_folder_missing")
        return out
    missing = [k for k, v in out["required_files"].items() if not v]
    if missing:
        out["errors"].append(f"missing_files:{missing}")
        return out

    try:
        ref = nib.load(str(folder / "reference.nii.gz"))
        ann = nib.load(str(folder / "annotation.nii.gz"))
        ann_data = np.asanyarray(ann.dataobj)
        structures = json.loads((folder / "structures.json").read_text(encoding="utf-8"))
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

        used_ids = set(np.unique(ann_data).astype(int).tolist())
        structure_ids = set(int(s["id"]) for s in structures)
        ids_without_structure = sorted(x for x in used_ids if x != 0 and x not in structure_ids)
        structures_without_voxels = sorted(x for x in structure_ids if x not in used_ids and x != ROOT_ID)

        out["nifti"] = {
            "reference_shape": list(ref.shape),
            "annotation_shape": list(ann.shape),
            "same_shape": tuple(ref.shape) == tuple(ann.shape),
            "same_affine": bool(np.allclose(ref.affine, ann.affine, atol=1e-6)),
            "annotation_unique_count": int(np.unique(ann_data).size),
            "annotation_integer_like": bool(np.all(np.isclose(np.unique(ann_data), np.round(np.unique(ann_data))))),
        }
        out["structures"] = {
            "count": len(structures),
            "ids_without_structure_count": len(ids_without_structure),
            "ids_without_structure_sample": ids_without_structure[:50],
            "structures_without_voxels_count": len(structures_without_voxels),
            "structures_without_voxels_sample": structures_without_voxels[:50],
        }
        out["metadata"] = {
            "name": metadata.get("name"),
            "species": metadata.get("species"),
            "resolution": metadata.get("resolution"),
            "orientation": metadata.get("orientation"),
        }

        if not out["nifti"]["same_shape"]:
            out["errors"].append("shape_mismatch")
        if not out["nifti"]["same_affine"]:
            out["errors"].append("affine_mismatch")
        if not out["nifti"]["annotation_integer_like"]:
            out["errors"].append("annotation_not_integer_like")
        if ids_without_structure:
            out["errors"].append("ids_without_structure")
    except Exception as exc:
        out["errors"].append(repr(exc))

    out["passed"] = len(out["errors"]) == 0
    return out


def install_versioned_folder(candidate: Path, bg_dir: Path) -> dict[str, Any]:
    bg_dir.mkdir(parents=True, exist_ok=True)
    target = bg_dir / VERSIONED_FULL_NAME

    backup_target = None
    if target.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_target = bg_dir / f"{VERSIONED_FULL_NAME}_backup_{timestamp}"
        shutil.move(str(target), str(backup_target))

    shutil.copytree(candidate, target)

    # BrainGlobe installed atlases convention: README.txt, reference.tiff/annotation.tiff often exist,
    # but NIfTI loading may still be handled by core if metadata points correctly. We preserve originals
    # and add a marker manifest.
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "version": ATLAS_VERSION,
        "versioned_full_name": VERSIONED_FULL_NAME,
        "target": str(target),
        "backup_previous_target": str(backup_target) if backup_target else None,
        "source_candidate": str(candidate),
        "note": "Native BrainGlobe cache install attempt created by rat-paxinos-brainglobe-builder.",
    }
    (target / "native_install_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"target": str(target), "backup_previous_target": str(backup_target) if backup_target else None, "manifest": manifest}


def patch_last_versions(bg_dir: Path) -> dict[str, Any]:
    conf_path = bg_dir / "last_versions.conf"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = bg_dir / f"last_versions.conf.backup_{timestamp}"

    result: dict[str, Any] = {
        "conf_path": str(conf_path),
        "backup_path": str(backup_path),
        "existed_before": conf_path.exists(),
        "entry_before": None,
        "entry_after": ATLAS_VERSION,
        "patched": False,
    }

    parser = configparser.ConfigParser()
    if conf_path.exists():
        shutil.copy2(conf_path, backup_path)
        parser.read(conf_path, encoding="utf-8")
    if not parser.has_section("atlases"):
        parser.add_section("atlases")

    if parser.has_option("atlases", ATLAS_NAME):
        result["entry_before"] = parser.get("atlases", ATLAS_NAME)

    parser.set("atlases", ATLAS_NAME, ATLAS_VERSION)
    with conf_path.open("w", encoding="utf-8") as f:
        parser.write(f)
    result["patched"] = True
    return result


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
            "has_reference": hasattr(atlas, "reference"),
            "has_annotation": hasattr(atlas, "annotation"),
            "has_structures": hasattr(atlas, "structures"),
        }
        for attr in ["atlas_name", "name", "root_dir", "atlas_dir", "local_full_name", "local_version", "resolution", "orientation"]:
            try:
                result["object_summary"][attr] = str(getattr(atlas, attr))
            except Exception as exc:
                result["object_summary"][attr] = f"<<ERROR {exc!r}>>"
    except Exception as exc:
        result["exception"] = repr(exc)
        result["traceback"] = traceback.format_exc()
    return result


def write_reports(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v10_native_install_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V10 native BrainGlobe install report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Requested: {report['requested']}",
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
    lines.append("")
    lines.append("Native install:")
    for k, v in report.get("native_install", {}).items():
        if k != "manifest":
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("last_versions.conf patch:")
    for k, v in report.get("last_versions_patch", {}).items():
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
    (REPORTS_DIR / "v10_native_install_report.txt").write_text("\n".join(lines), encoding="utf-8")

    final = [
        "V10 FINAL STATUS",
        "=" * 72,
        f"PASSED: {report['passed']}",
        f"Requested: {report['requested']}",
        f"Candidate validation passed: {report['candidate_validation'].get('passed')}",
        f"Native install target: {report.get('native_install', {}).get('target')}",
        f"last_versions patched: {report.get('last_versions_patch', {}).get('patched')}",
        f"Real BrainGlobe load success: {report.get('real_load', {}).get('success')}",
        "",
        "Result:",
    ]
    if report["passed"]:
        final.append("- BrainGlobeAtlas accepted the native installed Paxinos candidate.")
        final.append("- Next step: ABBA visibility/loading test.")
    elif not report["requested"]:
        final.append("- Native install was not requested. Use menu option 8 or --native-install.")
    else:
        final.append("- Native installation was attempted but the real load did not pass yet.")
        final.append("- The candidate folder and last_versions registry were still created/patched with backups.")
        final.append(f"- Exception: {report.get('real_load', {}).get('exception')}")
    (REPORTS_DIR / "v10_final_status.txt").write_text("\n".join(final), encoding="utf-8")

    (REPORTS_DIR / "v10_real_load_report.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-install", action="store_true", help="Install candidate into BrainGlobe cache with version suffix and patch last_versions.conf.")
    args = parser.parse_args()

    candidate = official_candidate_folder()
    bg_dir = default_brainglobe_dir()
    validation = validate_candidate(candidate)

    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "requested": args.native_install,
        "atlas_name": ATLAS_NAME,
        "atlas_version": ATLAS_VERSION,
        "versioned_full_name": VERSIONED_FULL_NAME,
        "candidate_folder": str(candidate),
        "brainglobe_dir": str(bg_dir),
        "candidate_validation": validation,
        "native_install": {},
        "last_versions_patch": {},
        "real_load": {"attempted": False, "success": False, "exception": "Native install not requested."},
        "passed": False,
    }

    if args.native_install:
        if not validation.get("passed"):
            report["real_load"] = {"attempted": False, "success": False, "exception": "Candidate validation failed."}
        else:
            report["native_install"] = install_versioned_folder(candidate, bg_dir)
            report["last_versions_patch"] = patch_last_versions(bg_dir)
            report["real_load"] = try_load_with_brain_globe(bg_dir)

    report["passed"] = bool(validation.get("passed") and report.get("real_load", {}).get("success"))

    write_reports(report)

    table = Table(title="V10 native BrainGlobe install")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Requested", str(args.native_install))
    table.add_row("Candidate valid", str(validation.get("passed")))
    table.add_row("Versioned folder", VERSIONED_FULL_NAME)
    table.add_row("last_versions patched", str(report.get("last_versions_patch", {}).get("patched")))
    table.add_row("Real load success", str(report.get("real_load", {}).get("success")))
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Exception", str(report.get("real_load", {}).get("exception"))[:100])
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v10_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v10_native_install_report.txt'}")

    # If requested and failed, return non-zero so user sees it.
    if args.native_install and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
