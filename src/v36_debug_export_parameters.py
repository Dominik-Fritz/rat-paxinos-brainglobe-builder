#!/usr/bin/env python3
"""Compare native BDV export parameters against the measured Z defect.

Read-only probe. It restores the authoritative state and prepares the slices
exactly as the renderer does (select all, match-neighbour thickness, task
barrier), then runs ExportResampledSlicesToBDVSourceCommand several times with
different parameters and records only the resulting native Z profile. Nothing
is resampled onto the target grid, written to an atlas, or installed.

Established before this probe:
  - the slices going in are correct: 588 sources, uniform 0.04 mm centres,
    40 um thickness after the thickness action, all selected;
  - the export grid comes out exactly half a voxel off the slice lattice
    (offset 46.5 voxels), every plane landing on a slice boundary;
  - the exported volume holds 152/589 all-zero planes and ~0.487 of the source
    intensity, while Python's later resampling preserves 98.8% of it.

margin_z moves the export box origin and therefore the Z phase; interpolate
decides whether Z sampling blends at all. Varying them separates a phase
artefact from an interpolation artefact. All exports share one JVM session, so
the expensive state restore happens once.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

import native_abba_renderer as renderer
import native_abba_runtime as runtime
import ch03_nissl_pipeline as pipeline

VOXEL_MM = renderer.VOXEL_SIZE_MM

VARIANTS = [
    # Round 1 established that only the Z phase matters: margin 0 and 40 differ
    # by a whole voxel and behave identically (phase 0.5, ~0.48 intensity),
    # while 20 um lands the grid on the slice centres (phase 0, 1 empty plane,
    # 0.96 intensity). Round 2 compares the two margins that give phase 0 and
    # differ only in how much Z margin they add, because 20 um leaves the last
    # registered target AP just outside the exported stack.
    {"name": "margin20_interpolate", "margin_z": 20.0, "interpolate": True},
    {"name": "margin60_interpolate", "margin_z": 60.0, "interpolate": True},
]


def _native_volume_and_transform(ij, sac) -> tuple[np.ndarray, np.ndarray]:
    """Extract the exported raster as AP/SI/LR plus its XYZ source transform."""
    source = sac.getSpimSource()
    rai = source.getSource(0, 0)
    array = np.asarray(ij.py.from_java(rai))
    if array.ndim != 3:
        raise RuntimeError(f"native BDV source is not 3-D: {array.shape}")
    dimensions_xyz = tuple(int(rai.dimension(axis)) for axis in range(3))
    if tuple(array.shape) == dimensions_xyz[::-1]:
        volume = array
    elif tuple(array.shape) == dimensions_xyz:
        volume = array.transpose(2, 1, 0)
    else:
        raise RuntimeError(f"shape {array.shape} disagrees with XYZ {dimensions_xyz}")
    from scyjava import jimport
    transform = jimport("net.imglib2.realtransform.AffineTransform3D")()
    source.getSourceTransform(0, 0, transform)
    matrix = np.array([[float(transform.get(row, column)) for column in range(4)]
                       for row in range(3)])
    return volume, matrix


def _target_ap_coverage(volume: np.ndarray, matrix: np.ndarray,
                        target_ap: np.ndarray) -> dict:
    """Would the renderer's nearest-plane AP selection reach every target plane?

    Uses the exact arithmetic of _source_to_ap_si_lr so a margin that aligns the
    phase but drops the last registered section cannot pass unnoticed.
    """
    start = (float(matrix[2, 3]) - renderer.TARGET_ORIGIN_XYZ_MM[2]) / VOXEL_MM
    outside, empty_native = [], []
    for source_id, ap in enumerate(int(value) for value in target_ap):
        index = int(np.floor((ap - start) + 0.5))
        if index < 0 or index >= volume.shape[0]:
            outside.append({"source_id": source_id, "target_ap": ap, "native_ap_index": index})
        elif not np.any(volume[index]):
            empty_native.append({"source_id": source_id, "target_ap": ap,
                                 "native_ap_index": index})
    return {
        "native_ap_start_voxels": start,
        "registered_target_ap_count": int(len(target_ap)),
        "target_ap_outside_native_range_count": len(outside),
        "target_ap_outside_native_range": outside,
        "target_ap_on_empty_native_plane_count": len(empty_native),
        "target_ap_on_empty_native_plane": empty_native,
    }


def _profile(volume: np.ndarray, matrix: np.ndarray, first_slice_centre_mm: float) -> dict:
    planes = [renderer._signal_stats(volume[index]) for index in range(volume.shape[0])]
    empty = [index for index, stats in enumerate(planes) if not stats["nonzero_pixels"]]
    maxima = sorted(stats["maximum"] for stats in planes if stats["nonzero_pixels"])
    z0 = float(matrix[2, 3])
    offset_voxels = (first_slice_centre_mm - z0) / VOXEL_MM
    return {
        "native_shape_ap_si_lr": list(volume.shape),
        "native_source_transform_xyz": matrix.tolist(),
        "export_z0_mm": z0,
        "export_z_last_mm": z0 + VOXEL_MM * (volume.shape[0] - 1),
        "first_slice_centre_minus_export_z0_voxels": offset_voxels,
        "z_phase_fraction": offset_voxels % 1.0,
        "plane_count": int(volume.shape[0]),
        "empty_plane_count": len(empty),
        "empty_plane_indices": empty,
        "nonempty_plane_maximum_median": maxima[len(maxima) // 2] if maxima else None,
        "per_plane_intensity": [{"native_ap_index": index, **stats}
                                for index, stats in enumerate(planes)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args()
    package = Path(args.package).resolve()
    manifest = pipeline.load_package_manifest(package)
    authoritative = package / manifest["abba_state_file"]
    runtime.inspect_state(authoritative)
    paths = runtime.RuntimePaths()
    paths.create()
    work = Path(tempfile.mkdtemp(prefix="native-export-ab-", dir=paths.temporary))
    results = []
    try:
        import tifffile
        annotation = pipeline.orient_annotation(
            tifffile.imread(pipeline.find_annotation_tiff()), pipeline.find_annotation_tiff())
        target_ap, _ = pipeline.registered_target_ap_mapping(annotation)
        del annotation
        planes, source_diagnostics = renderer._single_plane_tiffs(work / "moving_sources")
        rebound = paths.reports / "rebound_state.abba"
        renderer.build_rebound_state(authoritative, planes, rebound)
        ij, _ = runtime.initialize_native_api(paths)
        abba, _ = renderer._open_fixed_abba(ij, renderer._atlas_name())
        renderer._restore_state_and_wait(abba, renderer._java_file(rebound))
        renderer._prepare_slices_for_export_and_wait(abba)

        slice_sources = list(abba.mp.getSlices())
        centres = sorted(float(item.getSlicingAxisPosition()) for item in slice_sources)
        first_centre = centres[0]
        thickness = sorted({round(float(item.getThicknessInMm()), 9) for item in slice_sources})

        source_maxima = sorted(item["maximum"] for item in source_diagnostics)
        source_median_maximum = source_maxima[len(source_maxima) // 2]

        for variant in VARIANTS:
            module = abba.export_resampled_slices_to_bdv_source(
                block_size_x=64, block_size_y=64, block_size_z=1, channels="0",
                downsample_x=1, downsample_y=1, downsample_z=1,
                image_name=f"ab_probe_{variant['name']}",
                interpolate=variant["interpolate"],
                margin_z=variant["margin_z"],
                n_threads=max(1, min(8, os.cpu_count() or 1)),
                px_size_micron_x=40.0, px_size_micron_y=40.0, px_size_micron_z=40.0,
                resolution_levels=1,
            )
            sacs = renderer._find_source_and_converters(module)
            if len(sacs) != 1:
                raise RuntimeError(f"{variant['name']}: expected one channel, got {len(sacs)}")
            volume, matrix = _native_volume_and_transform(ij, sacs[0])
            profile = _profile(volume, matrix, first_centre)
            median = profile["nonempty_plane_maximum_median"]
            profile["nonempty_maximum_median_over_source_median"] = (
                median / source_median_maximum if median else None
            )
            profile["target_ap_coverage"] = _target_ap_coverage(volume, matrix, target_ap)
            results.append({**variant, **profile})
            del volume

        report = {
            "probe": "native BDV export parameter comparison",
            "read_only": True,
            "abba_state_sha256": runtime.STATE_SHA256,
            "abba_version": runtime.ABBA_VERSION,
            "slice_count": len(slice_sources),
            "slice_centre_first_mm": first_centre,
            "slice_centre_last_mm": centres[-1],
            "slice_thickness_mm_unique": thickness,
            "source_plane_maximum_median": source_median_maximum,
            "variants": results,
        }
        destination = paths.reports / "export_parameter_probe.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(json.dumps({
        "report": str(destination),
        "slice_thickness_mm_unique": thickness,
        "variants": [{
            "name": item["name"], "margin_z": item["margin_z"],
            "interpolate": item["interpolate"],
            "plane_count": item["plane_count"],
            "empty_plane_count": item["empty_plane_count"],
            "z_phase_fraction": item["z_phase_fraction"],
            "intensity_ratio_to_source": item["nonempty_maximum_median_over_source_median"],
            "target_ap_outside_native_range_count":
                item["target_ap_coverage"]["target_ap_outside_native_range_count"],
            "target_ap_on_empty_native_plane_count":
                item["target_ap_coverage"]["target_ap_on_empty_native_plane_count"],
        } for item in results],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
