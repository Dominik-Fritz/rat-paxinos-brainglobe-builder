from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from types import ModuleType

import nibabel as nib
import numpy as np
from rich.console import Console
from rich.table import Table

from utils_paths import (
    ATLAS_NAME,
    OUTPUT_DIR,
    REPORTS_DIR,
    official_candidate_folder,
    project_local_cache_folder,
)

console = Console()


def module_path(module_name: str) -> str | None:
    try:
        module = importlib.import_module(module_name)
        return str(Path(inspect.getfile(module)).resolve())
    except Exception:
        return None


def discover_cache_diagnostics() -> dict:
    home = Path.home()
    paths = {
        "home": str(home),
        "project_local_cache": str(project_local_cache_folder()),
        "candidate_folder": str(official_candidate_folder()),
        "possible_user_cache_dirs": [
            str(home / ".brainglobe"),
            str(home / ".brainglobe" / "atlases"),
            str(home / ".cache" / "brainglobe"),
            str(home / ".cache" / "brainglobe" / "atlases"),
            str(home / "brainglobe"),
        ],
        "env": {
            "BRAINGLOBE_ATLAS_DIR": os.environ.get("BRAINGLOBE_ATLAS_DIR"),
            "BRAINGLOBE_DIR": os.environ.get("BRAINGLOBE_DIR"),
            "BRAINGLOBE_CONFIG_DIR": os.environ.get("BRAINGLOBE_CONFIG_DIR"),
        },
        "modules": {
            "brainglobe_atlasapi": module_path("brainglobe_atlasapi"),
            "brainglobe_atlasapi.bg_atlas": module_path("brainglobe_atlasapi.bg_atlas"),
            "brainglobe_atlasapi.config": module_path("brainglobe_atlasapi.config"),
            "brainglobe_atlasapi.utils": module_path("brainglobe_atlasapi.utils"),
        },
    }

    # Try to read useful constants without assuming they exist.
    constants = {}
    for module_name in ["brainglobe_atlasapi.config", "brainglobe_atlasapi.utils"]:
        try:
            module = importlib.import_module(module_name)
            constants[module_name] = {}
            for attr in dir(module):
                if any(key in attr.lower() for key in ["dir", "path", "cache", "atlas", "version", "url"]):
                    try:
                        value = getattr(module, attr)
                        if isinstance(value, (str, Path, int, float, bool, type(None))):
                            constants[module_name][attr] = str(value)
                    except Exception:
                        pass
        except Exception as exc:
            constants[module_name] = {"error": repr(exc)}
    paths["module_constants"] = constants
    return paths


def validate_candidate_folder(folder: Path) -> dict:
    required = ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json"]
    result = {
        "folder": str(folder),
        "exists": folder.exists(),
        "required_files": {name: (folder / name).exists() for name in required},
        "passed": False,
        "nifti": {},
        "structures": {},
        "metadata": {},
        "errors": [],
    }
    if not folder.exists():
        result["errors"].append("folder_missing")
        return result
    missing = [name for name, ok in result["required_files"].items() if not ok]
    if missing:
        result["errors"].append(f"missing_files:{missing}")
        return result

    try:
        ref = nib.load(str(folder / "reference.nii.gz"))
        ann = nib.load(str(folder / "annotation.nii.gz"))
        ann_data = np.asanyarray(ann.dataobj)
        structures = json.loads((folder / "structures.json").read_text(encoding="utf-8"))
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        ids = set(np.unique(ann_data).astype(int).tolist())
        structure_ids = set(int(s["id"]) for s in structures)
        ids_without_structure = sorted(x for x in ids if x != 0 and x not in structure_ids)

        result["nifti"] = {
            "same_shape": tuple(ref.shape) == tuple(ann.shape),
            "same_affine": bool(np.allclose(ref.affine, ann.affine, atol=1e-6)),
            "reference_shape": list(ref.shape),
            "annotation_shape": list(ann.shape),
            "annotation_unique_count": int(np.unique(ann_data).size),
            "annotation_integer_like": bool(np.all(np.isclose(np.unique(ann_data), np.round(np.unique(ann_data))))),
        }
        result["structures"] = {
            "count": len(structures),
            "ids_without_structure_count": len(ids_without_structure),
            "ids_without_structure_sample": ids_without_structure[:50],
        }
        result["metadata"] = {
            "name": metadata.get("name"),
            "species": metadata.get("species"),
            "resolution": metadata.get("resolution"),
            "orientation": metadata.get("orientation"),
        }
        if not result["nifti"]["same_shape"]:
            result["errors"].append("shape_mismatch")
        if not result["nifti"]["same_affine"]:
            result["errors"].append("affine_mismatch")
        if not result["nifti"]["annotation_integer_like"]:
            result["errors"].append("annotation_not_integer_like")
        if ids_without_structure:
            result["errors"].append("ids_without_structure")
    except Exception as exc:
        result["errors"].append(repr(exc))
    result["passed"] = not result["errors"]
    return result


