"""Render the Ch03 Nissl channel natively in ABBA 0.11.

Every spatial transform is evaluated by ABBA in Java; Python only rebinds the
sources, moves arrays and places planes on the atlas AP grid.
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
# The BigWarp registrations sit on a coronal canvas centred at LR=0/SI=0
# (px=-9.4, py=-6.56). BrainGlobe has no origin field, so the voxel centres are
# placed explicitly, and the fixed source and the resampling must share them.
TARGET_ORIGIN_XYZ_MM = (
    -((TARGET_SHAPE[2] - 1) * VOXEL_SIZE_MM) / 2.0,
    -((TARGET_SHAPE[1] - 1) * VOXEL_SIZE_MM) / 2.0,
    0.0,
)
LANDMARK_TOLERANCE_MM = 1e-9
# ABBA builds its export box from the slice boundaries, which leaves the Z grid
# half a voxel off the slice centres. 60 um restores the phase and, unlike 20 um,
# keeps the last section inside the stack.
NATIVE_EXPORT_MARGIN_Z_UM = 60.0
# ABBA recomputes the bounds from the fixed source, so they are informative only,
# and float noise in the last digits must not count as a change.
BOUNDS_TOLERANCE_MM = 1e-9


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
    """Per-plane intensity statistics; never modifies pixels."""
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


MOVING_PLANE_MANIFEST = ROOT / "resources/optional_ch03/whs_nissl_slices_manifest.json"
MOVING_PLANE_DIR = ROOT / "resources/optional_ch03/whs_nissl_slices_paxinos_40um_ap"
# 255 * 257 == 65535. One factor for every plane keeps relative intensities intact.
UINT8_TO_UINT16_SCALE = 257


VISUAL_PARITY_APPROVAL = ROOT / "resources/optional_ch03/visual_parity_approval.json"
VISUAL_PARITY_STATES = {"pending", "passed", "failed"}


def content_sha256_of(volume: np.ndarray) -> str:
    """Identity of the reconstruction itself, TIFF container excluded."""
    return hashlib.sha256(np.ascontiguousarray(volume).tobytes()).hexdigest()


def resolve_visual_parity(content_sha256: str) -> dict:
    """Return the recorded visual-parity decision if it was made for these voxels.

    The approval names the content hash it was given for; any other result stays pending.
    """
    absent = {"visual_parity_status": "pending", "release_eligible": False,
              "visual_parity_approval": {"recorded": False, "applies_to_this_build": False}}
    if not VISUAL_PARITY_APPROVAL.is_file():
        return absent
    try:
        record = json.loads(VISUAL_PARITY_APPROVAL.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        absent["visual_parity_approval"]["error"] = f"unreadable approval: {exc}"
        return absent
    status = record.get("visual_parity_status")
    approved_for = record.get("output_content_sha256")
    provenance = {
        "recorded": True,
        "file": str(VISUAL_PARITY_APPROVAL),
        "recorded_status": status,
        "approved_for_content_sha256": approved_for,
        "reviewer": record.get("reviewer"),
        "reviewed_utc": record.get("reviewed_utc"),
        "notes": record.get("notes"),
    }
    if status not in VISUAL_PARITY_STATES - {"pending"}:
        provenance["applies_to_this_build"] = False
        provenance["reason"] = f"recorded status {status!r} is not a decision"
        return {"visual_parity_status": "pending", "release_eligible": False,
                "visual_parity_approval": provenance}
    if approved_for != content_sha256:
        provenance["applies_to_this_build"] = False
        provenance["reason"] = (
            "approval names a different reconstruction; this build produced "
            f"{content_sha256}"
        )
        return {"visual_parity_status": "pending", "release_eligible": False,
                "visual_parity_approval": provenance}
    provenance["applies_to_this_build"] = True
    return {"visual_parity_status": status, "release_eligible": status == "passed",
            "visual_parity_approval": provenance}


def _load_moving_plane_manifest() -> dict:
    if not MOVING_PLANE_MANIFEST.is_file():
        raise NisslBuildError(
            "MOVING_PLANES_MISSING",
            f"pinned registration planes manifest is missing: {MOVING_PLANE_MANIFEST}",
        )
    manifest = json.loads(MOVING_PLANE_MANIFEST.read_text(encoding="utf-8"))
    if len(manifest.get("planes", {})) != 588:
        raise NisslBuildError(
            "MOVING_PLANES_MISSING",
            f"expected 588 pinned planes, manifest lists {len(manifest.get('planes', {}))}",
        )
    return manifest


def _moving_source_provenance(package_manifest: dict, plane_manifest: dict) -> dict:
    """Describe the moving data; the Waxholm package itself is not needed."""
    digest = hashlib.sha256()
    for source_id in range(588):
        name = f"whs_nissl_40um_ap_{source_id + 189}.tiff"
        digest.update(plane_manifest["planes"][name]["sha256"].encode("ascii"))
    return {
        "source_kind": "pinned registration planes (SHA-256 verified per plane)",
        "path": str(MOVING_PLANE_DIR),
        "manifest": str(MOVING_PLANE_MANIFEST),
        "sha256": digest.hexdigest(),
        "sha256_definition": "SHA-256 over the 588 per-plane hashes in source_id order",
        "plane_count": 588,
        "plane_dtype": plane_manifest.get("plane_dtype"),
        "uint16_scale_factor": plane_manifest.get("uint16_scale_factor"),
        "ap_range": [189, 776],
        "ap_direction": package_manifest["waxholm_ap_direction"],
        "derived_from_atlas_name": package_manifest["waxholm_atlas_name"],
        "derived_from_dataset_version": package_manifest["waxholm_dataset_version"],
        "derived_from_brainglobe_package_version":
            package_manifest["waxholm_brainglobe_package_version"],
        "brainglobe_package_required": False,
        "qupath_project": plane_manifest.get("qupath_project"),
    }


def _single_plane_tiffs(folder: Path) -> tuple[list[Path], list[dict]]:
    """Write out the pinned moving planes the saved registration was made on.

    Re-deriving them from the raw 39 um Waxholm volume puts them 22-25 voxels off
    in LR, by a different amount at every AP level.
    """
    planes = _load_moving_plane_manifest()["planes"]
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    diagnostics: list[dict] = []
    for source_id, waxholm_ap in enumerate(range(189, 777)):
        name = f"whs_nissl_40um_ap_{waxholm_ap}.tiff"
        expected = planes.get(name)
        if expected is None or int(expected["source_id"]) != source_id:
            raise NisslBuildError("MOVING_PLANES_MISSING",
                                  f"manifest has no entry for source_id {source_id} ({name})")
        pinned = MOVING_PLANE_DIR / name
        if not pinned.is_file():
            raise NisslBuildError("MOVING_PLANES_MISSING", f"pinned plane is missing: {pinned}")
        observed = pipeline.sha256_file(pinned)
        if observed != expected["sha256"]:
            raise NisslBuildError(
                "MOVING_PLANES_CORRUPT",
                f"{name} does not match the pinned registration input "
                f"(expected {expected['sha256']}, found {observed})",
            )
        plane = np.asarray(tifffile.imread(pinned))
        if plane.shape != (512, 512) or plane.dtype != np.uint8:
            raise NisslBuildError(
                "MOVING_PLANES_CORRUPT",
                f"{name} must be uint8 (512, 512), got {plane.dtype} {plane.shape}",
            )
        scaled = plane.astype(np.uint16) * UINT8_TO_UINT16_SCALE
        path = folder / name
        tifffile.imwrite(path, scaled, photometric="minisblack")
        paths.append(path)
        diagnostics.append({"source_id": source_id, "waxholm_ap": waxholm_ap,
                            "pinned_sha256": expected["sha256"],
                            **_signal_stats(scaled)})
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
    """Swap the QuPath loader for the pinned TIFFs; ABBA actions and affines stay byte-identical."""
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
        # The dataset has 998 setups and sources.json selects 197..784. Every opener
        # is replaced so nothing can fall back to QuPath.
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


def _bounds_delta(expected: dict, observed: dict) -> float | None:
    """Largest absolute bound change in mm, or None when the shape itself changed."""
    deltas: list[float] = []
    for key in ("interval_min", "interval_max"):
        before, after = expected.get(key), observed.get(key)
        if before is None or after is None:
            if (before is None) != (after is None):
                return None
            continue
        if len(before) != len(after):
            return None
        deltas.extend(abs(float(a) - float(b)) for a, b in zip(before, after))
    return max(deltas) if deltas else 0.0


def _verify_transform_roundtrip(authoritative: Path, saved: Path,
                                diff_path: Path | None = None) -> dict:
    """Check that state_load/state_save kept the BigWarp TPS deformation."""
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
    bounds_deltas: list[float] = []
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
        bounds_delta = _bounds_delta(item, other)
        bounds_differ = bounds_delta is None or bounds_delta > BOUNDS_TOLERANCE_MM
        if bounds_delta is not None:
            bounds_deltas.append(bounds_delta)
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
                "bounds_max_abs_delta_mm": bounds_delta,
                "landmark_count": int(item["src_pts"].shape[-1]),
                "deformation_issues": issues,
            })
    diff_path = diff_path or runtime.RuntimePaths().reports / "transform_roundtrip_diff.json"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(json.dumps({
        "criterion": "deformation_landmarks",
        "landmark_tolerance_mm": LANDMARK_TOLERANCE_MM,
        "bounds_tolerance_mm": BOUNDS_TOLERANCE_MM,
        "source_count": len(expected),
        "deformation_mismatch_count": len(deformation_mismatches),
        "bounds_changed_source_count": len(bounds_changed),
        "bounds_max_abs_delta_mm": max(bounds_deltas) if bounds_deltas else None,
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
        "bounds_tolerance_mm": BOUNDS_TOLERANCE_MM,
        "bounds_max_abs_delta_mm": max(bounds_deltas) if bounds_deltas else None,
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
    # ABBAStateSaveCommand fails rather than overwrite. The file is evidence for
    # this run only, never an input.
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    saved = abba.state_save(_java_file(destination))
    if not bool(saved):
        raise NisslBuildError("NATIVE_TRANSFORM_ROUNDTRIP", "ABBAStateSaveCommand reported failure")
    abba.wait_for_end_of_tasks()
    if not destination.is_file():
        raise NisslBuildError("NATIVE_TRANSFORM_ROUNDTRIP", f"ABBA did not write {destination}")
    return _verify_transform_roundtrip(authoritative, destination, diff_path)


def _collect_state_diagnostics(abba, authoritative: Path, destination: Path,
                               diff_path: Path | None = None) -> tuple[dict, dict, list[str]]:
    """Run the audits that do not render; only a real TPS or transform-type mismatch is fatal."""
    warnings = []
    roundtrip = _save_and_verify_state_roundtrip(abba, authoritative, destination, diff_path)
    try:
        slice_state = _audit_native_slice_state(abba)
    except Exception as exc:
        slice_state = {"verified": False, "diagnostic_error": str(exc)}
        warnings.append(f"Native slice-state audit was unavailable: {exc}")
    return roundtrip, slice_state, warnings


SCALAR_JAVA_RETURN_TYPES = {
    "double", "float", "int", "long", "short", "byte", "boolean",
    "java.lang.Double", "java.lang.Float", "java.lang.Integer",
    "java.lang.Long", "java.lang.Boolean", "java.lang.String",
}


def _slice_scalar_getters(slice_source) -> list[str]:
    """Zero-argument scalar getters that SliceSources actually exposes."""
    names = set()
    for method in slice_source.getClass().getMethods():
        if int(method.getParameterCount()) != 0:
            continue
        name = str(method.getName())
        if not (name.startswith("get") or name.startswith("is")):
            continue
        if str(method.getReturnType().getName()) in SCALAR_JAVA_RETURN_TYPES:
            names.add(name)
    return sorted(names)


def _audit_native_slice_state(abba) -> dict:
    """Check the restored slices: 588, each registered, evenly spaced along the slicing axis.

    ABBA 0.11 exposes no tolerance or iteration count for the inverse solver, so
    those are reported as unavailable.
    """
    slices = list(abba.mp.getSlices())
    if len(slices) != 588:
        raise NisslBuildError(
            "NATIVE_SLICE_STATE", f"expected 588 restored slices, got {len(slices)}"
        )
    getters = _slice_scalar_getters(slices[0])
    positions, thickness, registrations = [], set(), []
    for source_id, slice_source in enumerate(slices):
        positions.append(float(slice_source.getSlicingAxisPosition()))
        thickness.add(round(float(slice_source.getThicknessInMm()), 9))
        count = int(slice_source.getNumberOfRegistrations())
        if count < 1:
            raise NisslBuildError(
                "NATIVE_SLICE_STATE",
                f"source_id {source_id} carries {count} registrations after state load",
            )
        registrations.append(count)
    ordered = np.asarray(positions, dtype=np.float64)
    spacing = np.diff(np.sort(ordered))
    uniform = bool(spacing.size and np.allclose(spacing, VOXEL_SIZE_MM, rtol=0, atol=1e-9))
    if len(set(np.round(ordered, 9))) != 588:
        raise NisslBuildError(
            "NATIVE_SLICE_STATE", "restored slices do not occupy 588 distinct AP positions"
        )
    return {
        "verified": True,
        "slice_count": len(slices),
        "discovered_scalar_getters": getters,
        "slicing_axis_first_mm": float(ordered.min()),
        "slicing_axis_last_mm": float(ordered.max()),
        "slicing_axis_spacing_uniform": uniform,
        "slicing_axis_spacing_mm": float(np.median(spacing)) if spacing.size else None,
        "thickness_mm_at_load": sorted(thickness),
        "registrations_per_slice": sorted(set(registrations)),
        "iterative_inverse_settings_available": False,
        "note": (
            "ABBA 0.11 SliceSources exposes no tolerance/iteration getters, so the "
            "iterative-inverse optimizer settings cannot be read here; the getters listed "
            "above are the ones the class really provides. Export thickness is applied "
            "later, so thickness_mm_at_load is the serialized value, not the export value."
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
    # Some ABBA builds update the open positioner and only report success.
    if fallback is not None and int(fallback.getSlices().size()) == 588:
        return fallback
    keys = [str(key) for key in module.getOutputs().keySet()]
    raise NisslBuildError("NATIVE_STATE_LOAD", f"ZIP import returned no MultiSlicePositioner; outputs={keys}")

def _source_to_ap_si_lr(ij, sac, diagnostics: dict | None = None) -> np.ndarray:
    """Resample ABBA's export onto the fixed atlas voxel centres.

    The TPS has already been applied in Java; this is a linear change of sampling grid.
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
    # Millimetre values such as -8.14 are not exact in binary: snap values that are
    # numerically whole voxels, keep real half-voxel offsets.
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
        # Z profile of ABBA's own export, before Python places anything. It tells a
        # plane ABBA never rendered apart from one lost in the SI/LR resampling.
        diagnostics["native_plane_intensity_diagnostics"] = [
            {"native_ap_index": index,
             "native_ap_world_mm": float(matrix[2, 3] + index * expected_scale_mm),
             "target_ap_coordinate": float(starts[0] + index),
             **_signal_stats(source_ap_si_lr[index])}
            for index in range(source_ap_si_lr.shape[0])
        ]
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

    # Target voxel i sits at origin + 0.04*i, source voxel j at 0.04*j + translation.
    # Sections are a discrete AP sequence: take the nearest native Z plane and
    # interpolate within SI/LR only, never between neighbouring sections.
    from scipy.ndimage import affine_transform
    target = np.zeros(TARGET_SHAPE, dtype=source_ap_si_lr.dtype)
    identity_2d = np.eye(2, dtype=np.float64)
    selection: list[dict] = []
    for ap in range(TARGET_SHAPE[0]):
        source_ap = int(np.floor((ap - starts[0]) + 0.5))
        within_range = 0 <= source_ap < source_ap_si_lr.shape[0]
        selection.append({"target_ap": ap, "native_ap_index": source_ap,
                          "within_native_range": within_range})
        if not within_range:
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
    if diagnostics is not None:
        addressed = {item["native_ap_index"] for item in selection
                     if item["within_native_range"]}
        diagnostics["native_plane_selection"] = selection
        diagnostics["unused_native_ap_indices"] = sorted(
            set(range(source_ap_si_lr.shape[0])) - addressed
        )
    return target


