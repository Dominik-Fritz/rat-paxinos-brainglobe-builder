"""Native ABBA 0.11/BigWarp Ch03 renderer.

The only spatial transforms evaluated here are Java transforms restored by ABBA.
Python is limited to deterministic source rebinding, array transfer, AP placement,
and transactional file/report handling.
"""
from __future__ import annotations

import errno
import hashlib
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
# The saved BigWarp registrations live on a coronal canvas centred at world
# LR=0/SI=0 (registration px=-9.4, py=-6.56).  BrainGlobe has no origin field,
# so centre the target voxel *centres* explicitly. This same origin is supplied
# to the fixed ABBA Source and to post-export sampling; changing only one side
# caused the previous large right/down displacement.
TARGET_ORIGIN_XYZ_MM = (
    -((TARGET_SHAPE[2] - 1) * VOXEL_SIZE_MM) / 2.0,
    -((TARGET_SHAPE[1] - 1) * VOXEL_SIZE_MM) / 2.0,
    0.0,
)
LANDMARK_TOLERANCE_MM = 1e-9


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


def _signal_stats(plane: np.ndarray) -> dict:
    """Cheap, deterministic intensity evidence; never modifies source pixels."""
    finite = np.asarray(plane)
    nonzero = finite[finite != 0]
    return {
        "dtype": str(finite.dtype),
        "minimum": float(np.min(finite)),
        "maximum": float(np.max(finite)),
        "mean": float(np.mean(finite, dtype=np.float64)),
        "nonzero_pixels": int(nonzero.size),
        "nonzero_mean": float(np.mean(nonzero, dtype=np.float64)) if nonzero.size else None,
    }


def _single_plane_tiffs(source: Path, folder: Path) -> tuple[list[Path], list[dict]]:
    """Materialize temporary named planes; never use directory ordering as identity."""
    folder.mkdir(parents=True, exist_ok=True)
    volume = tifffile.memmap(source)
    paths: list[Path] = []
    diagnostics: list[dict] = []
    try:
        if tuple(volume.shape) != (1024, 512, 512):
            raise NisslBuildError("WHS_SOURCE_SHAPE", f"expected (1024, 512, 512), got {volume.shape}")
        for source_id, waxholm_ap in enumerate(range(189, 777)):
            path = folder / f"whs_nissl_40um_ap_{waxholm_ap}.tiff"
            plane = np.asarray(volume[waxholm_ap])
            tifffile.imwrite(path, plane, photometric="minisblack")
            paths.append(path)
            diagnostics.append({"source_id": source_id, "waxholm_ap": waxholm_ap,
                                **_signal_stats(plane)})
            if source_id + 189 != waxholm_ap:
                raise AssertionError("source/AP identity changed")
    finally:
        pipeline.close_memmap(volume)
    return paths, diagnostics


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


def _restore_state_and_wait(abba, state_file) -> None:
    """Restore all serialized actions and cross ABBA's task-queue barrier."""
    loaded = abba.state_load(state_file)
    if not bool(loaded):
        raise NisslBuildError("NATIVE_STATE_LOAD", "ABBAStateLoadCommand reported failure")
    abba.wait_for_end_of_tasks()
    count = int(abba.get_n_slices())
    if count != 588:
        raise NisslBuildError("NATIVE_STATE_LOAD", f"expected 588 slices, got {count}")


def _prepare_slices_for_export_and_wait(abba) -> None:
    """Apply only native export thickness, then cross the task barrier again."""
    abba.select_all_slices()
    abba.set_slices_thickness_match_neighbors()
    abba.wait_for_end_of_tasks()


