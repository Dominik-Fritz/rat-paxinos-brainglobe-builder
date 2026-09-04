from src import summarize_native_abba_diagnostics as summary


def test_compact_summary_distinguishes_source_gaps_from_export_losses():
    report = {"abba_reconstruction": {
        "renderer_backend": "native_abba_0.11",
        "native_backend_verified": True,
        "native_grid_diagnostics": {"native_array_shape_ap_si_lr": [589, 328, 470]},
        "source_plane_intensity_diagnostics": [
            {"source_id": 0, "nonzero_pixels": 10, "nonzero_mean": 20},
            {"source_id": 1, "nonzero_pixels": 0, "nonzero_mean": None},
            {"source_id": 2, "nonzero_pixels": 5, "nonzero_mean": 40},
        ],
        "output_plane_intensity_diagnostics": [
            {"source_id": 0, "nonzero_pixels": 8, "nonzero_mean": 10},
            {"source_id": 1, "nonzero_pixels": 0, "nonzero_mean": None},
            {"source_id": 2, "nonzero_pixels": 0, "nonzero_mean": None},
        ],
        "spatial_diagnostics": {
            "median_centroid_delta_si_lr_voxels": [1.0, 2.0],
            "median_centroid_delta_si_lr_um": [40.0, 80.0],
        },
    }}
    result = summary.summarize(report)
    assert result["source_blank_source_ids"] == [1]
    assert result["output_blank_source_ids"] == [1, 2]
    assert result["output_blank_despite_nonblank_source_ids"] == [2]
    assert result["output_to_source_nonzero_mean_ratio"]["median"] == 0.5
    assert result["median_centroid_delta_si_lr_voxels"] == [1.0, 2.0]


def test_compact_summary_handles_older_report_without_diagnostics():
    result = summary.summarize({"abba_reconstruction": {"renderer_backend": "native_abba_0.11"}})
    assert result["source_diagnostic_count"] == 0
    assert result["output_diagnostic_count"] == 0
    assert result["output_to_source_nonzero_mean_ratio"] == {"count": 0}
