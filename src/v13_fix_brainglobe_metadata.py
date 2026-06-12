from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
from rich.console import Console
from rich.table import Table

from utils_paths import (
    ATLAS_NAME,
    REPORTS_DIR,
    official_candidate_folder,
    provisional_folder,
)

console = Console()


def target_folder(name: str) -> Path:
    if name == "provisional":
        return provisional_folder()
    if name == "official":
        return official_candidate_folder()
    raise ValueError(f"Unknown target: {name}")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def fix_metadata(folder: Path) -> dict[str, Any]:
    metadata_path = folder / "metadata.json"
    annotation_path = folder / "annotation.nii.gz"
    reference_path = folder / "reference.nii.gz"

    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing metadata.json: {metadata_path}")
    if not annotation_path.exists():
        raise FileNotFoundError(f"Missing annotation.nii.gz: {annotation_path}")
    if not reference_path.exists():
        raise FileNotFoundError(f"Missing reference.nii.gz: {reference_path}")

    metadata = load_json(metadata_path)
    before_keys = sorted(metadata.keys())

    annotation = nib.load(str(annotation_path))
    reference = nib.load(str(reference_path))

    annotation_shape = [int(x) for x in annotation.shape]
    reference_shape = [int(x) for x in reference.shape]
    zooms_mm = [float(x) for x in annotation.header.get_zooms()[:3]]

    # BrainGlobe core.py expects at least metadata["shape"].
    # Resolution in BrainGlobe is conventionally micrometers, so keep [40,40,40]
    # for this Paxinos rasterization and record NIfTI zooms separately.
    metadata["name"] = metadata.get("name", ATLAS_NAME)
    metadata["atlas_name"] = metadata.get("atlas_name", ATLAS_NAME)
    metadata["citation"] = metadata.get(
        "citation",
        "BlueBrainHeadModels v1 / Paxinos-Watson rat atlas digitization; DOI 10.5281/zenodo.10926947",
    )
    metadata["species"] = metadata.get("species", "Rattus norvegicus")
    metadata["symmetric"] = metadata.get("symmetric", False)
    metadata["resolution"] = metadata.get("resolution", [40, 40, 40])
    metadata["shape"] = annotation_shape
    metadata["annotation_shape"] = annotation_shape
    metadata["reference_shape"] = reference_shape
    metadata["nifti_voxel_size_mm"] = zooms_mm
    metadata["orientation"] = metadata.get("orientation", "LPI")
    metadata["root_id"] = metadata.get("root_id", 997000)
    metadata["version"] = metadata.get("version", "1.0")
    metadata["format"] = metadata.get("format", "brainglobe_local_candidate")
    metadata["source"] = metadata.get(
        "source",
        "BlueBrainHeadModels v1, DOI 10.5281/zenodo.10926947",
    )
    metadata["reference_atlas_preference"] = "waxholm_rat"
    metadata["reference_note"] = (
        "If an external reference atlas is needed for comparison, prefer Waxholm rat over Allen mouse because the target species is rat."
    )

    save_json(metadata_path, metadata)

    after_keys = sorted(metadata.keys())
    changed_keys = sorted(set(after_keys) - set(before_keys))
    ensured_keys = [
        "shape",
        "annotation_shape",
        "reference_shape",
        "resolution",
        "orientation",
        "species",
        "symmetric",
        "root_id",
        "version",
    ]

    return {
        "folder": str(folder),
        "metadata_path": str(metadata_path),
        "annotation_shape": annotation_shape,
        "reference_shape": reference_shape,
        "nifti_voxel_size_mm": zooms_mm,
        "before_keys": before_keys,
        "after_keys": after_keys,
        "changed_or_added_keys": changed_keys,
        "ensured_keys": ensured_keys,
        "has_shape": "shape" in metadata,
        "shape": metadata.get("shape"),
        "passed": "shape" in metadata and metadata.get("shape") == annotation_shape,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official"], required=True)
    args = parser.parse_args()

    folder = target_folder(args.target)
    result = fix_metadata(folder)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "result": result,
        "passed": result["passed"],
    }

    suffix = f"_{args.target}"
    (REPORTS_DIR / f"v13_metadata_fix_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V13 BrainGlobe metadata fix report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"Folder: {result['folder']}",
        f"PASSED: {report['passed']}",
        "",
        "Ensured keys:",
    ]
    for k in result["ensured_keys"]:
        lines.append(f"- {k}")
    lines += [
        "",
        f"shape: {result['shape']}",
        f"annotation_shape: {result['annotation_shape']}",
        f"reference_shape: {result['reference_shape']}",
        f"nifti_voxel_size_mm: {result['nifti_voxel_size_mm']}",
        "",
        "Added keys:",
    ]
    for k in result["changed_or_added_keys"]:
        lines.append(f"- {k}")

    text = "\n".join(lines)
    (REPORTS_DIR / f"v13_metadata_fix_report{suffix}.txt").write_text(text, encoding="utf-8")
    # Convenience latest report
    (REPORTS_DIR / "v13_metadata_fix_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V13 metadata fix ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Shape", str(result["shape"]))
    table.add_row("Has shape", str(result["has_shape"]))
    table.add_row("Added keys", str(len(result["changed_or_added_keys"])))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / f'v13_metadata_fix_report{suffix}.txt'}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
