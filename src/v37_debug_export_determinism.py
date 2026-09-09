#!/usr/bin/env python3
"""Test whether the native Ch03 render is bit-reproducible.

Read-only probe. It restores the authoritative state once, prepares the slices
exactly as the renderer does, then runs the production export twice with
identical parameters and hashes the result at two points:

  1. the native BDV volume as ABBA exports it, and
  2. the finished target volume, reproducing the renderer's tail
     (_source_to_ap_si_lr -> uint16 -> duplicate the anterior edge plane),

so the second hash is directly comparable with `output_sha256` in
reports/ch03_nissl/ch03_nissl_report.json.

That separates three questions:
  * do two exports in ONE session agree?           -> intra-session determinism
  * does this session agree with the last build?   -> cross-session determinism
  * if not, is the difference numerical noise or structural?

Motivation: two builds with identical pinned inputs and an unchanged pixel path
produced different output_sha256 values (14fa96c8..., a29245d3...) while their
geometry and intensity statistics were identical. Nothing is installed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import tifffile

import native_abba_renderer as renderer
import native_abba_runtime as runtime
import ch03_nissl_pipeline as pipeline

EXPORT_RUNS = 2


def _sha256_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _compare(first: np.ndarray, second: np.ndarray) -> dict:
    if first.shape != second.shape:
        return {"identical": False, "reason": f"shape {first.shape} vs {second.shape}"}
    difference = first.astype(np.int64) - second.astype(np.int64)
    differing = np.count_nonzero(difference)
    if not differing:
        return {"identical": True, "differing_voxels": 0}
    planes = np.flatnonzero(np.any(difference != 0, axis=(1, 2)))
    return {
        "identical": False,
        "differing_voxels": int(differing),
        "total_voxels": int(first.size),
        "differing_fraction": float(differing) / float(first.size),
        "max_abs_difference": int(np.max(np.abs(difference))),
        "mean_abs_difference_over_differing": float(np.mean(np.abs(difference[difference != 0]))),
        "affected_plane_count": int(planes.size),
        "first_affected_planes": planes[:10].tolist(),
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
    work = Path(tempfile.mkdtemp(prefix="native-determinism-", dir=paths.temporary))
    try:
        annotation_path = pipeline.find_annotation_tiff()
        labels = pipeline.orient_annotation(tifffile.imread(annotation_path), annotation_path)
        target_ap, duplicate_ap = pipeline.registered_target_ap_mapping(labels)
        del labels

        planes, _ = renderer._single_plane_tiffs(work / "moving_sources")
        rebound = paths.reports / "rebound_state.abba"
        renderer.build_rebound_state(authoritative, planes, rebound)
        ij, _ = runtime.initialize_native_api(paths)
        abba, _ = renderer._open_fixed_abba(ij, renderer._atlas_name())
        renderer._restore_state_and_wait(abba, renderer._java_file(rebound))
        renderer._prepare_slices_for_export_and_wait(abba)

        native_hashes, output_hashes = [], []
        native_volumes, output_volumes = [], []
        for run in range(EXPORT_RUNS):
            module = abba.export_resampled_slices_to_bdv_source(
                block_size_x=64, block_size_y=64, block_size_z=1, channels="0",
                downsample_x=1, downsample_y=1, downsample_z=1,
                image_name=f"determinism_probe_{run}", interpolate=True,
                margin_z=renderer.NATIVE_EXPORT_MARGIN_Z_UM,
                n_threads=max(1, min(8, os.cpu_count() or 1)),
                px_size_micron_x=40.0, px_size_micron_y=40.0, px_size_micron_z=40.0,
                resolution_levels=1,
            )
            sacs = renderer._find_source_and_converters(module)
            if len(sacs) != 1:
                raise RuntimeError(f"run {run}: expected one channel, got {len(sacs)}")
            source = sacs[0].getSpimSource()
            rai = source.getSource(0, 0)
            native = np.asarray(ij.py.from_java(rai))
            dimensions = tuple(int(rai.dimension(axis)) for axis in range(3))
            if tuple(native.shape) == dimensions:
                native = native.transpose(2, 1, 0)
            native_volumes.append(np.array(native))
            native_hashes.append(_sha256_array(native))

            # Reproduce the renderer's tail so the hash is comparable with the build.
            target = renderer._source_to_ap_si_lr(ij, sacs[0]).astype(np.uint16, copy=False)
            target[duplicate_ap] = target[target_ap[0]]
            output_volumes.append(target)
            output_hashes.append(_sha256_array(target))
            print(f"run {run}: native={native_hashes[-1][:16]}... output={output_hashes[-1][:16]}...")

        report_path = pipeline.REPORT_JSON
        build_output_sha = None
        if report_path.is_file():
            build_output_sha = json.loads(report_path.read_text(encoding="utf-8")) \
                .get("abba_reconstruction", {}).get("output_sha256")

        result = {
            "probe": "native export bit-reproducibility",
            "read_only": True,
            "export_runs": EXPORT_RUNS,
            "n_threads": max(1, min(8, os.cpu_count() or 1)),
            "margin_z_um": renderer.NATIVE_EXPORT_MARGIN_Z_UM,
            "native_volume_sha256": native_hashes,
            "output_volume_sha256": output_hashes,
            "intra_session_native_identical": len(set(native_hashes)) == 1,
            "intra_session_output_identical": len(set(output_hashes)) == 1,
            "intra_session_native_diff": _compare(native_volumes[0], native_volumes[1]),
            "intra_session_output_diff": _compare(output_volumes[0], output_volumes[1]),
            "last_build_output_sha256": build_output_sha,
            "matches_last_build": bool(build_output_sha) and build_output_sha in output_hashes,
        }
        destination = paths.reports / "export_determinism_probe.json"
        destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "probe"}, indent=2))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
