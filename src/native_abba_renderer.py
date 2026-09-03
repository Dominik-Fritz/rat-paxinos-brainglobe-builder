"""Native ABBA 0.11/BigWarp Ch03 renderer.

The only spatial transforms evaluated here are Java transforms restored by ABBA.
Python is limited to deterministic source rebinding, array transfer, AP placement,
and transactional file/report handling.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ch03_nissl_pipeline as pipeline
from abba_nissl import NisslBuildError
import native_abba_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
TARGET_SHAPE = (608, 286, 409)  # AP, SI, LR
VOXEL_SIZE_MM = 0.04
# The vendored AbbaMap creates the fixed BrainGlobe source with a scale-only
# AffineTransform3D: voxel (0, 0, 0) is world (0, 0, 0).  The native export's
# SourceTransform is expressed in that same fixed-atlas frame.  Do not add a
# guessed coronal centring translation here; doing so moved the atlas centre to
# the lower-right corner and clipped most registered sections.
TARGET_ORIGIN_XYZ_MM = (0.0, 0.0, 0.0)


def classify_native_failure(exc: BaseException) -> NisslBuildError:
    if isinstance(exc, MemoryError):
        return NisslBuildError("NATIVE_MEMORY", "native ABBA rendering exhausted memory")
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return NisslBuildError("NATIVE_ENOSPC", "native ABBA runtime volume has insufficient free space")
    text = str(exc)
    if "project.qpproj" in text or "G:\\nissl_registration" in text:
        return NisslBuildError(
            "SOURCE_REBINDING",
            "SacBigWarp2DRegistration source_ids 0..587 (Waxholm AP 189..776) still attempted "
            "to open the historical QuPath project. The portable BIOFORMATS rebinding was not "
            "accepted by ABBA 0.11; inspect reports/native_abba/rebound_state.abba.",
        )
    return NisslBuildError("NATIVE_ABBA_RENDER", text)


def _single_plane_tiffs(source: Path, folder: Path) -> list[Path]:
    """Materialize temporary named planes; never use directory ordering as identity."""
    folder.mkdir(parents=True, exist_ok=True)
    volume = tifffile.memmap(source)
    paths: list[Path] = []
    try:
        if tuple(volume.shape) != (1024, 512, 512):
            raise NisslBuildError("WHS_SOURCE_SHAPE", f"expected (1024, 512, 512), got {volume.shape}")
        for source_id, waxholm_ap in enumerate(range(189, 777)):
            path = folder / f"whs_nissl_40um_ap_{waxholm_ap}.tiff"
            tifffile.imwrite(path, np.asarray(volume[waxholm_ap]), photometric="minisblack")
            paths.append(path)
            if source_id + 189 != waxholm_ap:
                raise AssertionError("source/AP identity changed")
    finally:
        pipeline.close_memmap(volume)
    return paths


def _portable_opener(original: dict, path: Path) -> dict:
    """Preserve serialized calibration options and replace only external identity."""
    rebound = dict(original)
    rebound["type"] = "BIOFORMATS"
    rebound["location"] = str(path.resolve())
    # Each materialized TIFF contains one Bio-Formats series.
    rebound["id"] = 0
    rebound["nChannels"] = 1
    rebound["splitRGB"] = False
    return rebound


def build_rebound_state(state_path: Path, plane_paths: list[Path], destination: Path) -> dict:
    """Replace the historical QuPath loader while preserving ABBA actions/affines byte-for-byte."""
    if len(plane_paths) != 588:
        raise NisslBuildError("SOURCE_REBINDING", f"expected 588 explicit planes, got {len(plane_paths)}")
    runtime.inspect_state(state_path)
    with zipfile.ZipFile(state_path) as source_zip:
        xml = source_zip.read("_bdvdataset_0.xml").decode("utf-8")
        match = re.search(r"<openers>(.*?)</openers>", xml, re.DOTALL)
        if not match:
            raise NisslBuildError("SOURCE_REBINDING", "_bdvdataset_0.xml has no serialized openers")
        openers = json.loads(match.group(1))
        sources = json.loads(source_zip.read("sources.json"))
        setup_ids = [int(item["sac"]["viewsetup"]) for item in sources]
        if len(setup_ids) != 588 or setup_ids != list(range(197, 785)):
            raise NisslBuildError("SOURCE_REBINDING", f"unexpected BDV viewsetup mapping: {setup_ids[:3]}..{setup_ids[-3:]}")
        if max(setup_ids) >= len(openers):
            raise NisslBuildError("SOURCE_REBINDING", f"viewsetup {max(setup_ids)} exceeds {len(openers)} XML openers")
        # The historical dataset has 998 setups; sources.json selects exactly
        # setups 197..784. Replace every opener so no lazy access can ever
        # reach QuPath, while binding the selected setups explicitly by ID.
        rebound = [_portable_opener(opener, plane_paths[0]) for opener in openers]
        for source_id, setup_id in enumerate(setup_ids):
            rebound[setup_id] = _portable_opener(openers[setup_id], plane_paths[source_id])
        portable_xml = xml[:match.start(1)] + json.dumps(rebound, separators=(",", ":")) + xml[match.end(1):]
        if "G:\\\\nissl_registration" in portable_xml or "project.qpproj" in portable_xml:
            raise NisslBuildError("SOURCE_REBINDING", "historical QuPath path survived rebinding")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("sources.json", source_zip.read("sources.json"))
            output.writestr("state.json", source_zip.read("state.json"))
            output.writestr("_bdvdataset_0.xml", portable_xml.encode("utf-8"))
    return {"type": "BIOFORMATS temporary TIFF rebinding", "source_ids": [0, 587],
            "waxholm_ap": [189, 776], "persistent_plane_copies": False,
            "rebound_state": str(destination), "sha256": runtime.sha256(destination)}


def _java_file(path: Path):
    from scyjava import jimport
    return jimport("java.io.File")(str(path))


def _find_source_and_converters(module) -> list:
    """Discover command outputs by Java type, never by an assumed output key."""
    values = list(module.getOutputs().values())
    found: list = []
    pending = list(values)
    while pending:
        value = pending.pop(0)
        if value is None:
            continue
        if hasattr(value, "getSpimSource"):
            found.append(value)
            continue
        if isinstance(value, (str, bytes)):
            continue
        try:
            pending.extend(list(value))
        except (TypeError, AttributeError):
            pass
    if not found:
        keys = [str(key) for key in module.getOutputs().keySet()]
        raise NisslBuildError("NATIVE_EXPORT_OUTPUT", f"no SourceAndConverter in outputs {keys}")
    return found



def _find_multipositioner(module, fallback):
    """Use the MultiSlicePositioner returned by native ZIP import, if exposed."""
    candidates = list(module.getOutputs().values())
    for value in candidates:
        pending = [value]
        while pending:
            item = pending.pop(0)
            if item is None:
                continue
            if hasattr(item, "getSlices") and hasattr(item, "selectSlice"):
                return item
            if isinstance(item, (str, bytes)):
                continue
            try:
                pending.extend(list(item))
            except (TypeError, AttributeError):
                pass
    # Some ABBA builds mutate the currently opened positioner and expose only
    # a success output. Accept that only when it really contains all slices.
    if fallback is not None and int(fallback.getSlices().size()) == 588:
        return fallback
    keys = [str(key) for key in module.getOutputs().keySet()]
    raise NisslBuildError("NATIVE_STATE_LOAD", f"ZIP import returned no MultiSlicePositioner; outputs={keys}")

def _source_to_ap_si_lr(ij, sac) -> np.ndarray:
    """Resample the native BDV raster onto the fixed atlas voxel centres.

    ABBA's native export is cropped and may start between fixed-atlas voxel
    centres.  The TPS has already been evaluated by Java at this point; this
    step only performs an explicit linear change of sampling grid.
    """
    source = sac.getSpimSource()
    rai = source.getSource(0, 0)
    converted = ij.py.from_java(rai)
    array = np.asarray(converted)
    if array.ndim != 3:
        raise NisslBuildError("NATIVE_EXPORT_SHAPE", f"native BDV source is not 3-D: {array.shape}")
    dimensions_xyz = tuple(int(rai.dimension(axis)) for axis in range(3))
    if tuple(array.shape) == dimensions_xyz[::-1]:
        source_ap_si_lr = array
    elif tuple(array.shape) == dimensions_xyz:
        source_ap_si_lr = array.transpose(2, 1, 0)
    else:
        raise NisslBuildError(
            "NATIVE_EXPORT_AXES",
            f"PyImageJ shape {array.shape} disagrees with BDV XYZ dimensions {dimensions_xyz}",
        )

    from scyjava import jimport
    transform = jimport("net.imglib2.realtransform.AffineTransform3D")()
    source.getSourceTransform(0, 0, transform)
    matrix = np.array([[float(transform.get(row, column)) for column in range(4)] for row in range(3)])
    expected_scale_mm = VOXEL_SIZE_MM
    linear = matrix[:, :3]
    if not np.allclose(linear, np.diag([expected_scale_mm] * 3), rtol=0, atol=1e-9):
        raise NisslBuildError(
            "NATIVE_EXPORT_GRID",
            f"native output must have axis-aligned 0.04-mm XYZ transform, got {matrix.tolist()}",
        )
    target_origin_xyz = np.asarray(TARGET_ORIGIN_XYZ_MM, dtype=np.float64)
    starts_xyz = (matrix[:, 3] - target_origin_xyz) / expected_scale_mm
    # Decimal millimetre translations such as -8.14 cannot be represented
    # exactly in binary. Snap only values already numerically equal to an
    # integer voxel; preserve genuine half-voxel offsets for interpolation.
    nearest = np.rint(starts_xyz)
    starts_xyz = np.where(np.isclose(starts_xyz, nearest, rtol=0, atol=1e-9), nearest, starts_xyz)
    starts = starts_xyz[::-1]  # AP, SI, LR
    overlaps = [
        max(0.0, min(float(source_size - 1), float(target_size - 1 - start))) >=
        min(float(source_size - 1), max(0.0, float(-start)))
        for start, source_size, target_size in zip(starts, source_ap_si_lr.shape, TARGET_SHAPE)
    ]
    if not all(overlaps):
        raise NisslBuildError(
            "NATIVE_EXPORT_BOUNDS",
            f"native source {source_ap_si_lr.shape} at AP/SI/LR origin {starts.tolist()} misses {TARGET_SHAPE}",
        )

    # Target voxel i is at its explicit atlas world origin + 0.04*i; source
    # voxel j is at 0.04*j+translation.  Render one AP
    # plane at a time to keep peak memory bounded and make interpolation and
    # edge behaviour explicit.
    from scipy.ndimage import affine_transform
    target = np.zeros(TARGET_SHAPE, dtype=source_ap_si_lr.dtype)
    identity = np.eye(3, dtype=np.float64)
    for ap in range(TARGET_SHAPE[0]):
        offset = np.array([ap - starts[0], -starts[1], -starts[2]], dtype=np.float64)
        plane = affine_transform(
            source_ap_si_lr,
            identity,
            offset=offset,
            output_shape=(1, TARGET_SHAPE[1], TARGET_SHAPE[2]),
            output=source_ap_si_lr.dtype,
            order=1,
            mode="constant",
            cval=0,
            prefilter=False,
        )
        target[ap] = plane[0]
    return target


def _atlas_name() -> str:
    for folder in pipeline.atlas_candidates():
        metadata = folder / "metadata.json"
        if metadata.is_file():
            data = json.loads(metadata.read_text(encoding="utf-8"))
            return str(data.get("atlas_name") or data.get("name") or "paxinos_watson_rat_40um")
    raise NisslBuildError("FIXED_SOURCE", "built Paxinos atlas metadata was not found")


def _registered_blank_planes(volume: np.ndarray, target_ap: np.ndarray) -> list[int]:
    """Report zero-valued planes without mistaking image content for I/O failure.

    Zero is a valid intensity/background value.  An all-zero native result can
    therefore be important visual-validation evidence, but it cannot prove
    that a source was not rendered.  State/source/API checks establish backend
    provenance; this diagnostic must not turn a pending test installation into
    a failed build or synthesize replacement pixels.
    """
    return [int(value) for value in target_ap[~np.any(volume[target_ap] != 0, axis=(1, 2))]]



class _AbbaAtlasView:
    """Expose the already AP/SI/LR arrays in ABBA's required ASR convention."""
    def __init__(self, atlas):
        self._atlas = atlas
        self.orientation = "asr"
        self.metadata = dict(atlas.metadata)
        self.metadata["orientation"] = "asr"

    def __getattr__(self, name):
        return getattr(self._atlas, name)


