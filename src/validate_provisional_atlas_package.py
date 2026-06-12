from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, provisional_folder

console = Console()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_valid_root_structure(structure: dict[str, Any]) -> bool:
    try:
        sid = int(structure.get("id"))
    except Exception:
        return False

    name = str(structure.get("name", "")).strip().lower()
    acronym = str(structure.get("acronym", "")).strip().lower()
    path = structure.get("structure_id_path", [])

    if sid == 997:
        if name == "root" or acronym == "root":
            return True
        if isinstance(path, list) and len(path) == 1 and int(path[0]) == 997:
            return True

    # Backward compatibility for older provisional reports only.
    if sid == 997000:
        if name == "root" or acronym == "root":
            return True
        if isinstance(path, list) and len(path) == 1 and int(path[0]) == 997000:
            return True

    return False


def structure_id_set(structures: list[dict[str, Any]]) -> set[int]:
    out = set()
    for s in structures:
        try:
            out.add(int(s["id"]))
        except Exception:
            pass
    return out


def validate() -> dict[str, Any]:
    folder = provisional_folder()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    required_files = ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json", "README.md"]
    file_checks = {name: (folder / name).exists() for name in required_files}

    errors: list[str] = []
    warnings: list[str] = []

    for name, exists in file_checks.items():
        if not exists:
            errors.append(f"Missing required file: {name}")

    if errors:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "atlas_folder": str(folder),
            "file_checks": file_checks,
            "passed": False,
            "warnings": warnings,
            "errors": errors,
        }

    ref_img = nib.load(str(folder / "reference.nii.gz"))
    ann_img = nib.load(str(folder / "annotation.nii.gz"))
    ref = np.asanyarray(ref_img.dataobj)
    ann = np.asanyarray(ann_img.dataobj)

    ref_shape = list(ref.shape)
    ann_shape = list(ann.shape)
    same_shape = ref_shape == ann_shape
    same_affine = np.allclose(ref_img.affine, ann_img.affine)
    ann_unique = np.unique(ann[np.isfinite(ann)])
    used_ids = set(np.round(ann_unique).astype(int).tolist())
    used_nonzero_ids = {x for x in used_ids if x != 0}

    structures = load_json(folder / "structures.json")
    metadata = load_json(folder / "metadata.json")

    if not isinstance(structures, list):
        errors.append("structures.json is not a list.")
        structures = []

    ids = structure_id_set(structures)
    roots = [s for s in structures if is_valid_root_structure(s)]

    ids_without_structure = sorted(used_nonzero_ids - ids)
    structures_without_voxels = sorted(ids - used_nonzero_ids)

    required_keys = {"id", "name", "acronym", "rgb_triplet", "structure_id_path"}
    required_keys_present = all(required_keys.issubset(set(s.keys())) for s in structures)

    if not same_shape:
        errors.append("Reference and annotation shapes differ.")
    if len(roots) != 1:
        errors.append(f"Expected exactly one valid root structure, found {len(roots)}.")
    if ids_without_structure:
        errors.append(f"{len(ids_without_structure)} annotation IDs have no structure entry.")
    if not required_keys_present:
        errors.append("At least one structure entry is missing required keys.")

    annotation_integer_like = np.allclose(ann_unique, np.round(ann_unique))

    nifti_checks = {
        "reference_shape": ref_shape,
        "annotation_shape": ann_shape,
        "same_shape": same_shape,
        "reference_dtype": str(ref.dtype),
        "annotation_dtype": str(ann.dtype),
        "same_affine": bool(same_affine),
        "annotation_unique_count": int(len(ann_unique)),
        "annotation_integer_like": bool(annotation_integer_like),
        "reference_nonzero_voxels": int(np.count_nonzero(ref)),
    }

    structure_checks = {
        "structure_count": len(structures),
        "has_root": len(roots) == 1,
        "root_ids": [int(r["id"]) for r in roots if "id" in r],
        "root_preview": roots[:1],
        "ids_without_structure_count": len(ids_without_structure),
        "ids_without_structure": ids_without_structure[:50],
        "structure_ids_without_voxels_count": len(structures_without_voxels),
        "structure_ids_without_voxels_sample": structures_without_voxels[:25],
        "required_keys_present": required_keys_present,
    }

    metadata_checks = {
        "name": metadata.get("name"),
        "species": metadata.get("species"),
        "resolution": metadata.get("resolution"),
        "orientation": metadata.get("orientation"),
        "root_id": metadata.get("root_id"),
        "source_dataset_version": (metadata.get("source_dataset") or {}).get("version"),
        "source_dataset_doi": (metadata.get("source_dataset") or {}).get("doi"),
    }

    brainglobe_import_checks = {}
    try:
        import brainglobe_atlasapi
        brainglobe_import_checks["brainglobe_atlasapi_import"] = True
        brainglobe_import_checks["brainglobe_atlasapi_version"] = getattr(brainglobe_atlasapi, "__version__", "unknown")
    except Exception as exc:
        brainglobe_import_checks["brainglobe_atlasapi_import"] = False
        brainglobe_import_checks["brainglobe_atlasapi_error"] = repr(exc)
        errors.append("brainglobe_atlasapi import failed.")

    try:
        import brainglobe_utils
        brainglobe_import_checks["brainglobe_utils_import"] = True
        brainglobe_import_checks["brainglobe_utils_version"] = getattr(brainglobe_utils, "__version__", "unknown")
    except Exception as exc:
        brainglobe_import_checks["brainglobe_utils_import"] = False
        brainglobe_import_checks["brainglobe_utils_error"] = repr(exc)

    passed = len(errors) == 0

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_folder": str(folder),
        "file_checks": file_checks,
        "nifti_checks": nifti_checks,
        "structure_checks": structure_checks,
        "metadata_checks": metadata_checks,
        "brainglobe_import_checks": brainglobe_import_checks,
        "passed": passed,
        "warnings": warnings,
        "errors": errors,
    }


