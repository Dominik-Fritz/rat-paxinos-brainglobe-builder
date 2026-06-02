from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from nibabel.orientations import aff2axcodes
from rich.console import Console
from rich.table import Table

from parse_labels import (
    entries_by_id,
    parse_cortex_labels,
    parse_itksnap_labels,
    summarize_labels,
)
from utils_paths import RAW_DIR, REPORTS_DIR, discover_expected_files, ensure_project_dirs


console = Console()


def format_path(path: Path | None) -> str:
    return str(path.relative_to(Path.cwd())) if path else "MISSING"


def load_nifti_summary(path: Path) -> dict[str, Any]:
    img = nib.load(str(path))
    hdr = img.header

    # Try to avoid loading massive MRI volumes unless needed.
    dtype = str(hdr.get_data_dtype())
    shape = tuple(int(x) for x in img.shape)
    zooms = tuple(float(x) for x in hdr.get_zooms()[: len(shape)])
    affine = img.affine
    orientation = "".join(aff2axcodes(affine))

    summary: dict[str, Any] = {
        "path": str(path),
        "shape": shape,
        "zooms": zooms,
        "dtype": dtype,
        "orientation": orientation,
        "affine": affine.tolist(),
        "file_size_mb": path.stat().st_size / (1024 * 1024),
    }

    # Only compute unique labels for likely label volumes.
    lower_name = path.name.lower()
    is_likely_label = (
        "atlas" in lower_name
        or "labels" in lower_name
        or "mask" in lower_name
        or lower_name.startswith("neurorat")
    ) and "mri" not in lower_name and "anatomical" not in lower_name

    if is_likely_label:
        data = np.asanyarray(img.dataobj)
        finite = data[np.isfinite(data)]
        if finite.size:
            unique_values = np.unique(finite)
            summary["min"] = float(np.min(unique_values))
            summary["max"] = float(np.max(unique_values))
            summary["unique_count"] = int(unique_values.size)
            summary["all_values_are_integer_like"] = bool(
                np.all(np.isclose(unique_values, np.round(unique_values)))
            )
            summary["first_unique_values"] = [
                int(x) if float(x).is_integer() else float(x)
                for x in unique_values[:50]
            ]
        else:
            summary["min"] = None
            summary["max"] = None
            summary["unique_count"] = 0
            summary["all_values_are_integer_like"] = None
            summary["first_unique_values"] = []
    else:
        summary["min"] = "skipped"
        summary["max"] = "skipped"
        summary["unique_count"] = "skipped"
        summary["all_values_are_integer_like"] = "skipped"
        summary["first_unique_values"] = []

    return summary


def same_geometry(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        tuple(a["shape"]) == tuple(b["shape"])
        and np.allclose(np.array(a["zooms"]), np.array(b["zooms"]), atol=1e-6)
        and np.allclose(np.array(a["affine"]), np.array(b["affine"]), atol=1e-6)
    )


