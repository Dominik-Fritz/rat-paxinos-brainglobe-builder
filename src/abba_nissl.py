"""Strict, portable reader and renderer for the v0.3 ABBA Nissl state.

The reader deliberately does not use the BDV ``project.qpproj`` locations.  The
only image input is a separately verified, version-pinned WHS Nissl volume.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
from scipy.ndimage import map_coordinates
from scipy.optimize import least_squares

ABBA_SHA256 = "e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06"
ABBA_MEMBERS = {"sources.json", "state.json", "_bdvdataset_0.xml"}
SOURCE_COUNT = 588
SOURCE_AP = (189, 776)
TARGET_SHAPE = (608, 286, 409)
KNOWN_ACTIONS = {"CreateSliceAction", "MoveSliceAction", "RegisterSliceAction"}
KNOWN_REGISTRATIONS = {"SacBigWarp2DRegistration"}
KNOWN_TRANSFORMS = {
    "BoundedRealTransform", "InvertibleWrapped2DTransformAs3D",
    "WrappedIterativeInvertibleRealTransform", "ThinplateSplineTransform",
}


class NisslBuildError(RuntimeError):
    """An actionable, classified Nissl reconstruction failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk_types(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            found.append(value["type"])
        for child in value.values():
            found.extend(_walk_types(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_types(child))
    return found


@dataclass(frozen=True)
class Registration:
    source_id: int
    ap_index: int
    pretransform: tuple[float, ...]
    source_points: np.ndarray
    target_points: np.ndarray
    transform_sha256: str
    source_affine: tuple[float, ...] = (
        .039, 0., 0., -9.984, 0., .039, 0., -9.984, 0., 0., .001, -.0005,
    )


@dataclass(frozen=True)
class AbbaState:
    path: Path
    version: str
    registrations: tuple[Registration, ...]
    report: dict


def _read_archive(path: Path, expected_hash: str) -> tuple[list, dict, str]:
    actual = sha256_file(path)
    if actual.lower() != expected_hash.lower():
        raise NisslBuildError("ABBA_HASH_MISMATCH", f"expected {expected_hash}, got {actual}: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                raise NisslBuildError("ABBA_UNSAFE_ZIP", "archive contains an unsafe path")
            if len(names) != len(set(names)) or set(names) != ABBA_MEMBERS:
                raise NisslBuildError("ABBA_COMPONENTS", f"expected exactly {sorted(ABBA_MEMBERS)}, got {sorted(names)}")
            sources = json.loads(archive.read("sources.json"))
            state = json.loads(archive.read("state.json"))
            bdv = archive.read("_bdvdataset_0.xml").decode("utf-8")
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NisslBuildError("ABBA_CORRUPT", str(exc)) from exc
    return sources, state, bdv


def validate_abba(path: Path, expected_hash: str = ABBA_SHA256) -> AbbaState:
    """Validate every source/action and return transformations in slice order."""
    sources, state, bdv = _read_archive(path, expected_hash)
    if len(sources) != SOURCE_COUNT or len(state.get("slices_state_list", [])) != SOURCE_COUNT:
        raise NisslBuildError("ABBA_COUNT", "sources.json and slices_state_list must both contain 588 entries")
    by_id: dict[int, dict] = {}
    pattern = re.compile(r"^whs_nissl_40um_ap_(\d{3})\.tiff - Image0-Channel 1$")
    for source in sources:
        sid = source.get("source_id")
        if not isinstance(sid, int) or sid in by_id:
            raise NisslBuildError("ABBA_SOURCE_ID", f"duplicate or invalid source_id: {sid!r}")
        by_id[sid] = source
    if sorted(by_id) != list(range(SOURCE_COUNT)):
        raise NisslBuildError("ABBA_SOURCE_ID", "source_id must be contiguous 0..587")
    for sid, source in by_id.items():
        match = pattern.fullmatch(str(source.get("source_name", "")))
        expected_ap = sid + SOURCE_AP[0]
        if not match or int(match.group(1)) != expected_ap:
            raise NisslBuildError("ABBA_AP_NAME", f"source_id {sid} must name AP {expected_ap}")
        if source.get("sac", {}).get("spimdata", {}).get("datalocation") != "_bdvdataset_0.xml":
            raise NisslBuildError("ABBA_DATASET", f"source_id {sid} does not reference the embedded BDV dataset")
    if "project.qpproj" not in bdv:
        raise NisslBuildError("ABBA_DATASET", "unexpected BDV provenance; historical path is recorded but never opened")
    try:
        bdv_root = ET.fromstring(bdv)
        setup_affines = {}
        for view in bdv_root.findall(".//ViewRegistration"):
            transforms = view.findall("./ViewTransform/affine")
            if len(transforms) != 1 or not transforms[0].text:
                raise ValueError(f"view setup {view.get('setup')} has no unique affine")
            values = tuple(float(value) for value in transforms[0].text.split())
            if len(values) != 12 or not np.isfinite(values).all():
                raise ValueError(f"view setup {view.get('setup')} has an invalid affine")
            setup_affines[int(view.get("setup", "-1"))] = values
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise NisslBuildError("ABBA_DATASET", f"invalid embedded BDV registrations: {exc}") from exc

    registrations: list[Registration] = []
    action_counts: dict[str, int] = {}
    transform_hashes: list[str] = []
    for slice_number, item in enumerate(state["slices_state_list"]):
        pre = item.get("preTransform", {}).get("affinetransform3d")
        if not isinstance(pre, list) or len(pre) != 12 or not all(isinstance(v, (int, float)) for v in pre):
            raise NisslBuildError("ABBA_PRETRANSFORM", f"invalid preTransform at slice {slice_number}")
        created = None
        registration = None
        for action in item.get("actions", []):
            kind = action.get("type")
            action_counts[kind] = action_counts.get(kind, 0) + 1
            if kind not in KNOWN_ACTIONS:
                raise NisslBuildError("ABBA_ACTION_TYPE", f"unknown {kind!r} at slice {slice_number}")
            if kind == "CreateSliceAction":
                ids = action.get("original_sources", {}).get("source_indexes")
                if ids != [slice_number] or created is not None:
                    raise NisslBuildError("ABBA_SOURCE_MAPPING", f"ambiguous source mapping at slice {slice_number}: {ids}")
                created = ids[0]
            elif kind == "RegisterSliceAction":
                if registration is not None:
                    raise NisslBuildError("ABBA_REGISTRATION_COUNT", f"multiple registrations at slice {slice_number}")
                registration = action.get("registration", {})
        if created is None or registration is None:
            raise NisslBuildError("ABBA_REGISTRATION_COUNT", f"missing creation or registration at slice {slice_number}")
        if registration.get("type") not in KNOWN_REGISTRATIONS:
            raise NisslBuildError("ABBA_REGISTRATION_TYPE", f"unknown {registration.get('type')!r} at slice {slice_number}")
        try:
            transform = json.loads(registration["transform"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise NisslBuildError("ABBA_TRANSFORM", f"invalid transform at slice {slice_number}") from exc
        types = _walk_types(transform)
        unknown = sorted(set(types) - KNOWN_TRANSFORMS)
        if unknown or types != ["BoundedRealTransform", "InvertibleWrapped2DTransformAs3D", "WrappedIterativeInvertibleRealTransform", "ThinplateSplineTransform"]:
            raise NisslBuildError("ABBA_TRANSFORM_TYPE", f"unsupported transform chain {types} at slice {slice_number}")
        tps = transform["realTransform"]["wrappedTransform"]["wrappedTransform"]
        src, tgt = np.asarray(tps.get("srcPts"), float).T, np.asarray(tps.get("tgtPts"), float).T
        if src.ndim != 2 or src.shape[1] != 2 or src.shape != tgt.shape or src.shape[0] < 3 or not np.isfinite(src).all() or not np.isfinite(tgt).all():
            raise NisslBuildError("ABBA_SPLINE", f"invalid ThinplateSpline landmarks at slice {slice_number}")
        # Propagated registrations have identical TPS control points but a
        # slice-specific bounded Z interval.  Count/reuse the scientific 2-D
        # mapping rather than treating that bookkeeping bound as a new warp.
        canonical = json.dumps(tps, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        transform_hashes.append(digest)
        setup = by_id[created].get("sac", {}).get("viewsetup")
        if not isinstance(setup, int) or setup not in setup_affines:
            raise NisslBuildError("ABBA_DATASET", f"source_id {created} has no BDV pixel-to-world affine")
        registrations.append(Registration(
            created, created + 189, tuple(map(float, pre)), src, tgt, digest, setup_affines[setup]
        ))
    copied = sum(count - 1 for count in __import__("collections").Counter(transform_hashes).values() if count > 1)
    report = {"abba_sha256": expected_hash, "abba_version": state.get("version"), "source_count": 588,
              "slice_state_count": 588, "source_ap_range": [189, 776], "source_direction": "anterior-to-posterior",
              "action_counts": action_counts, "registration_types": ["SacBigWarp2DRegistration/ThinplateSplineTransform"],
              "copied_registration_planes": copied, "historical_bdv_path_used": False,
              "bdv_pixel_to_world_affines_applied": True,
              "bigwarp_inverse": "Newton inverse of forward TPS; landmarks are never swapped as the final transform"}
    return AbbaState(path, str(state.get("version")), tuple(registrations), report)


def validate_source_volume(path: Path, expected_sha256: str, expected_shape: tuple[int, int, int]) -> np.ndarray:
    if not path.is_file():
        raise NisslBuildError("WHS_DOWNLOAD_MISSING", f"version-pinned WHS Nissl volume is missing: {path}")
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        raise NisslBuildError("WHS_CACHE_CORRUPT", f"WHS checksum mismatch: expected {expected_sha256}, got {actual}")
    import tifffile
    volume = tifffile.memmap(path)
    if tuple(volume.shape) != tuple(expected_shape):
        raise NisslBuildError("WHS_SOURCE_SHAPE", f"expected AP/SI/LR {expected_shape}, got {volume.shape}")
    return volume


def _fit_tps(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fit the same 2-D r^2 log(r) TPS represented by BigWarp landmarks."""
    delta = source[:, None, :] - source[None, :, :]
    radius = np.linalg.norm(delta, axis=2)
    kernel = np.zeros_like(radius)
    nonzero = radius > 0
    kernel[nonzero] = radius[nonzero] ** 2 * np.log(radius[nonzero])
    polynomial = np.column_stack((np.ones(len(source)), source))
    system = np.block([[kernel, polynomial], [polynomial.T, np.zeros((3, 3))]])
    rhs = np.vstack((target, np.zeros((3, 2))))
    coefficients = np.linalg.lstsq(system, rhs, rcond=None)[0]
    return coefficients[:len(source)], coefficients[len(source):]


def _apply_tps(points: np.ndarray, landmarks: np.ndarray, radial: np.ndarray,
               affine: np.ndarray, jacobian: bool = False):
    delta = points[:, None, :] - landmarks[None, :, :]
    radius = np.linalg.norm(delta, axis=2)
    kernel = np.zeros_like(radius)
    nonzero = radius > 0
    kernel[nonzero] = radius[nonzero] ** 2 * np.log(radius[nonzero])
    result = kernel @ radial + np.column_stack((np.ones(len(points)), points)) @ affine
    if not jacobian:
        return result
    factor = np.zeros_like(radius)
    factor[nonzero] = 2 * np.log(radius[nonzero]) + 1
    derivative = delta * factor[:, :, None]
    # J[n, output_coordinate, input_coordinate]
    matrix = np.einsum("nki,ko->noi", derivative, radial)
    matrix += affine[1:].T[None, :, :]
    return result, matrix


def invert_bigwarp_tps(target: np.ndarray, source_points: np.ndarray,
                       target_points: np.ndarray, allow_invalid: bool = False) -> np.ndarray:
    """Numerically apply BigWarp's iterative inverse; swapping landmarks is not an inverse."""
    radial, affine = _fit_tps(source_points, target_points)
    # A reverse landmark fit is only a fast initial estimate. Newton iterations
    # invert the actual forward TPS stored by BigWarp.
    reverse_radial, reverse_affine = _fit_tps(target_points, source_points)
    estimate = _apply_tps(target, target_points, reverse_radial, reverse_affine)
    initial_estimate = estimate.copy()
    active = np.ones(len(target), dtype=bool)
    residual_norm = np.full(len(target), np.inf)
    for _ in range(50):
        indexes = np.flatnonzero(active)
        if not len(indexes):
            return estimate
        mapped, jac = _apply_tps(estimate[indexes], source_points, radial, affine, jacobian=True)
        residual = mapped - target[indexes]
        residual_norm[indexes] = np.linalg.norm(residual, axis=1)
        converged = residual_norm[indexes] < 1e-6
        active[indexes[converged]] = False
        indexes = indexes[~converged]
        if not len(indexes):
            return estimate
        residual = residual[~converged]
        jac = jac[~converged]
        determinant = jac[:, 0, 0] * jac[:, 1, 1] - jac[:, 0, 1] * jac[:, 1, 0]
        singular = np.abs(determinant) < 1e-12
        if np.any(singular):
            if not allow_invalid:
                raise NisslBuildError("ABBA_SPLINE_INVERSE", "singular BigWarp TPS Jacobian")
            estimate[indexes[singular]] = np.nan
            active[indexes[singular]] = False
            indexes = indexes[~singular]
            residual = residual[~singular]
            jac = jac[~singular]
            determinant = determinant[~singular]
            if not len(indexes):
                continue
        step = np.empty((len(indexes), 2), dtype=np.float64)
        step[:, 0] = (jac[:, 1, 1] * residual[:, 0] - jac[:, 0, 1] * residual[:, 1]) / determinant
        step[:, 1] = (-jac[:, 1, 0] * residual[:, 0] + jac[:, 0, 0] * residual[:, 1]) / determinant
        # BigWarp's iterative wrapper uses a controlled optimizer. Limit a
        # Newton jump to 0.5 mm so remote points cannot overshoot/fold while
        # already-converged pixels remain frozen.
        length = np.linalg.norm(step, axis=1)
        scale = np.minimum(1.0, 0.5 / np.maximum(length, 1e-15))
        estimate[indexes] -= step * scale[:, None]
    worst = float(np.max(residual_norm[active])) if np.any(active) else 0.0
    if allow_invalid:
        # Difficult points are rare and usually occur near strongly warped
        # boundaries. Recover them individually with a trust-region solver
        # before declaring that no inverse sample exists.
        recover = active | np.logical_not(np.isfinite(estimate).all(axis=1))
        for index in np.flatnonzero(recover):
            def objective(point):
                return (_apply_tps(point[None, :], source_points, radial, affine)[0] - target[index])

            start = estimate[index] if np.isfinite(estimate[index]).all() else initial_estimate[index]
            solved = least_squares(objective, start, method="lm", max_nfev=200,
                                   ftol=1e-12, xtol=1e-12, gtol=1e-12)
            if solved.success and np.linalg.norm(objective(solved.x)) < 1e-5:
                estimate[index] = solved.x
                active[index] = False
                recover[index] = False
        estimate[recover] = np.nan
        return estimate
    raise NisslBuildError("ABBA_SPLINE_INVERSE",
                          f"BigWarp iterative inverse did not converge for {int(active.sum())}/{len(target)} "
                          f"pixels; maximum residual {worst:.6g} mm")


def render_plane(source: np.ndarray, registration: Registration, shape: tuple[int, int],
                 diagnostics: dict | None = None) -> np.ndarray:
    """Inverse-map one slice onto the centred 40-um SI/LR target grid."""
    si, lr = np.indices(shape, dtype=np.float64)
    # BDV centres an n-pixel source using origin -n*spacing/2 (not -(n-1)/2).
    target_xy = np.column_stack((lr.ravel() * .04 - shape[1] * .04 / 2,
                                 si.ravel() * .04 - shape[0] * .04 / 2))
    inverse = np.empty_like(target_xy)
    # Chunking bounds TPS working memory and isolates iterative convergence;
    # some curated planes contain 60+ landmarks.
    for start in range(0, len(target_xy), 4096):
        stop = min(start + 4096, len(target_xy))
        try:
            inverse[start:stop] = invert_bigwarp_tps(
                target_xy[start:stop], registration.source_points, registration.target_points,
                allow_invalid=True,
            )
        except NisslBuildError as exc:
            raise NisslBuildError(
                exc.code,
                f"source_id {registration.source_id} (Waxholm AP {registration.ap_index}), "
                f"target pixels {start}..{stop - 1}: {exc}",
            ) from exc
    affine = np.asarray(registration.pretransform).reshape(3, 4)
    homogeneous = np.eye(4); homogeneous[:3] = affine
    try:
        undo_affine = np.linalg.inv(homogeneous)
    except np.linalg.LinAlgError as exc:
        raise NisslBuildError("ABBA_PRETRANSFORM", f"singular preTransform for source {registration.source_id}") from exc
    xyz = np.column_stack((inverse, np.zeros(len(inverse)), np.ones(len(inverse))))
    inverse = (xyz @ undo_affine.T)[:, :2]
    source_world = np.column_stack((inverse, np.zeros(len(inverse)), np.ones(len(inverse))))
    bdv = np.eye(4); bdv[:3] = np.asarray(registration.source_affine).reshape(3, 4)
    source_pixel = source_world @ np.linalg.inv(bdv).T
    source_lr = source_pixel[:, 0]
    source_si = source_pixel[:, 1]
    invalid = np.logical_not(np.isfinite(source_lr) & np.isfinite(source_si))
    source_lr[invalid] = -1e9
    source_si[invalid] = -1e9
    if diagnostics is not None:
        diagnostics["noninvertible_target_pixels"] = int(invalid.sum())
    return map_coordinates(source, [source_si, source_lr], order=1, mode="constant", cval=0,
                           prefilter=False).reshape(shape)


def allocate_memmap(path: Path, shape: tuple[int, ...], dtype=np.uint16) -> np.memmap:
    try:
        return np.memmap(path, mode="w+", dtype=dtype, shape=shape)
    except MemoryError as exc:
        raise NisslBuildError("MEMORY_EXHAUSTED", f"cannot allocate output memmap {path}") from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise NisslBuildError("STORAGE_EXHAUSTED", f"not enough space for output memmap {path}") from exc
        raise


def render_volume(state: AbbaState, source: np.ndarray, destination: Path,
                  target_ap_indices: np.ndarray, duplicated_target_ap: int,
                  target_shape: tuple[int, int, int] = TARGET_SHAPE) -> dict:
    """Render all registrations plane-wise; never materialise the volume in RAM."""
    if source.shape[0] <= SOURCE_AP[1]:
        raise NisslBuildError("WHS_SOURCE_SHAPE", f"AP axis has only {source.shape[0]} planes; AP 776 is required")
    target_ap_indices = np.asarray(target_ap_indices, dtype=np.int64)
    if target_ap_indices.shape != (len(state.registrations),):
        raise NisslBuildError("TARGET_AP_MAPPING", f"expected {len(state.registrations)} target AP indices, got {target_ap_indices.shape}")
    if (np.diff(target_ap_indices) <= 0).any() or target_ap_indices[0] < 0 or target_ap_indices[-1] >= target_shape[0]:
        raise NisslBuildError("TARGET_AP_MAPPING", f"target AP indices are invalid: {target_ap_indices.tolist()}")
    if duplicated_target_ap < 0 or duplicated_target_ap >= target_shape[0] or duplicated_target_ap in target_ap_indices:
        raise NisslBuildError("TARGET_AP_MAPPING", f"invalid duplicated anterior target AP: {duplicated_target_ap}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = allocate_memmap(destination, target_shape)
    output[:] = 0
    try:
        inverse_diagnostics = []
        for registration, target_ap_value in zip(state.registrations, target_ap_indices, strict=True):
            target_ap = int(target_ap_value)
            plane_diagnostics = {"source_id": registration.source_id,
                                 "waxholm_ap": registration.ap_index, "target_ap": target_ap}
            output[target_ap] = np.clip(render_plane(source[registration.ap_index], registration,
                                                      target_shape[1:], plane_diagnostics), 0, 65535).astype(np.uint16)
            if plane_diagnostics["noninvertible_target_pixels"]:
                inverse_diagnostics.append(plane_diagnostics)
        output[duplicated_target_ap] = output[int(target_ap_indices[0])]
        output.flush()
    except MemoryError as exc:
        raise NisslBuildError("MEMORY_EXHAUSTED", "memory exhausted while rendering a plane") from exc
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise NisslBuildError("STORAGE_EXHAUSTED", "storage exhausted while rendering") from exc
        raise
    finally:
        del output
    return {**state.report, "target_grid_ap_si_lr": list(target_shape), "target_voxel_um": 40,
            "target_sequence_offset": 1, "anterior_edge_policy": "duplicate_first_registered_plane",
            "mapped_plane_count": len(state.registrations),
            "mapped_target_ap_min_max": [int(target_ap_indices[0]), int(target_ap_indices[-1])],
            "duplicated_anterior_target_ap": int(duplicated_target_ap),
            "unused_target_sequence_positions": {"before": 1, "after": 0},
            "noninvertible_target_pixels_zero_filled": int(sum(
                plane["noninvertible_target_pixels"] for plane in inverse_diagnostics
            )),
            "noninvertible_planes": inverse_diagnostics,
            "interpolation": "linear inverse mapping; constant zero outside source",
            "output_raw_sha256": sha256_file(destination)}


def compare_reference(reconstructed: np.ndarray, reference: np.ndarray, selected=(0, 150, 607)) -> dict:
    """Numerical diagnostic only; it intentionally makes no equivalence claim."""
    if reconstructed.shape != reference.shape:
        raise NisslBuildError("COMPARISON_SHAPE", f"shapes differ: {reconstructed.shape} and {reference.shape}")
    delta = reconstructed.astype(np.float64) - reference.astype(np.float64)
    stats = lambda a: {"min": float(a.min()), "max": float(a.max()), "mean": float(a.mean()),
                       "std": float(a.std())}
    return {"shape": list(reconstructed.shape), "axes": "AP/SI/LR", "reconstructed": stats(reconstructed),
            "reference": stats(reference), "difference": stats(delta),
            "selected_planes": {str(ap): stats(delta[ap]) for ap in selected if 0 <= ap < delta.shape[0]},
            "scientific_equivalence": "not asserted; external visual ABBA validation is required"}
