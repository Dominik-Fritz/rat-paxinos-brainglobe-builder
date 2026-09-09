#!/usr/bin/env python3
"""Write a compact, shareable summary of the large native Ch03 JSON report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


# Kept in step with native_abba_renderer.NATIVE_EXPORT_MARGIN_Z_UM (a test
# asserts they match). A report produced with the earlier 40 um margin came from
# a Z grid half a voxel off the slice centres, so its plane counts and
# intensities are not comparable with a current run.
CURRENT_EXPORT_MARGIN_Z_UM = 60.0


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
    alignment = reconstruction.get("alignment_diagnostics") or {}
    # Stage-2 evidence: ABBA's own exported Z profile, before Python resampling.
    grid = reconstruction.get("native_grid_diagnostics", {}) or {}
    native_planes = grid.get("native_plane_intensity_diagnostics", []) or []
    native_empty = sorted(int(item["native_ap_index"]) for item in native_planes
                          if not item.get("nonzero_pixels"))
    classification = reconstruction.get("blank_registered_plane_classification", {}) or {}
    ap_policy = reconstruction.get("ap_sampling_policy")
    margin = reconstruction.get("native_export_margin_z_um")
    current_sampling = (
        ap_policy == "nearest_native_plane_no_inter_slice_intensity_blending"
        and margin == CURRENT_EXPORT_MARGIN_Z_UM
    )
    return {
        "diagnostic_schema_status": "current" if current_sampling else "predates_ap_sampling_fix",
        "rerun_required_for_current_sampling": not current_sampling,
        "renderer_backend": reconstruction.get("renderer_backend"),
        "native_backend_verified": reconstruction.get("native_backend_verified"),
        "visual_parity_status": reconstruction.get("visual_parity_status"),
        "source_sha256": reconstruction.get("source", {}).get("sha256"),
        # Compare this between runs to judge reproducibility; output_sha256 also
        # covers the TIFF container and answers a different question.
        "output_content_sha256": reconstruction.get("output_content_sha256"),
        "abba_state_sha256": reconstruction.get("abba_state_sha256"),
        # Keep the scalar grid facts; the 589-plane Z profile and the 608-entry
        # selection map stay in the full report so this file remains readable.
        "native_grid_diagnostics": {
            key: value for key, value in (grid or {}).items()
            if key not in ("native_plane_intensity_diagnostics", "native_plane_selection")
        } or None,
        "native_transform_roundtrip": reconstruction.get("native_transform_roundtrip"),
        "native_slice_state_audit": reconstruction.get("native_slice_state_audit"),
        "java_dependencies": reconstruction.get("java_dependencies"),
        "java_dependency_overrides": reconstruction.get("java_dependency_overrides"),
        "ap_sampling_policy": ap_policy,
        "native_export_margin_z_um": margin,
        "source_blank_source_ids": source_blank,
        "output_blank_source_ids": output_blank,
        "output_blank_despite_nonblank_source_ids": lost,
        "native_plane_count": len(native_planes),
        "native_empty_plane_count": len(native_empty),
        "native_empty_ap_indices": native_empty,
        "unused_native_ap_indices": grid.get("unused_native_ap_indices"),
        "blank_plane_stage_counts": {
            key: classification.get(key) for key in
            ("blank_registered_plane_count", "empty_waxholm_source_count",
             "native_export_empty_count", "sampling_loss_count", "no_native_plane_count")
        } if classification else None,
        "output_to_source_nonzero_mean_ratio": _quantiles(dark_ratios),
        # Centroid deltas compare unequal supports and overstate SI; the
        # correlation-based alignment below is the number to trust.
        "median_centroid_delta_si_lr_voxels": spatial.get("median_centroid_delta_si_lr_voxels"),
        "median_centroid_delta_si_lr_um": spatial.get("median_centroid_delta_si_lr_um"),
        "alignment_median_shift_si_lr_um": alignment.get("median_shift_si_lr_um"),
        "alignment_iqr_si_lr_voxels": alignment.get("iqr_si_lr_voxels"),
        "alignment_max_abs_shift_um": alignment.get("max_abs_shift_um"),
        "alignment_warning_threshold_um": alignment.get("warning_threshold_um"),
        "alignment_within_threshold": reconstruction.get("alignment_within_threshold"),
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
