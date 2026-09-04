#!/usr/bin/env python3
"""Write a compact, shareable summary of the large native Ch03 JSON report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    array = np.asarray(values, dtype=np.float64)
    return {"count": int(array.size), "min": float(array.min()),
            "p10": float(np.percentile(array, 10)), "median": float(np.median(array)),
            "p90": float(np.percentile(array, 90)), "max": float(array.max())}


def summarize(report: dict) -> dict:
    reconstruction = report.get("abba_reconstruction", {})
    reconstruction = reconstruction.get("reconstruction", reconstruction)
    source = reconstruction.get("source_plane_intensity_diagnostics", [])
    output = reconstruction.get("output_plane_intensity_diagnostics", [])
    source_by_id = {int(item["source_id"]): item for item in source}
    output_by_id = {int(item["source_id"]): item for item in output}
    source_blank = sorted(i for i, item in source_by_id.items() if not item.get("nonzero_pixels"))
    output_blank = sorted(i for i, item in output_by_id.items() if not item.get("nonzero_pixels"))
    lost = sorted(i for i in output_blank if source_by_id.get(i, {}).get("nonzero_pixels", 0))
    dark_ratios = []
    for source_id in sorted(source_by_id.keys() & output_by_id.keys()):
        before = source_by_id[source_id].get("nonzero_mean")
        after = output_by_id[source_id].get("nonzero_mean")
        if before not in (None, 0) and after is not None:
            dark_ratios.append(float(after) / float(before))
    spatial = reconstruction.get("spatial_diagnostics", {})
    return {
        "renderer_backend": reconstruction.get("renderer_backend"),
        "native_backend_verified": reconstruction.get("native_backend_verified"),
        "visual_parity_status": reconstruction.get("visual_parity_status"),
        "source_sha256": reconstruction.get("source", {}).get("sha256"),
        "abba_state_sha256": reconstruction.get("abba_state_sha256"),
        "native_grid_diagnostics": reconstruction.get("native_grid_diagnostics"),
        "ap_sampling_policy": reconstruction.get("ap_sampling_policy"),
        "native_export_margin_z_um": reconstruction.get("native_export_margin_z_um"),
        "source_blank_source_ids": source_blank,
        "output_blank_source_ids": output_blank,
        "output_blank_despite_nonblank_source_ids": lost,
        "output_to_source_nonzero_mean_ratio": _quantiles(dark_ratios),
        "median_centroid_delta_si_lr_voxels": spatial.get("median_centroid_delta_si_lr_voxels"),
        "median_centroid_delta_si_lr_um": spatial.get("median_centroid_delta_si_lr_um"),
        "source_diagnostic_count": len(source),
        "output_diagnostic_count": len(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    source = root / "reports" / "ch03_nissl" / "ch03_nissl_report.json"
    if not source.is_file():
        raise FileNotFoundError(f"Native Ch03 report not found: {source}")
    summary = summarize(json.loads(source.read_text(encoding="utf-8")))
    folder = root / "reports" / "native_abba"
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "native_diagnostics_summary.json"
    text_path = folder / "native_diagnostics_summary.txt"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = ["Native ABBA compact diagnostics", "=" * 72]
    lines.extend(f"{key}: {value}" for key, value in summary.items())
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(text_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
