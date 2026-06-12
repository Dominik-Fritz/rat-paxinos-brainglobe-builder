from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any
import nibabel as nib
import numpy as np
from nibabel.orientations import aff2axcodes
from rich.console import Console
from rich.table import Table
from parse_labels import parse_cortex_labels, parse_itksnap_labels
from utils_paths import RAW_DIR, REPORTS_DIR, discover_expected_files, ensure_project_dirs

console = Console()

def summarize_labels(entries):
    ids = [e.id for e in entries]
    return {"count": len(entries), "min_id": min(ids) if ids else None, "max_id": max(ids) if ids else None, "duplicate_ids": sorted({x for x in ids if ids.count(x) > 1}), "placeholder_count": len([e for e in entries if e.is_placeholder]), "sample_names": [e.name for e in entries[:20]]}

def load_nifti_summary(path: Path) -> dict[str, Any]:
    img = nib.load(str(path))
    hdr = img.header
    shape = tuple(int(x) for x in img.shape)
    zooms = tuple(float(x) for x in hdr.get_zooms()[: len(shape)])
    affine = img.affine
    lower_name = path.name.lower()
    is_likely_label = ("atlas" in lower_name or "labels" in lower_name or "mask" in lower_name or lower_name.startswith("neurorat")) and "mri" not in lower_name and "anatomical" not in lower_name
    summary = {"path": str(path), "shape": shape, "zooms": zooms, "dtype": str(hdr.get_data_dtype()), "orientation": "".join(aff2axcodes(affine)), "affine": affine.tolist(), "file_size_mb": path.stat().st_size / (1024 * 1024)}
    if is_likely_label:
        data = np.asanyarray(img.dataobj)
        unique = np.unique(data[np.isfinite(data)])
        summary.update({"min": float(np.min(unique)) if unique.size else None, "max": float(np.max(unique)) if unique.size else None, "unique_count": int(unique.size), "all_values_are_integer_like": bool(np.all(np.isclose(unique, np.round(unique)))) if unique.size else None, "first_unique_values": [int(x) if float(x).is_integer() else float(x) for x in unique[:50]]})
    else:
        summary.update({"min": "skipped", "max": "skipped", "unique_count": "skipped", "all_values_are_integer_like": "skipped", "first_unique_values": []})
    return summary

def geometry_similarity(a, b):
    same_shape = tuple(a["shape"]) == tuple(b["shape"])
    same_zooms = bool(np.allclose(np.array(a["zooms"]), np.array(b["zooms"]), atol=1e-6))
    same_affine = bool(np.allclose(np.array(a["affine"]), np.array(b["affine"]), atol=1e-6))
    same_orientation = a["orientation"] == b["orientation"]
    return {"same_shape": same_shape, "same_zooms": same_zooms, "same_affine": same_affine, "same_orientation": same_orientation, "exact_match": same_shape and same_zooms and same_affine and same_orientation}

def main() -> int:
    ensure_project_dirs()
    files = discover_expected_files()
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "raw_dir": str(RAW_DIR), "files": {k: str(v) if v else None for k, v in files.items()}, "nifti_summaries": {}, "label_summaries": {}, "geometry_comparisons": {}, "recommendations": []}

    table = Table(title="NIfTI summaries")
    for col in ["Key", "Shape", "Voxel size", "Orient", "Dtype", "Unique", "Integer labels", "Size MB"]:
        table.add_column(col)

    nifti_keys = ["paxinos_atlas","sigma_reference","neurorat","neurorat_mri","neurorat_labels","waxholm_atlas","waxholm_labels_nii","waxholm_mask","waxholm_mri","waxholm_aligned_to_neurorat"]
    for key in nifti_keys:
        path = files.get(key)
        if not path:
            continue
        summary = load_nifti_summary(path)
        report["nifti_summaries"][key] = summary
        table.add_row(key, " × ".join(map(str, summary["shape"])), ", ".join(f"{z:g}" for z in summary["zooms"]), str(summary["orientation"]), str(summary["dtype"]), str(summary["unique_count"]), str(summary["all_values_are_integer_like"]), f"{summary['file_size_mb']:.2f}")
    console.print(table)

    for key, parser in [("paxinos_labels", parse_itksnap_labels), ("paxinos_labels_cortex", parse_cortex_labels), ("sigma_labels", parse_itksnap_labels)]:
        path = files.get(key)
        if path:
            report["label_summaries"][key] = summarize_labels(parser(path))

    pax = report["nifti_summaries"].get("paxinos_atlas")
    if pax:
        for candidate_key in ["sigma_reference","neurorat","neurorat_mri","neurorat_labels","waxholm_atlas","waxholm_labels_nii","waxholm_mri","waxholm_aligned_to_neurorat"]:
            cand = report["nifti_summaries"].get(candidate_key)
            if cand:
                report["geometry_comparisons"][f"paxinos_vs_{candidate_key}"] = geometry_similarity(pax, cand)

    report["recommendations"].append("No exact geometry match to Paxinos was found. Reference volume selection needs manual review." if not any(c.get("exact_match") for c in report["geometry_comparisons"].values()) else "At least one exact geometry match to Paxinos was found.")
    report["recommendations"].append("Paxinos label table parsed successfully. Next step: generate a BrainGlobe-compatible structures.json.")

    lines = ["Rat Paxinos BrainGlobe Builder - Input Inspection Report", "="*72, f"Generated: {report['generated_at']}", f"Raw data folder: {RAW_DIR}", ""]
    lines.append("NIfTI summaries"); lines.append("-"*72)
    for k, s in report["nifti_summaries"].items():
        lines += [f"[{k}]", f"  shape: {s['shape']}", f"  voxel_size: {s['zooms']}", f"  orientation: {s['orientation']}", f"  dtype: {s['dtype']}", f"  unique_count: {s['unique_count']}", f"  integer_like: {s['all_values_are_integer_like']}", ""]
    lines.append("Label summaries"); lines.append("-"*72)
    for k, s in report["label_summaries"].items():
        lines.append(f"[{k}] {s}")
    lines.append(""); lines.append("Recommendations"); lines.append("-"*72); lines += [f"- {x}" for x in report["recommendations"]]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "input_inspection_report.txt").write_text("\\n".join(lines), encoding="utf-8")
    (REPORTS_DIR / "input_inspection_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"\\n[green]Report written:[/green] {REPORTS_DIR / 'input_inspection_report.txt'}")
    console.print(f"[green]JSON written:[/green]   {REPORTS_DIR / 'input_inspection_report.json'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
