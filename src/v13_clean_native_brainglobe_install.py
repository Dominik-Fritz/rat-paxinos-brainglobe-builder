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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_metadata_shape(folder: Path) -> dict[str, Any]:
    metadata_path = folder / "metadata.json"
    annotation_path = folder / "annotation.nii.gz"
    reference_path = folder / "reference.nii.gz"

    metadata = load_json(metadata_path)
    ann = nib.load(str(annotation_path))
    ref = nib.load(str(reference_path))
    shape = [int(x) for x in ann.shape]

    metadata["shape"] = shape
    metadata["annotation_shape"] = shape
    metadata["reference_shape"] = [int(x) for x in ref.shape]
    metadata["resolution"] = metadata.get("resolution", [40, 40, 40])
    metadata["orientation"] = metadata.get("orientation", "LPI")
    metadata["species"] = metadata.get("species", "Rattus norvegicus")
    metadata["symmetric"] = metadata.get("symmetric", False)
    metadata["root_id"] = metadata.get("root_id", ROOT_ID)
    metadata["version"] = ATLAS_VERSION
    metadata["atlas_name"] = ATLAS_NAME
    metadata["reference_atlas_preference"] = "waxholm_rat"

    save_json(metadata_path, metadata)
    return {"metadata_path": str(metadata_path), "shape": shape, "has_shape": True}


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
        meta_shape = ensure_metadata_shape(folder)
        ref = nib.load(str(folder / "reference.nii.gz"))
        ann = nib.load(str(folder / "annotation.nii.gz"))
        ann_data = np.asanyarray(ann.dataobj)
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
            "meta_shape_fix": meta_shape,
        }

        if not out["nifti"]["same_shape"]:
            out["errors"].append("shape_mismatch")
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
    backup_path = bg_dir / f"last_versions.conf.backup_v13_{timestamp}"

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
    ensure_metadata_shape(target)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "version": ATLAS_VERSION,
        "versioned_full_name": VERSIONED_FULL_NAME,
        "target": str(target),
        "source_candidate": str(candidate),
        "note": "Clean native BrainGlobe cache install created by rat-paxinos-brainglobe-builder V13.",
    }
    (target / "native_install_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"target": str(target), "manifest": manifest}


def post_install_scan(bg_dir: Path) -> dict[str, Any]:
    matches = sorted([p for p in bg_dir.iterdir() if p.name.startswith(ATLAS_NAME)], key=lambda p: p.name)
    return {
        "matches": [str(p) for p in matches],
        "match_count": len(matches),
        "expected_target": str(bg_dir / VERSIONED_FULL_NAME),
        "only_expected_target": len(matches) == 1 and matches[0].name == VERSIONED_FULL_NAME,
    }


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
        for attr in ["atlas_name", "name", "root_dir", "atlas_dir", "local_full_name", "local_version", "resolution", "orientation", "shape"]:
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
    (REPORTS_DIR / "v13_native_install_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    cleanup = report.get("cleanup", {})
    cleanup_lines = [
        "V13 BrainGlobe cache cleanup report",
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
    cleanup_lines.append(f"- match_count: {scan.get('match_count')}")
    cleanup_lines.append(f"- only_expected_target: {scan.get('only_expected_target')}")
    for x in scan.get("matches", []):
        cleanup_lines.append(f"- {x}")
    (REPORTS_DIR / "v13_cache_cleanup_report.txt").write_text("\n".join(cleanup_lines), encoding="utf-8")

    lines = [
        "V13 clean native BrainGlobe install report",
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
    (REPORTS_DIR / "v13_native_install_report.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v13_real_load_report.txt").write_text(text, encoding="utf-8")

    final = [
        "V13 FINAL STATUS",
        "=" * 72,
        f"PASSED: {report['passed']}",
        f"Requested: {report['requested']}",
        f"Clean install requested: {report['clean_install_requested']}",
        f"Candidate validation passed: {report['candidate_validation'].get('passed')}",
        f"Metadata shape: {report['candidate_validation'].get('metadata', {}).get('shape')}",
        f"Only expected cache target after install: {report.get('post_install_scan', {}).get('only_expected_target')}",
        f"last_versions patched: {report.get('last_versions_patch', {}).get('patched')}",
        f"Real BrainGlobe load success: {report.get('real_load', {}).get('success')}",
        "",
        "Result:",
    ]
    if report["passed"]:
        final.append("- BrainGlobeAtlas accepted the clean installed Paxinos candidate.")
        final.append("- Next step: ABBA visibility/loading test.")
    elif not report["requested"]:
        final.append("- Native install was not requested. Use menu option 8 or --native-install --clean-install.")
    else:
        final.append("- Clean native installation was attempted but real load did not pass yet.")
        final.append("- Metadata now includes shape, so the next exception should identify the next missing BrainGlobe field if any.")
        final.append(f"- Exception: {report.get('real_load', {}).get('exception')}")
    (REPORTS_DIR / "v13_final_status.txt").write_text("\n".join(final), encoding="utf-8")

    # Compatibility filenames
    (REPORTS_DIR / "v12_final_status.txt").write_text("\n".join(final), encoding="utf-8")
    (REPORTS_DIR / "v12_native_install_report.txt").write_text(text, encoding="utf-8")


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

    table = Table(title="V13 clean native BrainGlobe install")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Requested", str(args.native_install))
    table.add_row("Clean install", str(args.clean_install))
    table.add_row("Candidate valid", str(validation.get("passed")))
    table.add_row("Metadata shape", str(validation.get("metadata", {}).get("shape")))
    table.add_row("Moved old cache items", str(len(report.get("cleanup", {}).get("moved_items", []))))
    table.add_row("Only expected target", str(report.get("post_install_scan", {}).get("only_expected_target")))
    table.add_row("Real load success", str(report.get("real_load", {}).get("success")))
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Exception", str(report.get("real_load", {}).get("exception"))[:100])
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v13_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v13_native_install_report.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v13_cache_cleanup_report.txt'}")

    if args.native_install and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
