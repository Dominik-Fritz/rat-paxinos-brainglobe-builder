from __future__ import annotations
import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import nibabel as nib
import numpy as np
from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, ATLAS_NAME

console = Console()

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def schema_validate(folder: Path):
    errors = []
    warnings = []
    required_files = ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json", "candidate_manifest.json"]
    file_checks = {f: (folder / f).exists() for f in required_files}
    missing = [f for f, ok in file_checks.items() if not ok]
    if missing:
        errors.append(f"Missing files: {missing}")
        return {"file_checks": file_checks, "errors": errors, "warnings": warnings}

    reference = nib.load(str(folder / "reference.nii.gz"))
    annotation = nib.load(str(folder / "annotation.nii.gz"))
    structures = load_json(folder / "structures.json")
    metadata = load_json(folder / "metadata.json")
    manifest = load_json(folder / "candidate_manifest.json")

    ann_data = np.asanyarray(annotation.dataobj)
    used_ids = set(np.unique(ann_data).astype(int).tolist())
    structure_ids = set(int(s["id"]) for s in structures)
    ids_without_structure = sorted(x for x in used_ids if x != 0 and x not in structure_ids)

    required_structure_keys = {"id", "name", "acronym", "rgb_triplet", "structure_id_path"}
    bad_structures = []
    duplicate_ids = set()
    seen = set()
    for s in structures:
        sid = int(s.get("id", -1))
        if sid in seen:
            duplicate_ids.add(sid)
        seen.add(sid)
        missing_keys = sorted(required_structure_keys - set(s.keys()))
        if missing_keys:
            bad_structures.append({"id": sid, "missing_keys": missing_keys})
        rgb = s.get("rgb_triplet", [])
        if not (isinstance(rgb, list) and len(rgb) == 3 and all(isinstance(x, int) and 0 <= x <= 255 for x in rgb)):
            bad_structures.append({"id": sid, "bad_rgb_triplet": rgb})

    nifti_checks = {"reference_shape": list(reference.shape), "annotation_shape": list(annotation.shape), "same_shape": tuple(reference.shape) == tuple(annotation.shape), "same_affine": bool(np.allclose(reference.affine, annotation.affine, atol=1e-6)), "reference_dtype": str(reference.header.get_data_dtype()), "annotation_dtype": str(annotation.header.get_data_dtype()), "annotation_integer_like": bool(np.all(np.isclose(np.unique(ann_data), np.round(np.unique(ann_data))))), "annotation_unique_count": int(np.unique(ann_data).size)}
    structure_checks = {"structure_count": len(structures), "ids_without_structure_count": len(ids_without_structure), "ids_without_structure": ids_without_structure[:100], "duplicate_structure_ids": sorted(duplicate_ids), "bad_structures_sample": bad_structures[:50], "all_required_keys_ok": len(bad_structures) == 0}
    metadata_checks = {"name": metadata.get("name"), "species": metadata.get("species"), "resolution": metadata.get("resolution"), "orientation": metadata.get("orientation"), "has_source_dataset": "source_dataset" in metadata, "candidate_manifest_atlas_name": manifest.get("atlas_name")}

    if not nifti_checks["same_shape"]:
        errors.append("Reference and annotation shape mismatch.")
    if not nifti_checks["same_affine"]:
        errors.append("Reference and annotation affine mismatch.")
    if not nifti_checks["annotation_integer_like"]:
        errors.append("Annotation is not integer-like.")
    if structure_checks["ids_without_structure_count"]:
        errors.append("Annotation contains IDs without structure entries.")
    if structure_checks["duplicate_structure_ids"]:
        errors.append("Duplicate structure IDs found.")
    if not structure_checks["all_required_keys_ok"]:
        errors.append("Some structures are malformed.")
    if metadata_checks["name"] != ATLAS_NAME:
        warnings.append("Metadata name does not match expected atlas name.")

    return {"file_checks": file_checks, "nifti_checks": nifti_checks, "structure_checks": structure_checks, "metadata_checks": metadata_checks, "errors": errors, "warnings": warnings}

def brain_globe_import_probe():
    result = {}
    try:
        import brainglobe_atlasapi
        result["brainglobe_atlasapi_import"] = True
        result["brainglobe_atlasapi_version"] = getattr(brainglobe_atlasapi, "__version__", "unknown")
    except Exception as exc:
        result["brainglobe_atlasapi_import"] = False
        result["brainglobe_atlasapi_error"] = repr(exc)

    try:
        from brainglobe_atlasapi import BrainGlobeAtlas  # noqa: F401
        result["BrainGlobeAtlas_import"] = True
    except Exception as exc:
        result["BrainGlobeAtlas_import"] = False
        result["BrainGlobeAtlas_error"] = repr(exc)

    try:
        import brainglobe_utils
        result["brainglobe_utils_import"] = True
        result["brainglobe_utils_version"] = getattr(brainglobe_utils, "__version__", "unknown")
    except Exception as exc:
        result["brainglobe_utils_import"] = False
        result["brainglobe_utils_error"] = repr(exc)

    return result

def optional_local_install(folder: Path):
    local_cache = folder.parent / "_local_test_cache" / folder.name
    if local_cache.exists():
        shutil.rmtree(local_cache)
    local_cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(folder, local_cache)
    return {"attempted": True, "local_cache_folder": str(local_cache), "note": "This is a project-local copy, not a real BrainGlobe global cache registration."}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-local", action="store_true")
    args = parser.parse_args()

    folder = official_candidate_folder()
    if not folder.exists():
        raise FileNotFoundError(f"Missing official candidate folder: {folder}")

    schema = schema_validate(folder)
    imports = brain_globe_import_probe()
    install = {"attempted": False}
    if args.install_local and not schema["errors"]:
        install = optional_local_install(folder)

    passed = (len(schema["errors"]) == 0 and imports.get("brainglobe_atlasapi_import") is True and imports.get("BrainGlobeAtlas_import") is True)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "candidate_folder": str(folder), "schema_validation": schema, "brain_globe_import_probe": imports, "experimental_local_install": install, "passed": passed, "interpretation": "Candidate is internally valid and BrainGlobe API imports are available." if passed else "Candidate failed schema checks or BrainGlobe import probes.", "next_step": "If passed, the next work item is V7 local registration and real BrainGlobeAtlas load testing."}

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v6_brainglobe_compatibility_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["V6 BrainGlobe compatibility report", "=" * 72, f"Generated: {report['generated_at']}", f"Candidate folder: {folder}", f"PASSED: {passed}", "", "Schema errors:"]
    lines += [f"- {e}" for e in schema["errors"]] or ["- none"]
    lines += ["", "Schema warnings:"]
    lines += [f"- {w}" for w in schema["warnings"]] or ["- none"]
    lines += ["", "BrainGlobe import probe:"]
    for k, v in imports.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "Experimental local install:"]
    for k, v in install.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "Interpretation:", f"- {report['interpretation']}"]
    (REPORTS_DIR / "v6_brainglobe_compatibility_report.txt").write_text("\\n".join(lines), encoding="utf-8")

    table = Table(title="V6 BrainGlobe compatibility")
    table.add_column("Check"); table.add_column("Value")
    table.add_row("Passed", str(passed))
    table.add_row("Schema errors", str(len(schema["errors"])))
    table.add_row("Schema warnings", str(len(schema["warnings"])))
    table.add_row("BrainGlobeAtlas import", str(imports.get("BrainGlobeAtlas_import")))
    table.add_row("Local install attempted", str(install.get("attempted")))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v6_brainglobe_compatibility_report.txt'}")
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
