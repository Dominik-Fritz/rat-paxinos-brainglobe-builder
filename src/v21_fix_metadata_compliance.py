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

ZENODO_LINK = "https://zenodo.org/records/10926947"
GITHUB_LINK = "https://github.com/BlueBrain/BlueBrainHeadModels"
PAXINOS_CITATION = "Paxinos G, Watson C. The Rat Brain in Stereotaxic Coordinates, 6th edition. Academic Press, 2007."
BLUEBRAIN_CITATION = "BlueBrainHeadModels v1 / Paxinos-Watson atlas digitization, DOI: 10.5281/zenodo.10926947."


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
    before = dict(metadata)

    annotation = nib.load(str(annotation_path))
    reference = nib.load(str(reference_path))
    annotation_shape = [int(x) for x in annotation.shape]
    reference_shape = [int(x) for x in reference.shape]
    zooms_mm = [float(x) for x in annotation.header.get_zooms()[:3]]

    # BrainGlobe / rich metadata compatibility.
    metadata["name"] = metadata.get("name", ATLAS_NAME)
    metadata["atlas_name"] = metadata.get("atlas_name", ATLAS_NAME)
    metadata["title"] = metadata.get(
        "title",
        "Paxinos-Watson Rat Brain Atlas, provisional BrainGlobe package",
    )
    metadata["description"] = metadata.get(
        "description",
        "Provisional local BrainGlobe-compatible rat atlas package generated from the Paxinos-Watson digitization included in BlueBrainHeadModels v1.",
    )
    metadata["species"] = metadata.get("species", "Rattus norvegicus")
    metadata["atlas_link"] = metadata.get("atlas_link", ZENODO_LINK)
    metadata["citation"] = metadata.get("citation", BLUEBRAIN_CITATION)
    metadata["citation_text"] = metadata.get("citation_text", BLUEBRAIN_CITATION)
    metadata["additional_references"] = metadata.get(
        "additional_references",
        [
            {
                "name": "Paxinos and Watson 2007",
                "citation": PAXINOS_CITATION,
                "url": "https://www.elsevier.com/books/the-rat-brain-in-stereotaxic-coordinates/paxinos/978-0-12-374121-9",
            },
            {
                "name": "BlueBrainHeadModels v1",
                "citation": BLUEBRAIN_CITATION,
                "url": ZENODO_LINK,
            },
            {
                "name": "BlueBrainHeadModels GitHub",
                "citation": "Associated source repository.",
                "url": GITHUB_LINK,
            },
        ],
    )

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
    metadata["source"] = metadata.get("source", "BlueBrainHeadModels v1, DOI 10.5281/zenodo.10926947")
    metadata["reference_atlas_preference"] = metadata.get("reference_atlas_preference", "waxholm_rat")
    metadata["reference_file"] = metadata.get("reference_file", "reference.tiff")
    metadata["annotation_file"] = metadata.get("annotation_file", "annotation.tiff")

    metadata["files"] = metadata.get("files", {})
    metadata["files"]["reference_tiff"] = "reference.tiff"
    metadata["files"]["annotation_tiff"] = "annotation.tiff"
    metadata["files"]["reference_nifti"] = "reference.nii.gz"
    metadata["files"]["annotation_nifti"] = "annotation.nii.gz"
    metadata["files"]["structures"] = "structures.json"

    save_json(metadata_path, metadata)

    required_for_abba = [
        "atlas_link",
        "additional_references",
        "shape",
        "resolution",
        "orientation",
        "species",
        "citation",
        "version",
    ]

    missing_after = [k for k in required_for_abba if k not in metadata]
    changed = sorted(k for k in metadata.keys() if before.get(k) != metadata.get(k))

    return {
        "folder": str(folder),
        "metadata_path": str(metadata_path),
        "annotation_shape": annotation_shape,
        "reference_shape": reference_shape,
        "changed_keys": changed,
        "required_for_abba": required_for_abba,
        "missing_after": missing_after,
        "atlas_link": metadata.get("atlas_link"),
        "additional_references_count": len(metadata.get("additional_references", [])),
        "passed": len(missing_after) == 0,
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
    (REPORTS_DIR / f"v21_metadata_compliance_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V21 metadata compliance report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"Folder: {result['folder']}",
        f"PASSED: {report['passed']}",
        "",
        f"atlas_link: {result['atlas_link']}",
        f"additional_references_count: {result['additional_references_count']}",
        f"annotation_shape: {result['annotation_shape']}",
        f"reference_shape: {result['reference_shape']}",
        "",
        "Required ABBA/BrainGlobe metadata keys:",
    ]
    for k in result["required_for_abba"]:
        lines.append(f"- {k}")
    lines.append("")
    lines.append("Missing after:")
    lines.extend([f"- {k}" for k in result["missing_after"]] or ["- none"])
    lines.append("")
    lines.append("Changed keys:")
    for k in result["changed_keys"]:
        lines.append(f"- {k}")

    text = "\n".join(lines)
    (REPORTS_DIR / f"v21_metadata_compliance_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v21_metadata_compliance_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V21 metadata compliance ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("atlas_link", str(result["atlas_link"]))
    table.add_row("additional refs", str(result["additional_references_count"]))
    table.add_row("missing", str(len(result["missing_after"])))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / f'v21_metadata_compliance_report{suffix}.txt'}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