def write_report(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    (REPORTS_DIR / "provisional_atlas_loader_validation.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "Provisional atlas loader validation",
        "=" * 72,
        f"Generated: {report.get('generated_at')}",
        f"Atlas folder: {report.get('atlas_folder')}",
        f"PASSED: {report.get('passed')}",
        "",
        "file_checks:",
    ]

    for k, v in report.get("file_checks", {}).items():
        lines.append(f"- {k}: {v}")

    for section in ["nifti_checks", "structure_checks", "metadata_checks", "brainglobe_import_checks"]:
        lines.append("")
        lines.append(f"{section}:")
        for k, v in report.get(section, {}).items():
            lines.append(f"- {k}: {v}")

    lines.append("")
    lines.append("Warnings:")
    if report.get("warnings"):
        for w in report["warnings"]:
            lines.append(f"- {w}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Errors:")
    if report.get("errors"):
        for e in report["errors"]:
            lines.append(f"- {e}")
    else:
        lines.append("- none")

    (REPORTS_DIR / "provisional_atlas_loader_validation.txt").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main() -> int:
    report = validate()
    write_report(report)

    table = Table(title="Local provisional atlas validation")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report.get("passed")))
    table.add_row("Errors", str(len(report.get("errors", []))))
    table.add_row("Warnings", str(len(report.get("warnings", []))))
    table.add_row("Same shape", str(report.get("nifti_checks", {}).get("same_shape")))
    table.add_row("Has root", str(report.get("structure_checks", {}).get("has_root")))
    table.add_row("IDs without structure", str(report.get("structure_checks", {}).get("ids_without_structure_count")))
    table.add_row("BrainGlobe API import", str(report.get("brainglobe_import_checks", {}).get("brainglobe_atlasapi_import")))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'provisional_atlas_loader_validation.txt'}")

    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