def create_offline_cache_layouts() -> dict:
    """Create several candidate cache layouts for exact-cache reverse engineering.

    This is deliberately project-local and reversible. No global BrainGlobe folders
    are modified here. Because blindly mutating user caches is how tools become
    cursed objects.
    """
    source = official_candidate_folder()
    sandbox_root = OUTPUT_DIR / "brainglobe_offline_cache_sandbox"
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root)
    sandbox_root.mkdir(parents=True, exist_ok=True)

    layouts = {}

    # Layout A: direct atlas folder.
    layout_a = sandbox_root / "layout_a_direct" / ATLAS_NAME
    shutil.copytree(source, layout_a)
    layouts["layout_a_direct"] = str(layout_a)

    # Layout B: atlases/<atlas_name>.
    layout_b = sandbox_root / "layout_b_atlases_subdir" / "atlases" / ATLAS_NAME
    layout_b.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, layout_b)
    layouts["layout_b_atlases_subdir"] = str(layout_b)

    # Layout C: atlas name with files in a version-like subfolder.
    layout_c = sandbox_root / "layout_c_versioned" / "atlases" / ATLAS_NAME / "1"
    layout_c.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, layout_c)
    layouts["layout_c_versioned"] = str(layout_c)

    # Layout D: project local cache mirror from V7.
    mirror = project_local_cache_folder()
    if mirror.exists():
        layouts["v7_project_local_cache"] = str(mirror)

    index = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "layouts": layouts,
        "note": "Candidate layouts for offline/cache reverse engineering. None modifies global BrainGlobe cache.",
    }
    (sandbox_root / "offline_cache_layouts.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"sandbox_root": str(sandbox_root), "layouts": layouts, "index": index}


def patch_brainglobe_network_checks() -> dict:
    """Best-effort monkeypatch for the online GIN availability check.

    This does not guarantee BrainGlobeAtlas will load a non-indexed custom atlas.
    It only removes the specific 'GIN server is down' blocker so the next real
    exception becomes visible. Tiny distinction, giant amount of debugging saved.
    """
    result = {"attempted": True, "patched": [], "errors": []}
    try:
        utils = importlib.import_module("brainglobe_atlasapi.utils")
        if hasattr(utils, "check_gin_status"):
            def _always_true(*args, **kwargs):
                return True
            utils.check_gin_status = _always_true
            result["patched"].append("brainglobe_atlasapi.utils.check_gin_status")
    except Exception as exc:
        result["errors"].append(f"utils patch failed: {exc!r}")

    # Some versions import the function into other modules at import time.
    for module_name in ["brainglobe_atlasapi.bg_atlas"]:
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "check_gin_status"):
                def _always_true(*args, **kwargs):
                    return True
                setattr(module, "check_gin_status", _always_true)
                result["patched"].append(f"{module_name}.check_gin_status")
        except Exception as exc:
            result["errors"].append(f"{module_name} patch failed: {exc!r}")

    return result


def try_offline_brainglobe_load() -> dict:
    result = {
        "attempted": True,
        "success": False,
        "exception": None,
        "traceback": None,
        "object_summary": {},
    }
    patch = patch_brainglobe_network_checks()
    result["patch"] = patch

    try:
        from brainglobe_atlasapi import BrainGlobeAtlas
        atlas = BrainGlobeAtlas(ATLAS_NAME)
        result["success"] = True
        result["object_summary"] = {
            "class": atlas.__class__.__name__,
            "repr": repr(atlas),
            "has_reference": hasattr(atlas, "reference"),
            "has_annotation": hasattr(atlas, "annotation"),
            "has_structures": hasattr(atlas, "structures"),
        }
        for attr in ["resolution", "orientation", "root_dir", "atlas_dir", "name"]:
            try:
                result["object_summary"][attr] = str(getattr(atlas, attr))
            except Exception:
                pass
    except Exception as exc:
        result["exception"] = repr(exc)
        result["traceback"] = traceback.format_exc()
    return result


