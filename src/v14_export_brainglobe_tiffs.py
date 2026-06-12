from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import tifffile
from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()


def target_folder(name: str) -> Path:
    if name == "provisional":
        return provisional_folder()
    if name == "official":
        return official_candidate_folder()
    raise ValueError(f"Unknown target: {name}")


def load_nifti_array(path: Path) -> np.ndarray:
    img = nib.load(str(path))
    arr = np.asanyarray(img.dataobj)
    return np.asarray(arr)


def normalize_reference(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr)
    if arr.dtype == np.uint16:
        return arr
    arr = arr.astype(np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint16)
    mn = float(np.min(finite))
    mx = float(np.max(finite))
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint16)
    scaled = (arr - mn) / (mx - mn)
    scaled = np.clip(scaled, 0, 1)
    return (scaled * 65535).astype(np.uint16)


def normalize_annotation(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr)
    if np.max(arr) <= np.iinfo(np.uint16).max:
        return np.round(arr).astype(np.uint16)
    return np.round(arr).astype(np.uint32)


def export_tiffs(folder: Path) -> dict[str, Any]:
    reference_nii = folder / "reference.nii.gz"
    annotation_nii = folder / "annotation.nii.gz"
    reference_tiff = folder / "reference.tiff"
    annotation_tiff = folder / "annotation.tiff"

    if not reference_nii.exists():
        raise FileNotFoundError(f"Missing {reference_nii}")
    if not annotation_nii.exists():
        raise FileNotFoundError(f"Missing {annotation_nii}")

    ref = normalize_reference(load_nifti_array(reference_nii))
    ann = normalize_annotation(load_nifti_array(annotation_nii))

    # BrainGlobe core reads these via tifffile.imread. Keep array order identical
    # to the NIfTI data for now. If ABBA later complains visually, orientation is
    # the next boss monster, because of course it is.
    tifffile.imwrite(str(reference_tiff), ref, photometric="minisblack")
    tifffile.imwrite(str(annotation_tiff), ann, photometric="minisblack")

    metadata_path = folder / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["reference_file"] = "reference.tiff"
    metadata["annotation_file"] = "annotation.tiff"
    metadata["files"] = metadata.get("files", {})
    metadata["files"]["reference_tiff"] = "reference.tiff"
    metadata["files"]["annotation_tiff"] = "annotation.tiff"
    metadata["files"]["reference_nifti"] = "reference.nii.gz"
    metadata["files"]["annotation_nifti"] = "annotation.nii.gz"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "folder": str(folder),
        "reference_tiff": str(reference_tiff),
        "annotation_tiff": str(annotation_tiff),
        "reference_shape": list(ref.shape),
        "annotation_shape": list(ann.shape),
        "reference_dtype": str(ref.dtype),
        "annotation_dtype": str(ann.dtype),
        "reference_size_mb": reference_tiff.stat().st_size / (1024 * 1024),
        "annotation_size_mb": annotation_tiff.stat().st_size / (1024 * 1024),
        "passed": reference_tiff.exists() and annotation_tiff.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official"], required=True)
    args = parser.parse_args()

    folder = target_folder(args.target)
    result = export_tiffs(folder)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "result": result,
        "passed": result["passed"],
    }

    suffix = f"_{args.target}"
    (REPORTS_DIR / f"v14_tiff_export_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V14 BrainGlobe TIFF export report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"Folder: {result['folder']}",
        f"PASSED: {report['passed']}",
        "",
        f"reference.tiff: {result['reference_tiff']}",
        f"annotation.tiff: {result['annotation_tiff']}",
        f"reference_shape: {result['reference_shape']}",
        f"annotation_shape: {result['annotation_shape']}",
        f"reference_dtype: {result['reference_dtype']}",
        f"annotation_dtype: {result['annotation_dtype']}",
        f"reference_size_mb: {result['reference_size_mb']:.2f}",
        f"annotation_size_mb: {result['annotation_size_mb']:.2f}",
    ]
    text = "\n".join(lines)
    (REPORTS_DIR / f"v14_tiff_export_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v14_tiff_export_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V14 TIFF export ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Reference shape", str(result["reference_shape"]))
    table.add_row("Annotation shape", str(result["annotation_shape"]))
    table.add_row("Reference dtype", result["reference_dtype"])
    table.add_row("Annotation dtype", result["annotation_dtype"])
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / f'v14_tiff_export_report{suffix}.txt'}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