def _open_fixed_abba(ij, atlas_name: str):
    """Create the fixed BrainGlobe atlas using the exact vendored adapter."""
    from brainglobe_atlasapi import BrainGlobeAtlas
    from abba_python import Abba
    from abba_python.abba_atlas import AbbaAtlas
    bg_atlas = BrainGlobeAtlas(atlas_name)
    original_orientation = str(bg_atlas.orientation).lower()
    shape = tuple(int(value) for value in bg_atlas.annotation.shape)
    if shape != TARGET_SHAPE:
        raise NisslBuildError("FIXED_SOURCE", f"Paxinos annotation must be AP/SI/LR {TARGET_SHAPE}, got {shape}")
    runtime_view = _AbbaAtlasView(bg_atlas)
    fixed_atlas = AbbaAtlas(runtime_view, ij)
    fixed_atlas.initialize(None, None)
    Abba.opened_atlases[atlas_name] = fixed_atlas
    abba = Abba(atlas_name=atlas_name, ij=ij, x_axis="RL", y_axis="SI", z_axis="AP",
                headless=True, print_config=False, log_level="INFO")
    return abba, {"atlas_name": atlas_name, "shape_ap_si_lr": list(shape),
                  "installed_orientation": original_orientation,
                  "native_abba_orientation": "asr", "array_permutation_applied": False}