def geometry_similarity(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {
        "same_shape": tuple(a["shape"]) == tuple(b["shape"]),
        "same_zooms": np.allclose(np.array(a["zooms"]), np.array(b["zooms"]), atol=1e-6),
        "same_affine": np.allclose(np.array(a["affine"]), np.array(b["affine"]), atol=1e-6),
        "same_orientation": a["orientation"] == b["orientation"],
        "exact_match": same_geometry(a, b),
    }


def write_report(text: str, json_data: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "input_inspection_report.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "input_inspection_report.json").write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    ensure_project_dirs()
    files = discover_expected_files()

    lines: list[str] = []
    json_report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_dir": str(RAW_DIR),
        "files": {},
        "nifti_summaries": {},
        "label_summaries": {},
        "geometry_comparisons": {},
        "recommendations": [],
    }

    lines.append("Rat Paxinos BrainGlobe Builder - Input Inspection Report")
    lines.append("=" * 72)
    lines.append(f"Generated: {json_report['generated_at']}")
    lines.append(f"Raw data folder: {RAW_DIR}")
    lines.append("")

    lines.append("Expected files")
    lines.append("-" * 72)
    for key, path in files.items():
        status = "FOUND" if path else "MISSING"
        lines.append(f"{key:32s} {status:8s} {format_path(path)}")
        json_report["files"][key] = str(path) if path else None
    lines.append("")

    # NIfTI summaries
    nifti_keys = [
        "paxinos_atlas",
        "sigma_reference",
        "neurorat",
        "neurorat_mri",
        "neurorat_labels",
        "waxholm_atlas",
        "waxholm_labels_nii",
        "waxholm_mask",
        "waxholm_mri",
        "waxholm_aligned_to_neurorat",
    ]

    table = Table(title="NIfTI summaries")
    table.add_column("Key")
    table.add_column("Shape")
    table.add_column("Voxel size")
    table.add_column("Orient")
    table.add_column("Dtype")
    table.add_column("Unique")
    table.add_column("Integer labels")
    table.add_column("Size MB")

    for key in nifti_keys:
        path = files.get(key)
        if not path:
            continue
        try:
            summary = load_nifti_summary(path)
            json_report["nifti_summaries"][key] = summary
            table.add_row(
                key,
                " × ".join(map(str, summary["shape"])),
                ", ".join(f"{z:g}" for z in summary["zooms"]),
                str(summary["orientation"]),
                str(summary["dtype"]),
                str(summary["unique_count"]),
                str(summary["all_values_are_integer_like"]),
                f"{summary['file_size_mb']:.2f}",
            )
        except Exception as exc:
            json_report["nifti_summaries"][key] = {"error": repr(exc)}
            table.add_row(key, "ERROR", "", "", "", "", "", "")

    console.print(table)

    lines.append("NIfTI summaries")
    lines.append("-" * 72)
    for key, summary in json_report["nifti_summaries"].items():
        lines.append(f"[{key}]")
        if "error" in summary:
            lines.append(f"  ERROR: {summary['error']}")
            continue
        lines.append(f"  path: {summary['path']}")
        lines.append(f"  shape: {summary['shape']}")
        lines.append(f"  voxel_size: {summary['zooms']}")
        lines.append(f"  orientation: {summary['orientation']}")
        lines.append(f"  dtype: {summary['dtype']}")
        lines.append(f"  file_size_mb: {summary['file_size_mb']:.2f}")
        lines.append(f"  min/max: {summary['min']} / {summary['max']}")
        lines.append(f"  unique_count: {summary['unique_count']}")
        lines.append(f"  integer_like: {summary['all_values_are_integer_like']}")
        if summary.get("first_unique_values"):
            lines.append(f"  first_unique_values: {summary['first_unique_values']}")
        lines.append("")

    # Label files
    paxinos_label_path = files.get("paxinos_labels")
    if paxinos_label_path:
        entries = parse_itksnap_labels(paxinos_label_path)
        json_report["label_summaries"]["paxinos_labels"] = summarize_labels(entries)
        json_report["label_summaries"]["paxinos_labels"]["entries_by_id_sample"] = {
            str(entry.id): entry.name for entry in entries[:30]
        }

    cortex_label_path = files.get("paxinos_labels_cortex")
    if cortex_label_path:
        entries = parse_cortex_labels(cortex_label_path)
        json_report["label_summaries"]["paxinos_labels_cortex"] = summarize_labels(entries)
        json_report["label_summaries"]["paxinos_labels_cortex"]["entries_by_id_sample"] = {
            str(entry.id): {
                "acronym": entry.acronym,
                "name": entry.name,
            } for entry in entries[:30]
        }

    sigma_label_path = files.get("sigma_labels")
    if sigma_label_path:
        entries = parse_itksnap_labels(sigma_label_path)
        json_report["label_summaries"]["sigma_labels"] = summarize_labels(entries)

    lines.append("Label summaries")
    lines.append("-" * 72)
    for key, summary in json_report["label_summaries"].items():
        lines.append(f"[{key}]")
        for subkey, value in summary.items():
            if subkey == "entries_by_id_sample":
                continue
            lines.append(f"  {subkey}: {value}")
        lines.append("")

    # Geometry comparisons with Paxinos
    paxinos_summary = json_report["nifti_summaries"].get("paxinos_atlas")
    if paxinos_summary and "error" not in paxinos_summary:
        for candidate_key in [
            "sigma_reference",
            "neurorat",
            "neurorat_mri",
            "neurorat_labels",
            "waxholm_atlas",
            "waxholm_labels_nii",
            "waxholm_mri",
            "waxholm_aligned_to_neurorat",
        ]:
            candidate = json_report["nifti_summaries"].get(candidate_key)
            if candidate and "error" not in candidate:
                json_report["geometry_comparisons"][f"paxinos_vs_{candidate_key}"] = (
                    geometry_similarity(paxinos_summary, candidate)
                )

    lines.append("Geometry comparisons to Paxinos")
    lines.append("-" * 72)
    for key, comparison in json_report["geometry_comparisons"].items():
        lines.append(f"[{key}] {comparison}")
    lines.append("")

    # Recommendations
    comparisons = json_report["geometry_comparisons"]
    exact_matches = [key for key, c in comparisons.items() if c.get("exact_match")]
    if exact_matches:
        json_report["recommendations"].append(
            "At least one exact geometry match to Paxinos was found. Prefer an exact matching anatomical reference volume."
        )
    else:
        json_report["recommendations"].append(
            "No exact geometry match to Paxinos was found in available files. Reference volume selection needs manual review."
        )

    if "paxinos_labels" in json_report["label_summaries"]:
        json_report["recommendations"].append(
            "Paxinos label table parsed successfully. Next step: generate a BrainGlobe-compatible structures.json."
        )
    else:
        json_report["recommendations"].append(
            "Paxinos label table missing or failed to parse. Atlas build should not proceed."
        )

    lines.append("Recommendations")
    lines.append("-" * 72)
    for rec in json_report["recommendations"]:
        lines.append(f"- {rec}")
    lines.append("")

    report_text = "\n".join(lines)
    write_report(report_text, json_report)

    console.print(f"\n[green]Report written:[/green] {REPORTS_DIR / 'input_inspection_report.txt'}")
    console.print(f"[green]JSON written:[/green]   {REPORTS_DIR / 'input_inspection_report.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