def _atlas_name() -> str:
    for folder in pipeline.atlas_candidates():
        metadata = folder / "metadata.json"
        if metadata.is_file():
            data = json.loads(metadata.read_text(encoding="utf-8"))
            return str(data.get("atlas_name") or data.get("name") or "paxinos_watson_rat_40um")
    raise NisslBuildError("FIXED_SOURCE", "built Paxinos atlas metadata was not found")


def _registered_blank_planes(volume: np.ndarray, target_ap: np.ndarray) -> list[int]:
    """List all-zero planes. Zero is valid background, so this never fails the build or fills pixels."""
    return [int(value) for value in target_ap[~np.any(volume[target_ap] != 0, axis=(1, 2))]]


def _classify_blank_registered_planes(grid_diagnostics: dict, target_ap: np.ndarray,
                                      blank_ap: list[int],
                                      source_plane_diagnostics: list[dict]) -> dict:
    """Attribute each all-zero plane to its stage: pinned source, ABBA export or resampling."""
    selection = {int(item["target_ap"]): item
                 for item in grid_diagnostics.get("native_plane_selection", [])}
    native_planes = grid_diagnostics.get("native_plane_intensity_diagnostics", [])
    source_by_id = {int(item["source_id"]): item for item in source_plane_diagnostics}
    source_of_ap = {int(ap): source_id for source_id, ap in enumerate(target_ap)}
    empty_source, native_empty, sampling_loss, unaddressed = [], [], [], []
    for ap in (int(value) for value in blank_ap):
        source_id = source_of_ap.get(ap)
        entry = {"source_id": source_id, "target_ap": ap,
                 "waxholm_ap": None if source_id is None else source_id + 189}
        if not source_by_id.get(source_id, {}).get("nonzero_pixels", 1):
            empty_source.append(entry)
            continue
        item = selection.get(ap)
        entry["native_ap_index"] = None if item is None else item["native_ap_index"]
        if item is None or not item["within_native_range"]:
            unaddressed.append(entry)
            continue
        index = int(item["native_ap_index"])
        stats = native_planes[index] if index < len(native_planes) else None
        if stats is None:
            unaddressed.append(entry)
            continue
        entry["native_plane_nonzero_pixels"] = stats.get("nonzero_pixels")
        entry["native_plane_maximum"] = stats.get("maximum")
        (native_empty if not stats.get("nonzero_pixels") else sampling_loss).append(entry)
    native_nonzero = [int(item["nonzero_pixels"]) for item in native_planes]
    return {
        "definition": (
            "empty_waxholm_source: the pinned source plane was already all-zero. "
            "native_export_empty: ABBA's exported BDV plane was all-zero before "
            "Python touched it. sampling_loss: the native plane carried signal but "
            "SI/LR resampling produced an all-zero target plane. no_native_plane: "
            "the registered target AP has no addressable native plane. "
            "Diagnostic accounting only; no plane was filled or modified."
        ),
        "blank_registered_plane_count": len(blank_ap),
        "empty_waxholm_source_count": len(empty_source),
        "native_export_empty_count": len(native_empty),
        "sampling_loss_count": len(sampling_loss),
        "no_native_plane_count": len(unaddressed),
        "native_plane_count": len(native_planes),
        "native_empty_plane_count": sum(1 for value in native_nonzero if value == 0),
        "unused_native_ap_indices": grid_diagnostics.get("unused_native_ap_indices"),
        "empty_waxholm_source": empty_source,
        "native_export_empty": native_empty,
        "sampling_loss": sampling_loss,
        "no_native_plane": unaddressed,
    }