def render_native(package_path: str) -> dict:
    package = Path(package_path).resolve()
    manifest = pipeline.load_package_manifest(package)
    state_path = package / manifest["abba_state_file"]
    runtime.inspect_state(state_path)
    source_path, source_report = pipeline.find_waxholm_source(manifest)
    annotation_path = pipeline.find_annotation_tiff()
    labels = pipeline.orient_annotation(tifffile.imread(annotation_path), annotation_path)
    target_ap, duplicate_ap = pipeline.registered_target_ap_mapping(labels)
    paths = runtime.RuntimePaths()
    paths.create()
    work = Path(tempfile.mkdtemp(prefix="native-render-", dir=paths.temporary))
    try:
        planes = _single_plane_tiffs(source_path, work / "moving_sources")
        rebound_path = paths.reports / "rebound_state.abba"
        binding = build_rebound_state(state_path, planes, rebound_path)
        ij, _ = runtime.initialize_native_api(paths)
        abba, fixed_source_report = _open_fixed_abba(ij, _atlas_name())
        # This authoritative `.abba` is ABBA's three-member project state
        # (sources.json, state.json, BDV XML), not a "standard ZIP export".
        # ImportStdZipStateCommand expects a different interchange format with
        # meta.json.  Use the vendored state_load API so ABBA restores its own
        # project/source serialization natively.
        loaded = abba.state_load(_java_file(rebound_path))
        if not bool(loaded):
            raise NisslBuildError("NATIVE_STATE_LOAD", "ABBAStateLoadCommand reported failure")
        if int(abba.get_n_slices()) != 588:
            raise NisslBuildError("NATIVE_STATE_LOAD", f"expected 588 slices, got {abba.get_n_slices()}")
        abba.select_all_slices()
        # The serialized sources are 1-um-thick 2-D planes separated by 40 um.
        # A volumetric BDV export otherwise contains empty Z planes depending
        # on grid phase.  This native ABBA command changes only display/export
        # thickness so neighbouring registered sections meet; it does not
        # alter any saved registration transform or landmark.
        abba.set_slices_thickness_match_neighbors()
        module = abba.export_resampled_slices_to_bdv_source(
            block_size_x=64, block_size_y=64, block_size_z=1, channels="0",
            downsample_x=1, downsample_y=1, downsample_z=1,
            image_name="native_abba_0.11_waxholm_nissl", interpolate=True,
            margin_z=0.0, n_threads=max(1, min(8, os.cpu_count() or 1)),
            px_size_micron_x=40.0, px_size_micron_y=40.0, px_size_micron_z=40.0,
            resolution_levels=1,
        )
        sacs = _find_source_and_converters(module)
        if len(sacs) != 1:
            raise NisslBuildError("NATIVE_EXPORT_OUTPUT", f"expected one channel, got {len(sacs)}")
        native_volume = _source_to_ap_si_lr(ij, sacs[0]).astype(np.uint16, copy=False)
        # Enforce the validated sequence edge policy without altering anatomy.
        native_volume[duplicate_ap] = native_volume[target_ap[0]]
        blank_registered = _registered_blank_planes(native_volume, target_ap)
        temporary = pipeline.ACTIVE_PATH.with_suffix(".tiff.partial")
        tifffile.imwrite(temporary, native_volume, bigtiff=True)
        pipeline.activate_validated_tiff(temporary, pipeline.ACTIVE_PATH)
        report = {
            "renderer_backend": "native_abba_0.11", "native_backend_verified": True,
            "visual_parity_status": "pending", "release_eligible": False,
            "source": source_report, "source_binding": binding, "fixed_source": fixed_source_report,
            "abba_state_sha256": runtime.STATE_SHA256, "abba_version": runtime.ABBA_VERSION,
            "source_count": 588, "slice_state_count": 588, "mapped_plane_count": 588,
            "transform_types": runtime.inspect_state(state_path)["action_types"],
            "waxholm_ap_range": [189, 776], "ap_direction": "anterior-to-posterior",
            "target_shape_ap_si_lr": list(TARGET_SHAPE), "target_voxel_um": 40.0,
            "target_origin_xyz_mm": list(TARGET_ORIGIN_XYZ_MM),
            "slice_thickness_policy": "native_match_neighbors_for_export",
            "blank_registered_plane_count": len(blank_registered),
            "blank_registered_ap_indices": blank_registered,
            "coverage_status": "review_required" if blank_registered else "complete",
            "warnings": ([
                f"Native output has {len(blank_registered)} all-zero registered AP planes; "
                "retained unchanged for visual validation."
            ] if blank_registered else []),
            "actual_target_ap_indices": [int(value) for value in target_ap],
            "duplicated_anterior_target_ap": int(duplicate_ap),
            "stack_order": "anterior-to-posterior", "target_sequence_offset": 1,
            "anterior_edge_policy": "duplicate_first_registered_plane",
            "output_sha256": pipeline.sha256_file(pipeline.ACTIVE_PATH),
            "legacy_registered_stack_used": False,
        }
        pipeline.write_report({"abba_reconstruction": report})
        pipeline.install_channel(report)
        report["candidate_archive"] = str(pipeline.repack_candidate())
        return report
    except Exception as exc:
        if isinstance(exc, NisslBuildError):
            raise
        raise classify_native_failure(exc) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: Iterable[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args(argv)
    try:
        report = render_native(args.package)
        print(json.dumps({key: report[key] for key in ("renderer_backend", "native_backend_verified",
              "visual_parity_status", "release_eligible")}, indent=2))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "NATIVE_ABBA_RENDER")
        try:
            pipeline.write_report({
                "native_failure": {"error_code": str(code), "message": str(exc),
                                   "renderer_backend": "native_abba_0.11",
                                   "native_backend_verified": False,
                                   "visual_parity_status": "not_applicable",
                                   "release_eligible": False}
            })
        except Exception as report_exc:
            print(f"WARNING: could not write native failure report: {report_exc}", file=sys.stderr)
        print(f"ERROR [{code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