def write_reports(cache_diag: dict, candidate_validation: dict, layouts: dict, offline_load: dict, requested: bool) -> bool:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    passed = bool(candidate_validation.get("passed")) and (bool(offline_load.get("success")) if requested else True)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "offline_load_requested": requested,
        "cache_diagnostics": cache_diag,
        "candidate_validation": candidate_validation,
        "offline_cache_layouts": layouts,
        "offline_brainglobe_load": offline_load,
        "passed": passed,
        "interpretation": (
            "Offline BrainGlobeAtlas load passed." if offline_load.get("success")
            else "Offline load did not pass yet. Use the new exception to implement exact cache/index registration."
        ),
    }

    (REPORTS_DIR / "v8_offline_load_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    cache_lines = [
        "V8 cache diagnostics",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        "",
        "Environment variables:",
    ]
    for k, v in cache_diag.get("env", {}).items():
        cache_lines.append(f"- {k}: {v}")
    cache_lines.append("")
    cache_lines.append("Module paths:")
    for k, v in cache_diag.get("modules", {}).items():
        cache_lines.append(f"- {k}: {v}")
    cache_lines.append("")
    cache_lines.append("Possible user cache dirs:")
    for p in cache_diag.get("possible_user_cache_dirs", []):
        cache_lines.append(f"- {p}")
    cache_lines.append("")
    cache_lines.append("Module constants:")
    for module_name, constants in cache_diag.get("module_constants", {}).items():
        cache_lines.append(f"[{module_name}]")
        if isinstance(constants, dict):
            for k, v in constants.items():
                cache_lines.append(f"- {k}: {v}")
        else:
            cache_lines.append(f"- {constants}")
    (REPORTS_DIR / "v8_cache_diagnostics.txt").write_text("\n".join(cache_lines), encoding="utf-8")

    lines = [
        "V8 offline BrainGlobe load report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Atlas: {ATLAS_NAME}",
        f"Offline load requested: {requested}",
        f"PASSED: {passed}",
        "",
        "Candidate validation:",
    ]
    for k, v in candidate_validation.items():
        if k not in ("nifti", "structures"):
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("NIfTI:")
    for k, v in candidate_validation.get("nifti", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Structures:")
    for k, v in candidate_validation.get("structures", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Offline cache layouts:")
    lines.append(f"- sandbox_root: {layouts.get('sandbox_root')}")
    for k, v in layouts.get("layouts", {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Offline BrainGlobeAtlas load:")
    for k, v in offline_load.items():
        if k != "traceback":
            lines.append(f"- {k}: {v}")
    if offline_load.get("traceback"):
        lines.append("")
        lines.append("Traceback:")
        lines.append(offline_load["traceback"])
    (REPORTS_DIR / "v8_offline_load_report.txt").write_text("\n".join(lines), encoding="utf-8")

    final_lines = [
        "V8 FINAL STATUS",
        "=" * 72,
        f"PASSED: {passed}",
        f"Offline load requested: {requested}",
        f"Offline load success: {offline_load.get('success')}",
        f"Candidate validation passed: {candidate_validation.get('passed')}",
        "",
        "Result:",
    ]
    if offline_load.get("success"):
        final_lines.append("- BrainGlobeAtlas accepted the atlas after offline patching.")
        final_lines.append("- Next step: ABBA visibility test and packaging cleanup.")
    elif requested:
        final_lines.append("- Online GIN check has been bypassed, but BrainGlobeAtlas still did not load the custom atlas.")
        final_lines.append("- This is now useful: the traceback should reveal the exact cache/index format BrainGlobe expects.")
        final_lines.append(f"- Exception: {offline_load.get('exception')}")
    else:
        final_lines.append("- Offline load was not requested. Cache diagnostics and candidate layouts were generated.")
        final_lines.append("- Run mode 4 or 5 in run_builder.bat for patched offline load.")
    (REPORTS_DIR / "v8_final_status.txt").write_text("\n".join(final_lines), encoding="utf-8")

    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-load", action="store_true", help="Attempt patched offline BrainGlobeAtlas load.")
    args = parser.parse_args()

    cache_diag = discover_cache_diagnostics()
    candidate_validation = validate_candidate_folder(official_candidate_folder())
    layouts = create_offline_cache_layouts()

    if args.offline_load:
        offline_load = try_offline_brainglobe_load()
    else:
        offline_load = {
            "attempted": False,
            "success": False,
            "exception": "Offline load skipped. Use --offline-load.",
            "traceback": None,
            "object_summary": {},
        }

    passed = write_reports(cache_diag, candidate_validation, layouts, offline_load, args.offline_load)

    table = Table(title="V8 offline/cache diagnostics")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Candidate validation", str(candidate_validation.get("passed")))
    table.add_row("Offline load requested", str(args.offline_load))
    table.add_row("Offline load success", str(offline_load.get("success")))
    table.add_row("Exception", str(offline_load.get("exception"))[:100])
    table.add_row("Passed", str(passed))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v8_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v8_offline_load_report.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v8_cache_diagnostics.txt'}")

    # If offline load was requested and failed, return 0 anyway because this is
    # a diagnostic step. We need the traceback, not a tantrum from cmd.exe.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
