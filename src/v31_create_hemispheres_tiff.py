from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import tifffile
from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()


def folder_for(target: str) -> Path:
    if target == "provisional":
        return provisional_folder()
    if target == "official":
        return official_candidate_folder()
    if target == "installed":
        return Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"
    raise ValueError(target)


def lr_axis_from_orientation(orientation: str | None, shape: tuple[int, int, int]) -> int:
    """Return the axis that represents left/right according to metadata orientation.

    LPI -> axis 0. V32.2 PIL -> axis 2.
    If metadata is missing or weird, use a conservative fallback and report it.
    """
    if isinstance(orientation, str) and len(orientation) >= 3:
        for i, code in enumerate(orientation[:3].upper()):
            if code in {"L", "R"}:
                return i
    # Fallback: the old V32 atlas used axis 0 as LR.
    return 0


def make_hemispheres(annotation: np.ndarray, lr_axis: int) -> np.ndarray:
    """Create a masked BrainGlobe/ABBA hemisphere label image.

    Values:
      0 = outside the atlas annotation mask / undefined background
      1 = one side of the left-right axis
      2 = other side of the left-right axis

    Earlier V31 filled the entire rectangular volume with 1/2, which can behave like a third
    always-present display layer in ABBA. This version restricts hemispheres to annotation > 0.
    The universe survives one less phantom channel. Barely.
    """
    if annotation.ndim != 3:
        raise RuntimeError(f"Expected 3D annotation, got shape {annotation.shape}")
    mask = np.asarray(annotation) > 0
    hemi = np.zeros(annotation.shape, dtype=np.uint8)
    mid = annotation.shape[lr_axis] // 2

    side1 = [slice(None)] * 3
    side2 = [slice(None)] * 3
    side1[lr_axis] = slice(0, mid)
    side2[lr_axis] = slice(mid, annotation.shape[lr_axis])

    s1 = tuple(side1)
    s2 = tuple(side2)
    hemi[s1] = np.where(mask[s1], 1, 0)
    hemi[s2] = np.where(mask[s2], 2, 0)
    return hemi


def patch_metadata(folder: Path, lr_axis: int) -> dict:
    metadata_path = folder / "metadata.json"
    if not metadata_path.exists():
        return {"metadata_exists": False, "patched": False}

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["hemispheres_file"] = "hemispheres.tiff"
    metadata["hemispheres_note"] = (
        "V32.2 generated a masked left/right hemisphere image for ABBA compatibility. "
        "Values: 0 outside annotation, 1/2 within annotation split along the metadata LR axis."
    )
    metadata["hemispheres_lr_axis"] = lr_axis
    metadata["files"] = metadata.get("files", {})
    # Do not list hemispheres as a normal file/channel entry. BrainGlobe/ABBA know the dedicated
    # hemispheres_file key; putting it in files made the layer situation messier than necessary.
    metadata["files"].pop("hemispheres", None)
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"metadata_exists": True, "patched": True, "lr_axis": lr_axis}


def run(target: str) -> dict:
    folder = folder_for(target)
    annotation_tiff = folder / "annotation.tiff"
    reference_tiff = folder / "reference.tiff"
    hemispheres_tiff = folder / "hemispheres.tiff"
    metadata_path = folder / "metadata.json"

    if not annotation_tiff.exists():
        raise FileNotFoundError(annotation_tiff)
    if not reference_tiff.exists():
        raise FileNotFoundError(reference_tiff)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    orientation = metadata.get("orientation")

    ann = tifffile.imread(str(annotation_tiff))
    ref = tifffile.imread(str(reference_tiff))
    if ann.ndim != 3:
        raise RuntimeError(f"Expected 3D annotation.tiff, got shape {ann.shape}")
    if tuple(ref.shape) != tuple(ann.shape):
        raise RuntimeError(f"reference/annotation shape mismatch: {ref.shape} vs {ann.shape}")

    lr_axis = lr_axis_from_orientation(orientation, tuple(ann.shape))
    hemi = make_hemispheres(ann, lr_axis=lr_axis)
    tifffile.imwrite(str(hemispheres_tiff), hemi, photometric="minisblack")

    meta_report = patch_metadata(folder, lr_axis)

    unique = sorted(np.unique(hemi).astype(int).tolist())
    counts = {str(int(v)): int((hemi == v).sum()) for v in unique}
    nonzero_fraction = float(np.count_nonzero(hemi) / hemi.size) if hemi.size else 0.0
    annotation_nonzero_fraction = float(np.count_nonzero(ann) / ann.size) if ann.size else 0.0

    passed = (
        hemispheres_tiff.exists()
        and tuple(tifffile.imread(str(hemispheres_tiff)).shape) == tuple(ref.shape)
        and set(unique).issubset({0, 1, 2})
        and {1, 2}.issubset(set(unique))
        and nonzero_fraction <= max(annotation_nonzero_fraction + 0.001, 0.001)
    )

    return {
        "target": target,
        "folder": str(folder),
        "reference_tiff": str(reference_tiff),
        "annotation_tiff": str(annotation_tiff),
        "hemispheres_tiff": str(hemispheres_tiff),
        "shape": list(ref.shape),
        "orientation": orientation,
        "lr_axis": lr_axis,
        "dtype": str(hemi.dtype),
        "unique_values": unique,
        "counts": counts,
        "nonzero_fraction": nonzero_fraction,
        "annotation_nonzero_fraction": annotation_nonzero_fraction,
        "metadata": meta_report,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official", "installed"], required=True)
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    result = run(args.target)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "passed": result["passed"],
    }

    suffix = "_" + args.target
    (REPORTS_DIR / f"v31_hemispheres_report{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        "V31/V32.2 masked hemispheres.tiff compatibility report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"PASSED: {report['passed']}",
        "",
    ]
    for k, v in result.items():
        lines.append(f"- {k}: {v}")

    text = "\n".join(lines)
    (REPORTS_DIR / f"v31_hemispheres_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v31_hemispheres_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V31/V32.2 masked hemispheres.tiff ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Shape", str(result["shape"]))
    table.add_row("Orientation", str(result["orientation"]))
    table.add_row("LR axis", str(result["lr_axis"]))
    table.add_row("unique", str(result["unique_values"]))
    table.add_row("nonzero fraction", f"{result['nonzero_fraction']:.6f}")
    console.print(table)

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