# Ten voxels, where a lateral offset becomes plainly visible. A warning, never a correction.
ALIGNMENT_WARNING_UM = 400.0


def _alignment_diagnostics(labels: np.ndarray, volume: np.ndarray,
                           target_ap: np.ndarray, sample: int = 25) -> dict:
    """Measure the residual SI/LR offset by cross-correlating tissue and annotation masks.

    Centroids are not used: tissue outside the labelled area pulls them ~200 um off.
    """
    if not target_ap.size:
        return {"measured_plane_count": 0, "median_shift_si_lr_um": None}
    picks = target_ap[np.linspace(0, target_ap.size - 1, min(sample, target_ap.size)).astype(int)]
    shifts = []
    for ap in (int(value) for value in picks):
        mask = labels[ap] != 0
        plane = volume[ap]
        signal = plane > 0
        if not mask.any() or not signal.any():
            continue
        # Ignore the dimmest fifth of the tissue. `>=`, not `>`: on a near-uniform
        # plane the percentile equals the maximum and `>` would drop the section.
        signal &= plane >= np.percentile(plane[signal], 15)
        if not signal.any():
            continue
        # With this operand order a positive shift means higher SI/LR indices than the annotation.
        spectrum = np.fft.rfft2(signal.astype(np.float32))
        correlation = np.fft.irfft2(spectrum * np.conj(np.fft.rfft2(mask.astype(np.float32))),
                                    s=mask.shape)
        peak = np.unravel_index(np.argmax(np.fft.fftshift(correlation)), mask.shape)
        shifts.append([peak[0] - mask.shape[0] // 2, peak[1] - mask.shape[1] // 2])
    if not shifts:
        return {"measured_plane_count": 0, "median_shift_si_lr_um": None}
    array = np.asarray(shifts, dtype=np.float64)
    median = np.median(array, axis=0)
    spread = np.percentile(array, 75, axis=0) - np.percentile(array, 25, axis=0)
    return {
        "definition": ("Shift of the registered Nissl mask relative to the annotation mask, "
                       "by FFT cross-correlation. Diagnostic only; no voxel is moved."),
        "measured_plane_count": len(shifts),
        "median_shift_si_lr_voxels": median.tolist(),
        "median_shift_si_lr_um": (median * VOXEL_SIZE_MM * 1000.0).tolist(),
        "iqr_si_lr_voxels": spread.tolist(),
        "max_abs_shift_um": float(np.max(np.abs(median)) * VOXEL_SIZE_MM * 1000.0),
        "warning_threshold_um": ALIGNMENT_WARNING_UM,
        "pixels_modified": False,
    }


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
    """Present the AP/SI/LR arrays in the ASR convention ABBA expects."""
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
    # The pinned planes are the registration's input; the Waxholm package is not read.
    source_report = _moving_source_provenance(manifest, _load_moving_plane_manifest())
    annotation_path = pipeline.find_annotation_tiff()
    labels = pipeline.orient_annotation(tifffile.imread(annotation_path), annotation_path)
    target_ap, duplicate_ap = pipeline.registered_target_ap_mapping(labels)
    paths = runtime.RuntimePaths()
    paths.create()
    work = Path(tempfile.mkdtemp(prefix="native-render-", dir=paths.temporary))
    swept = runtime.sweep_stale_work_dirs(paths.temporary, keep=work)
    try:
        planes, source_plane_diagnostics = _single_plane_tiffs(work / "moving_sources")
        rebound_path = paths.reports / "rebound_state.abba"
        binding = build_rebound_state(state_path, planes, rebound_path)
        ij, _ = runtime.initialize_native_api(paths)
        abba, fixed_source_report = _open_fixed_abba(ij, _atlas_name())
        # The .abba file is ABBA's own project state (sources.json, state.json, BDV XML),
        # not a standard ZIP export, so it goes through state_load.
        _restore_state_and_wait(abba, _java_file(rebound_path))
        transform_roundtrip, slice_state_audit, diagnostic_warnings = _collect_state_diagnostics(
            abba,
            state_path,
            paths.reports / "native_state_roundtrip.abba",
            paths.reports / "transform_roundtrip_diff.json",
        )
        # state_load returns while slice actions are still queued; exporting before the
        # registrations finish yields unregistered or blank sections. The saved sources
        # are 1 um planes 40 um apart, and matching their thickness to the neighbours
        # closes the Z gaps without touching any transform.
        _prepare_slices_for_export_and_wait(abba)
        module = abba.export_resampled_slices_to_bdv_source(
            block_size_x=64, block_size_y=64, block_size_z=1, channels="0",
            downsample_x=1, downsample_y=1, downsample_z=1,
            image_name="native_abba_0.11_waxholm_nissl", interpolate=True,
            margin_z=NATIVE_EXPORT_MARGIN_Z_UM,
            n_threads=max(1, min(8, os.cpu_count() or 1)),
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
        blank_classification = _classify_blank_registered_planes(
            grid_diagnostics, target_ap, blank_registered, source_plane_diagnostics
        )
        spatial_diagnostics = _spatial_diagnostics(labels, native_volume, target_ap)
        alignment_diagnostics = _alignment_diagnostics(labels, native_volume, target_ap)
        alignment_warning = (
            alignment_diagnostics.get("max_abs_shift_um") is not None
            and alignment_diagnostics["max_abs_shift_um"] > ALIGNMENT_WARNING_UM
        )
        output_plane_diagnostics = [
            {"source_id": source_id, "waxholm_ap": source_id + 189,
             "target_ap": int(ap), **_signal_stats(native_volume[ap])}
            for source_id, ap in enumerate(target_ap)
        ]
        temporary = pipeline.ACTIVE_PATH.with_suffix(".tiff.partial")
        tifffile.imwrite(temporary, native_volume, bigtiff=True)
        pipeline.activate_validated_tiff(temporary, pipeline.ACTIVE_PATH)
        content_sha256 = content_sha256_of(native_volume)
        parity = resolve_visual_parity(content_sha256)
        report = {
            "renderer_backend": "native_abba_0.11", "native_backend_verified": True,
            **parity,
            "source": source_report, "source_binding": binding, "fixed_source": fixed_source_report,
            "moving_plane_provenance": {
                "pinned_manifest": str(MOVING_PLANE_MANIFEST),
                "pinned_directory": str(MOVING_PLANE_DIR),
                "plane_count": 588, "verified_sha256_per_plane": True,
                "uint8_to_uint16_scale": UINT8_TO_UINT16_SCALE,
                "derived_from_waxholm_volume": False,
                "note": ("Exact moving images of the authoritative registration; the Waxholm "
                         "BrainGlobe package is retained as provenance only."),
            },
            "abba_state_sha256": runtime.STATE_SHA256, "abba_version": runtime.ABBA_VERSION,
            "source_count": 588, "slice_state_count": 588, "mapped_plane_count": 588,
            "transform_types": runtime.inspect_state(state_path)["action_types"],
            "waxholm_ap_range": [189, 776], "ap_direction": "anterior-to-posterior",
            "target_shape_ap_si_lr": list(TARGET_SHAPE), "target_voxel_um": 40.0,
            "target_origin_xyz_mm": list(TARGET_ORIGIN_XYZ_MM),
            "slice_thickness_policy": "native_match_neighbors_for_export",
            "ap_sampling_policy": "nearest_native_plane_no_inter_slice_intensity_blending",
            "native_export_margin_z_um": NATIVE_EXPORT_MARGIN_Z_UM,
            "native_export_z_grid_policy": "margin aligns export plane centres with slice centres",
            "native_task_synchronization": "waitForTasks_after_state_load_and_thickness",
            "native_transform_roundtrip": transform_roundtrip,
            "transform_roundtrip_criterion": "deformation_landmarks",
            "native_slice_state_audit": slice_state_audit,
            "java_dependencies": list(runtime.JAVA_DEPENDENCIES),
            "java_dependency_overrides": runtime.JAVA_DEPENDENCY_OVERRIDES,
            "native_grid_diagnostics": grid_diagnostics,
            "source_plane_intensity_diagnostics": source_plane_diagnostics,
            "output_plane_intensity_diagnostics": output_plane_diagnostics,
            "intensity_policy": "native_values_preserved_no_per_slice_normalization",
            "spatial_diagnostics": spatial_diagnostics,
            "alignment_diagnostics": alignment_diagnostics,
            "alignment_within_threshold": not alignment_warning,
            "blank_registered_plane_count": len(blank_registered),
            "blank_registered_ap_indices": blank_registered,
            "blank_registered_plane_classification": blank_classification,
            "coverage_status": "review_required" if blank_registered else "complete",
            "warnings": diagnostic_warnings + ([
                f"Native output has {len(blank_registered)} all-zero registered AP planes; "
                "retained unchanged for visual validation."
            ] if blank_registered else []) + ([
                "ALIGNMENT_REGRESSION: registered Nissl sits "
                f"{alignment_diagnostics['median_shift_si_lr_um'][0]:+.0f} um SI / "
                f"{alignment_diagnostics['median_shift_si_lr_um'][1]:+.0f} um LR from the "
                f"annotation, beyond the {ALIGNMENT_WARNING_UM:.0f} um guard. Nothing was "
                "shifted; investigate the registration or the pinned source before release."
            ] if alignment_warning else []),
            "actual_target_ap_indices": [int(value) for value in target_ap],
            "duplicated_anterior_target_ap": int(duplicate_ap),
            "stack_order": "anterior-to-posterior", "target_sequence_offset": 1,
            "anterior_edge_policy": "duplicate_first_registered_plane",
            "output_sha256": pipeline.sha256_file(pipeline.ACTIVE_PATH),
            # The file hash also covers the TIFF container; the content hash compares voxels.
            "output_content_sha256": content_sha256,
            "output_content_sha256_definition":
                "SHA-256 over the raw uint16 AP/SI/LR voxels, container excluded",
            "legacy_registered_stack_used": False,
            "stale_work_dirs_swept": swept,
        }
        # Written before installing, so the evidence survives a crash; install_status
        # keeps a failed install from reading as a success.
        report["install_status"] = "pending"
        pipeline.write_report({"abba_reconstruction": report})
        pipeline.install_channel(report)
        report["install_status"] = "installed"
        report["candidate_archive"] = str(pipeline.repack_candidate())
        # Drop the previous run's failure record.
        pipeline.write_report({"abba_reconstruction": report}, drop=("native_failure",))
        return report
    except Exception as exc:
        if isinstance(exc, NisslBuildError):
            raise
        raise classify_native_failure(exc) from exc
    finally:
        released = runtime.release_work_dir(work)
        if not released["removed"]:
            print(f"WARNING [NATIVE_WORK_DIR_RETAINED]: {released}", file=sys.stderr)


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
