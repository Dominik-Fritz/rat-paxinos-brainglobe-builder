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

import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.ndimage import map_coordinates

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
        registrations.append(Registration(created, created + 189, tuple(map(float, pre)), src, tgt, digest))
    copied = sum(count - 1 for count in __import__("collections").Counter(transform_hashes).values() if count > 1)
    report = {"abba_sha256": expected_hash, "abba_version": state.get("version"), "source_count": 588,
              "slice_state_count": 588, "source_ap_range": [189, 776], "source_direction": "anterior-to-posterior",
              "action_counts": action_counts, "registration_types": ["SacBigWarp2DRegistration/ThinplateSplineTransform"],
              "copied_registration_planes": copied, "historical_bdv_path_used": False}
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


def render_plane(source: np.ndarray, registration: Registration, shape: tuple[int, int]) -> np.ndarray:
    """Inverse-map one slice onto the centred 40-um SI/LR target grid."""
    si, lr = np.indices(shape, dtype=np.float64)
    target_xy = np.column_stack(((lr.ravel() - (shape[1]-1)/2) * .04,
                                 (si.ravel() - (shape[0]-1)/2) * .04))
    # ABBA stores the forward TPS landmarks. Rendering requires target->source.
    inverse = RBFInterpolator(registration.target_points, registration.source_points,
                              kernel="thin_plate_spline", degree=1)(target_xy)
    affine = np.asarray(registration.pretransform).reshape(3, 4)
    homogeneous = np.eye(4); homogeneous[:3] = affine
    try:
        undo_affine = np.linalg.inv(homogeneous)
    except np.linalg.LinAlgError as exc:
        raise NisslBuildError("ABBA_PRETRANSFORM", f"singular preTransform for source {registration.source_id}") from exc
    xyz = np.column_stack((inverse, np.zeros(len(inverse)), np.ones(len(inverse))))
    inverse = (xyz @ undo_affine.T)[:, :2]
    source_lr = inverse[:, 0] / .039 + (source.shape[1]-1)/2
    source_si = inverse[:, 1] / .039 + (source.shape[0]-1)/2
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
                  target_shape: tuple[int, int, int] = TARGET_SHAPE) -> dict:
    """Render all registrations plane-wise; never materialise the volume in RAM."""
    if source.shape[0] <= SOURCE_AP[1]:
        raise NisslBuildError("WHS_SOURCE_SHAPE", f"AP axis has only {source.shape[0]} planes; AP 776 is required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    output = allocate_memmap(destination, target_shape)
    output[:] = 0
    try:
        for registration in state.registrations:
            target_ap = registration.source_id + 1
            output[target_ap] = np.clip(render_plane(source[registration.ap_index], registration,
                                                      target_shape[1:]), 0, 65535).astype(np.uint16)
        output[0] = output[1]  # scientifically confirmed anterior edge policy
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