def _find_tps(value):
    if isinstance(value, dict):
        if value.get("type") == "ThinplateSplineTransform":
            return value
        for child in value.values():
            found = _find_tps(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_tps(child)
            if found is not None:
                return found
    return None


def _walk_transform_types(value) -> list[str]:
    result = []
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            result.append(value["type"])
        for child in value.values():
            result.extend(_walk_transform_types(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_walk_transform_types(child))
    return result


def _registration_fingerprints(state_path: Path) -> list[dict]:
    """Extract the scientific TPS payload and informative wrapper state."""
    with zipfile.ZipFile(state_path) as archive:
        slices = json.loads(archive.read("state.json"))["slices_state_list"]
    result = []
    for source_id, slice_state in enumerate(slices):
        actions = [action for action in slice_state["actions"]
                   if action.get("type") == "RegisterSliceAction"]
        if len(actions) != 1:
            raise NisslBuildError(
                "NATIVE_TRANSFORM_ROUNDTRIP",
                f"source_id {source_id} has {len(actions)} RegisterSliceAction entries; expected 1",
            )
        registration = actions[0].get("registration", {})
        serialized = registration.get("transform")
        if not isinstance(serialized, str):
            raise NisslBuildError(
                "NATIVE_TRANSFORM_ROUNDTRIP",
                f"source_id {source_id} has no serialized registration transform",
            )
        transform = json.loads(serialized)
        canonical = json.dumps(transform, sort_keys=True, separators=(",", ":"))

        deformation = _find_tps(transform)
        if deformation is None:
            raise NisslBuildError(
                "NATIVE_TRANSFORM_ROUNDTRIP",
                f"source_id {source_id} has no ThinplateSplineTransform",
            )
        deformation_canonical = json.dumps(deformation, sort_keys=True, separators=(",", ":"))
        src_pts = np.asarray(deformation.get("srcPts"), dtype=np.float64)
        tgt_pts = np.asarray(deformation.get("tgtPts"), dtype=np.float64)
        if src_pts.ndim != 2 or tgt_pts.ndim != 2:
            raise NisslBuildError(
                "NATIVE_TRANSFORM_ROUNDTRIP", f"source_id {source_id} has invalid TPS landmarks"
            )
        result.append({
            "source_id": source_id,
            "registration_type": registration.get("type"),
            "transform_types": _walk_transform_types(transform),
            "src_pts": src_pts,
            "tgt_pts": tgt_pts,
            "interval_min": transform.get("interval_min"),
            "interval_max": transform.get("interval_max"),
            "transform_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "deformation_sha256": hashlib.sha256(deformation_canonical.encode("utf-8")).hexdigest(),
        })
    return result


def _landmark_delta(expected: np.ndarray, observed: np.ndarray) -> float | None:
    if expected.shape != observed.shape:
        return None
    return float(np.max(np.abs(expected - observed))) if expected.size else 0.0


def _verify_transform_roundtrip(authoritative: Path, saved: Path,
                                diff_path: Path | None = None) -> dict:
    """Prove native state_load/state_save retained the BigWarp TPS deformation."""
    expected = _registration_fingerprints(authoritative)
    observed = _registration_fingerprints(saved)
    if len(expected) != 588 or len(observed) != 588:
        raise NisslBuildError(
            "NATIVE_TRANSFORM_ROUNDTRIP",
            f"expected 588 authoritative and restored transforms, got {len(expected)} and {len(observed)}",
        )
    deformation_mismatches = {}
    differences = []
    bounds_changed = []
    for item, other in zip(expected, observed):
        issues = []
        if item["registration_type"] != other["registration_type"]:
            issues.append("registration_type changed")
        if item["transform_types"] != other["transform_types"]:
            issues.append("transform type chain changed")
        src_delta = _landmark_delta(item["src_pts"], other["src_pts"])
        tgt_delta = _landmark_delta(item["tgt_pts"], other["tgt_pts"])
        if src_delta is None or src_delta > LANDMARK_TOLERANCE_MM:
            issues.append(f"srcPts shape/delta changed ({src_delta})")
        if tgt_delta is None or tgt_delta > LANDMARK_TOLERANCE_MM:
            issues.append(f"tgtPts shape/delta changed ({tgt_delta})")
        bounds_differ = (item["interval_min"], item["interval_max"]) != (
            other["interval_min"], other["interval_max"]
        )
        if issues:
            deformation_mismatches[item["source_id"]] = issues
        if bounds_differ:
            bounds_changed.append(item["source_id"])
        if issues or bounds_differ:
            differences.append({
                "source_id": item["source_id"],
                "registration_type_before": item["registration_type"],
                "registration_type_after": other["registration_type"],
                "transform_types_before": item["transform_types"],
                "transform_types_after": other["transform_types"],
                "interval_min_before": item["interval_min"],
                "interval_min_after": other["interval_min"],
                "interval_max_before": item["interval_max"],
                "interval_max_after": other["interval_max"],
                "src_pts_shape_before": list(item["src_pts"].shape),
                "src_pts_shape_after": list(other["src_pts"].shape),
                "tgt_pts_shape_before": list(item["tgt_pts"].shape),
                "tgt_pts_shape_after": list(other["tgt_pts"].shape),
                "src_pts_max_abs_delta_mm": src_delta,
                "tgt_pts_max_abs_delta_mm": tgt_delta,
                "landmark_count": int(item["src_pts"].shape[-1]),
                "deformation_issues": issues,
            })
    diff_path = diff_path or runtime.RuntimePaths().reports / "transform_roundtrip_diff.json"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(json.dumps({
        "criterion": "deformation_landmarks",
        "landmark_tolerance_mm": LANDMARK_TOLERANCE_MM,
        "source_count": len(expected),
        "deformation_mismatch_count": len(deformation_mismatches),
        "bounds_changed_source_count": len(bounds_changed),
        "differences": differences,
    }, indent=2) + "\n", encoding="utf-8")
    if deformation_mismatches:
        ids = sorted(deformation_mismatches)
        raise NisslBuildError(
            "NATIVE_TRANSFORM_ROUNDTRIP",
            f"native ABBA round-trip changed the BigWarp deformation of {len(ids)}/588 "
            f"sources (first: {ids[:5]}); details: {diff_path}",
        )
    hashes = [item["transform_sha256"] for item in observed]
    deformation_hashes = [item["deformation_sha256"] for item in observed]
    wrapper_changes = sum(
        item["transform_sha256"] != other["transform_sha256"]
        for item, other in zip(expected, observed)
    )
    return {
        "verified": True,
        "criterion": "deformation_landmarks",
        "landmark_tolerance_mm": LANDMARK_TOLERANCE_MM,
        "transform_count": len(hashes),
        "unique_transform_count": len(set(hashes)),
        "unique_deformation_count": len(set(deformation_hashes)),
        "copied_deformation_count": len(deformation_hashes) - len(set(deformation_hashes)),
        "bounds_changed_source_count": len(bounds_changed),
        "source_dependent_wrapper_change_count": wrapper_changes,
        "diff_report": str(diff_path),
        "aggregate_sha256": hashlib.sha256("".join(hashes).encode("ascii")).hexdigest(),
        "saved_state_sha256": runtime.sha256(saved),
    }


def _save_and_verify_state_roundtrip(abba, authoritative: Path, destination: Path,
                                     diff_path: Path | None = None) -> dict:
    saved = abba.state_save(_java_file(destination))
    if not bool(saved):
        raise NisslBuildError("NATIVE_TRANSFORM_ROUNDTRIP", "ABBAStateSaveCommand reported failure")
    abba.wait_for_end_of_tasks()
    if not destination.is_file():
        raise NisslBuildError("NATIVE_TRANSFORM_ROUNDTRIP", f"ABBA did not write {destination}")
    return _verify_transform_roundtrip(authoritative, destination, diff_path)


def _collect_state_diagnostics(abba, authoritative: Path, destination: Path,
                               diff_path: Path | None = None) -> tuple[dict, dict, list[str]]:
    """Collect non-rendering audits without turning them into a build gate.

    Wrapper normalization is accepted by the landmark-aware roundtrip check.
    A genuine TPS/type-chain mismatch remains fatal; unavailable optimizer
    accessors are reported without blocking native rendering.
    """
    warnings = []
    roundtrip = _save_and_verify_state_roundtrip(abba, authoritative, destination, diff_path)
    try:
        inversion = _audit_native_inversion_settings(abba)
    except Exception as exc:
        inversion = {"verified": False, "diagnostic_error": str(exc)}
        warnings.append(f"Native inversion-settings diagnostic was unavailable: {exc}")
    return roundtrip, inversion, warnings


def _audit_native_inversion_settings(abba) -> dict:
    """Record the ABBA 0.11 per-slice iterative-inverse settings actually in use."""
    settings = []
    for source_id, slice_source in enumerate(list(abba.mp.getSlices())):
        tolerance = float(slice_source.getTolerance())
        max_iterations = int(slice_source.getMaxIteration())
        if not np.isfinite(tolerance) or tolerance <= 0 or max_iterations <= 0:
            raise NisslBuildError(
                "NATIVE_INVERSION_SETTINGS",
                f"source_id {source_id} has invalid tolerance/max iterations "
                f"({tolerance}, {max_iterations})",
            )
        settings.append((tolerance, max_iterations))
    if len(settings) != 588:
        raise NisslBuildError(
            "NATIVE_INVERSION_SETTINGS", f"expected 588 slice settings, got {len(settings)}"
        )
    unique = sorted(set(settings))
    return {
        "verified": True,
        "slice_count": len(settings),
        "unique_settings": [
            {"tolerance": tolerance, "max_iterations": max_iterations}
            for tolerance, max_iterations in unique
        ],
        "note": (
            "Values are read from loaded ABBA SliceSources. ABBA 0.11's iterative-wrapper "
            "serializer does not persist these optimizer values separately."
        ),
    }


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

def _source_to_ap_si_lr(ij, sac, diagnostics: dict | None = None) -> np.ndarray:
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
    if not np.allclose(
        linear, np.diag([expected_scale_mm] * 3), rtol=1e-9, atol=1e-12
    ):
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
    if diagnostics is not None:
        diagnostics.update({
            "native_array_shape_ap_si_lr": list(source_ap_si_lr.shape),
            "native_source_transform_xyz": matrix.tolist(),
            "target_origin_xyz_mm": list(TARGET_ORIGIN_XYZ_MM),
            "native_start_ap_si_lr_voxels": starts.tolist(),
        })
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
    # voxel j is at 0.04*j+translation. Registered histology sections are a
    # discrete AP sequence: never blend neighbouring sections along AP. Select
    # the nearest native Z plane, then interpolate only inside its SI/LR plane.
    # The native export has one-voxel Z margins so the first and last registered
    # section remain addressable after nearest-plane selection.
    from scipy.ndimage import affine_transform
    target = np.zeros(TARGET_SHAPE, dtype=source_ap_si_lr.dtype)
    identity_2d = np.eye(2, dtype=np.float64)
    for ap in range(TARGET_SHAPE[0]):
        source_ap = int(np.floor((ap - starts[0]) + 0.5))
        if source_ap < 0 or source_ap >= source_ap_si_lr.shape[0]:
            continue
        plane = affine_transform(
            source_ap_si_lr[source_ap],
            identity_2d,
            offset=np.array([-starts[1], -starts[2]], dtype=np.float64),
            output_shape=(TARGET_SHAPE[1], TARGET_SHAPE[2]),
            output=source_ap_si_lr.dtype,
            order=1,
            mode="constant",
            cval=0,
            prefilter=False,
        )
        target[ap] = plane
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


def _spatial_diagnostics(labels: np.ndarray, volume: np.ndarray, target_ap: np.ndarray) -> dict:
    """Quantify residual placement without shifting or masking any pixels."""
    planes = []
    centroid_deltas = []
    for ap in target_ap:
        label_coords = np.argwhere(labels[ap] != 0)
        signal_coords = np.argwhere(volume[ap] != 0)
        if not label_coords.size or not signal_coords.size:
            continue
        label_centroid = label_coords.mean(axis=0)
        signal_centroid = signal_coords.mean(axis=0)
        delta = signal_centroid - label_centroid
        centroid_deltas.append(delta)
        planes.append({
            "ap": int(ap),
            "centroid_delta_si_lr_voxels": delta.tolist(),
            "label_bbox_si_lr": [label_coords.min(axis=0).tolist(), label_coords.max(axis=0).tolist()],
            "signal_bbox_si_lr": [signal_coords.min(axis=0).tolist(), signal_coords.max(axis=0).tolist()],
        })
    median = (np.median(np.asarray(centroid_deltas), axis=0).tolist()
              if centroid_deltas else None)
    return {
        "definition": "Non-zero signal-vs-label support; diagnostic only, no correction applied.",
        "measured_plane_count": len(planes),
        "median_centroid_delta_si_lr_voxels": median,
        "median_centroid_delta_si_lr_um": ([value * 40.0 for value in median]
                                            if median is not None else None),
        "planes": planes,
        "pixels_modified": False,
    }



class _AbbaAtlasView:
    """Expose the already AP/SI/LR arrays in ABBA's required ASR convention."""
    def __init__(self, atlas):
        self._atlas = atlas
        self.orientation = "asr"
        self.metadata = dict(atlas.metadata)
        self.metadata["orientation"] = "asr"
        self.metadata["abba_world_origin_xyz_mm"] = list(TARGET_ORIGIN_XYZ_MM)

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
                  "native_abba_orientation": "asr", "array_permutation_applied": False,
                  "native_fixed_source_origin_xyz_mm": list(TARGET_ORIGIN_XYZ_MM)}

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
        planes, source_plane_diagnostics = _single_plane_tiffs(source_path, work / "moving_sources")
        rebound_path = paths.reports / "rebound_state.abba"
        binding = build_rebound_state(state_path, planes, rebound_path)
        ij, _ = runtime.initialize_native_api(paths)
        abba, fixed_source_report = _open_fixed_abba(ij, _atlas_name())
        # This authoritative `.abba` is ABBA's three-member project state
        # (sources.json, state.json, BDV XML), not a "standard ZIP export".
        # ImportStdZipStateCommand expects a different interchange format with
        # meta.json.  Use the vendored state_load API so ABBA restores its own
        # project/source serialization natively.
        _restore_state_and_wait(abba, _java_file(rebound_path))
        transform_roundtrip, inversion_settings, diagnostic_warnings = _collect_state_diagnostics(
            abba,
            state_path,
            paths.reports / "native_state_roundtrip.abba",
            paths.reports / "transform_roundtrip_diff.json",
        )
        # ABBAStateLoadCommand can return after enqueueing slice actions.  A
        # slice-count check only proves that CreateSliceAction ran; it does not
        # prove that the later MoveSliceAction/RegisterSliceAction tasks (and
        # their BigWarp transforms) finished.  Exporting here previously raced
        # those tasks, producing a mixture of unregistered, distorted and blank
        # sections.  Use the synchronization API shipped by ABBA 0.11 before
        # observing or exporting the restored state.
        # The serialized sources are 1-um-thick 2-D planes separated by 40 um.
        # A volumetric BDV export otherwise contains empty Z planes depending
        # on grid phase.  This native ABBA command changes only display/export
        # thickness so neighbouring registered sections meet; it does not
        # alter any saved registration transform or landmark.
        _prepare_slices_for_export_and_wait(abba)
        module = abba.export_resampled_slices_to_bdv_source(
            block_size_x=64, block_size_y=64, block_size_z=1, channels="0",
            downsample_x=1, downsample_y=1, downsample_z=1,
            image_name="native_abba_0.11_waxholm_nissl", interpolate=True,
            margin_z=40.0, n_threads=max(1, min(8, os.cpu_count() or 1)),
            px_size_micron_x=40.0, px_size_micron_y=40.0, px_size_micron_z=40.0,
            resolution_levels=1,
        )
        sacs = _find_source_and_converters(module)
        if len(sacs) != 1:
            raise NisslBuildError("NATIVE_EXPORT_OUTPUT", f"expected one channel, got {len(sacs)}")
        grid_diagnostics: dict = {}
        native_volume = _source_to_ap_si_lr(ij, sacs[0], grid_diagnostics).astype(np.uint16, copy=False)
        # Enforce the validated sequence edge policy without altering anatomy.
        native_volume[duplicate_ap] = native_volume[target_ap[0]]
        blank_registered = _registered_blank_planes(native_volume, target_ap)
        spatial_diagnostics = _spatial_diagnostics(labels, native_volume, target_ap)
        output_plane_diagnostics = [
            {"source_id": source_id, "waxholm_ap": source_id + 189,
             "target_ap": int(ap), **_signal_stats(native_volume[ap])}
            for source_id, ap in enumerate(target_ap)
        ]
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
            "ap_sampling_policy": "nearest_native_plane_no_inter_slice_intensity_blending",
            "native_export_margin_z_um": 40.0,
            "native_task_synchronization": "waitForTasks_after_state_load_and_thickness",
            "native_transform_roundtrip": transform_roundtrip,
            "transform_roundtrip_criterion": "deformation_landmarks",
            "native_inversion_settings": inversion_settings,
            "java_dependencies": list(runtime.JAVA_DEPENDENCIES),
            "java_dependency_overrides": runtime.JAVA_DEPENDENCY_OVERRIDES,
            "native_grid_diagnostics": grid_diagnostics,
            "source_plane_intensity_diagnostics": source_plane_diagnostics,
            "output_plane_intensity_diagnostics": output_plane_diagnostics,
            "intensity_policy": "native_values_preserved_no_per_slice_normalization",
            "spatial_diagnostics": spatial_diagnostics,
            "blank_registered_plane_count": len(blank_registered),
            "blank_registered_ap_indices": blank_registered,
            "coverage_status": "review_required" if blank_registered else "complete",
            "warnings": diagnostic_warnings + ([
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
