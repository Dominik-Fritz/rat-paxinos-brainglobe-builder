"""Install the manually registered WHS/Nissl reference as atlas channel 3.

This release module intentionally contains only the deterministic runtime path:
validate a curated registration package, map its registered ImageJ stack to the
Paxinos AP sequence, install TIFF/NIfTI references, and repack the candidate.
The Paxinos annotation and ontology are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tarfile
from typing import Iterable

import nibabel as nib
import numpy as np
import tifffile
from brainglobe_atlasapi import BrainGlobeAtlas
from brainglobe_atlasapi import config as brainglobe_config
from scipy import ndimage
sys.path.insert(0, str(Path(__file__).resolve().parent))
from abba_nissl import NisslBuildError, render_volume, sha256_file as strict_sha256, validate_abba

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL_DIR = ROOT / "resources" / "optional_ch03"
REPORT_DIR = ROOT / "reports" / "ch03_nissl"
REPORT_JSON = REPORT_DIR / "ch03_nissl_report.json"
ACTIVE_PATH = OPTIONAL_DIR / "waxholm_anatomy_reference.tiff"
TARGET_SHAPE = (608, 286, 409)  # AP, SI, LR
TARGET_VOXEL_MM = 0.04
ABBA_CANVAS_SHAPE = (656, 940)  # SI, LR
ABBA_PIXEL_MM = 0.0195
PACKAGE_MANIFEST_NAME = "registration_manifest.json"
DEFAULT_STACK_NAME = "registered_slices_ImageJ_stack.tif"
DEFAULT_STACK_ORDER = "anterior-to-posterior"
# Visual ABBA validation showed the exported Nissl sequence belongs one target
# position to the right of the initial direct mapping. This is an AP sequence
# offset, not a spatial image transformation.
DEFAULT_TARGET_SEQUENCE_OFFSET = 1


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(update: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8")) if REPORT_JSON.exists() else {}
    report.update(update)
    report["updated_utc"] = datetime.now(timezone.utc).isoformat()
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def atlas_candidates() -> list[Path]:
    generated = ROOT / "data" / "output"
    bg = Path(brainglobe_config.get_brainglobe_dir())
    candidates = [
        generated / "brainglobe_official_candidate" / "paxinos_watson_rat_40um",
        generated / "brainglobe_provisional" / "paxinos_watson_rat_40um",
        bg / "paxinos_watson_rat_40um_v1.0",
        bg / "paxinos_watson_rat_40um",
    ]
    return list(dict.fromkeys(candidates))


def find_annotation_tiff() -> Path:
    for atlas in atlas_candidates():
        path = atlas / "annotation.tiff"
        if path.is_file():
            return path
    raise FileNotFoundError("No generated or installed Paxinos annotation.tiff was found.")


def orient_annotation(labels: np.ndarray, path: Path) -> np.ndarray:
    if labels.ndim != 3:
        raise ValueError(f"Expected a 3-D annotation, got {labels.shape}: {path}")
    if tuple(labels.shape) == TARGET_SHAPE:
        return labels
    raw = tuple(int(value) for value in labels.shape)
    if sorted(raw) != sorted(TARGET_SHAPE):
        raise ValueError(f"Annotation shape {raw} is not a permutation of {TARGET_SHAPE}: {path}")
    remaining = list(range(3))
    permutation: list[int] = []
    for size in TARGET_SHAPE:
        matches = [axis for axis in remaining if raw[axis] == size]
        if len(matches) != 1:
            raise ValueError(f"Ambiguous annotation axis permutation for {raw}: {path}")
        permutation.append(matches[0])
        remaining.remove(matches[0])
    return np.transpose(labels, tuple(permutation))


def registered_target_ap_mapping(labels: np.ndarray) -> tuple[np.ndarray, int]:
    """Apply the validated +1 offset to the non-empty Paxinos AP sequence."""
    fixed_ap = np.flatnonzero(np.any(labels != 0, axis=(1, 2)))
    if fixed_ap.size != 589:
        raise NisslBuildError(
            "TARGET_AP_MAPPING", f"expected 589 non-empty Paxinos AP planes, found {fixed_ap.size}"
        )
    return fixed_ap[1:], int(fixed_ap[0])


def load_package_manifest(package: Path) -> dict:
    path = package / PACKAGE_MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"Required package manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"abba_state_file", "abba_state_sha256", "stack_order", "target_sequence_offset",
                "anterior_edge_policy", "waxholm_atlas_name", "waxholm_dataset_version",
                "waxholm_brainglobe_package_version", "waxholm_reference_shape_ap_si_lr",
                "waxholm_orientation", "waxholm_ap_direction"}
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError(f"Package manifest is missing fields: {missing}")
    if manifest["stack_order"] not in {"anterior-to-posterior", "posterior-to-anterior"}:
        raise ValueError(f"Invalid stack_order: {manifest['stack_order']}")
    offset = int(manifest["target_sequence_offset"])
    if offset not in {0, 1}:
        raise ValueError("target_sequence_offset must be 0 or 1 for the accepted 588/589 sequence.")
    if manifest["anterior_edge_policy"] not in {"leave_empty", "duplicate_first_registered_plane"}:
        raise ValueError(f"Invalid anterior_edge_policy: {manifest['anterior_edge_policy']}")
    if manifest["waxholm_ap_direction"] != "anterior-to-posterior":
        raise ValueError("The pinned Waxholm AP direction must be anterior-to-posterior.")
    for field in ("abba_state_file",):
        candidate = package / str(manifest[field])
        if not candidate.is_file():
            raise FileNotFoundError(f"Manifest file {field} is missing: {candidate}")
    return manifest


def inspect_package(package: Path, manifest: dict) -> dict:
    state = package / manifest["abba_state_file"]
    parsed = validate_abba(state, manifest["abba_state_sha256"])
    result = {
        "package": str(package), "manifest": manifest,
        "state": {"path": str(state), "sha256": sha256_file(state), "bytes": state.stat().st_size},
        "abba_validation": parsed.report,
    }
    write_report({"package_inventory": result})
    print(f"  Package manifest : {package / PACKAGE_MANIFEST_NAME}")
    print(f"  ABBA state       : {state.name}")
    print(f"  Sequence offset  : {manifest['target_sequence_offset']:+d} target position")
    return result


def find_waxholm_source(manifest: dict) -> tuple[Path, dict]:
    """Download when needed, then resolve only the exactly pinned BrainGlobe atlas."""
    root = Path(brainglobe_config.get_brainglobe_dir())
    name = manifest["waxholm_atlas_name"]
    package_version = manifest["waxholm_brainglobe_package_version"]
    expected_folder = root / f"{name}_v{package_version}"
    source_kind = "verified BrainGlobe cache"
    if not expected_folder.is_dir():
        print(f"  Waxholm source   : downloading {name} package v{package_version} via BrainGlobe AtlasAPI...")
        try:
            downloaded = BrainGlobeAtlas(name, brainglobe_dir=root, check_latest=True)
        except Exception as exc:
            raise NisslBuildError(
                "WHS_NETWORK",
                f"BrainGlobe could not download {name} v{package_version}. Check network/GIN availability: {exc}",
            ) from exc
        atlas = Path(downloaded.brainglobe_dir) / str(downloaded.local_full_name)
        source_kind = "downloaded and validated by BrainGlobe AtlasAPI"
        if atlas.name != expected_folder.name:
            raise NisslBuildError(
                "WHS_VERSION",
                f"BrainGlobe supplied {atlas.name}, but this release requires {expected_folder.name}",
            )
    else:
        atlas = expected_folder
    metadata_path, source = atlas / "metadata.json", atlas / "reference.tiff"
    if not metadata_path.is_file() or not source.is_file():
        raise NisslBuildError("WHS_CACHE_CORRUPT", f"incomplete BrainGlobe cache: {atlas}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    observed = str(metadata.get("version", metadata.get("atlas_version", package_version)))
    if observed != package_version:
        raise NisslBuildError("WHS_VERSION", f"expected BrainGlobe package v{package_version}, metadata reports {observed}")
    orientation = str(metadata.get("orientation", "")).lower()
    if orientation != manifest["waxholm_orientation"]:
        raise NisslBuildError("WHS_ORIENTATION", f"expected orientation {manifest['waxholm_orientation']}, got {orientation!r}")
    with tifffile.TiffFile(source) as tif:
        shape = tuple(tif.series[0].shape)
    if shape != tuple(manifest["waxholm_reference_shape_ap_si_lr"]):
        raise NisslBuildError("WHS_SOURCE_SHAPE", f"expected AP/SI/LR {manifest['waxholm_reference_shape_ap_si_lr']}, got {shape}")
    return source, {"atlas_name": name, "dataset_version": manifest["waxholm_dataset_version"],
                    "brainglobe_package_version": package_version, "source_kind": source_kind,
                    "path": str(source), "orientation": orientation,
                    "sha256": strict_sha256(source), "shape_ap_si_lr": list(shape),
                    "ap_range": [189, 776], "ap_direction": "anterior-to-posterior"}


def resample_abba_canvas(stack: np.ndarray) -> tuple[np.ndarray, str]:
    if stack.shape[1:] == TARGET_SHAPE[1:]:
        return stack, "native Paxinos SI/LR grid"
    if stack.shape[1:] == TARGET_SHAPE[1:][::-1]:
        return stack.transpose(0, 2, 1), "transposed native Paxinos SI/LR grid"
    if stack.shape[1:] != ABBA_CANVAS_SHAPE:
        raise ValueError(
            f"Unsupported registered plane shape {stack.shape[1:]}; expected {TARGET_SHAPE[1:]}, "
            f"{TARGET_SHAPE[1:][::-1]}, or calibrated ABBA canvas {ABBA_CANVAS_SHAPE}."
        )
    source_si = ((np.arange(TARGET_SHAPE[1]) - (TARGET_SHAPE[1] - 1) / 2) *
                 (TARGET_VOXEL_MM / ABBA_PIXEL_MM) + (stack.shape[1] - 1) / 2)
    source_lr = ((np.arange(TARGET_SHAPE[2]) - (TARGET_SHAPE[2] - 1) / 2) *
                 (TARGET_VOXEL_MM / ABBA_PIXEL_MM) + (stack.shape[2] - 1) / 2)
    if min(source_si[0], source_lr[0]) < 0 or source_si[-1] > stack.shape[1] - 1 or source_lr[-1] > stack.shape[2] - 1:
        raise ValueError("The Paxinos field of view lies outside the calibrated ABBA canvas.")
    grid_si, grid_lr = np.meshgrid(source_si, source_lr, indexing="ij")
    converted = np.empty((stack.shape[0], TARGET_SHAPE[1], TARGET_SHAPE[2]), dtype=stack.dtype)
    for plane in range(stack.shape[0]):
        converted[plane] = ndimage.map_coordinates(stack[plane], [grid_si, grid_lr], order=1, mode="nearest", prefilter=False)
    return converted, "centered 19.5-um ABBA canvas sampled on the 40-um Paxinos grid"


def measure_edge_coverage(labels: np.ndarray, nissl: np.ndarray) -> dict:
    """Measure visible Nissl support against label bounds without altering pixels."""
    planes = []
    coverages = []
    for ap in np.flatnonzero(np.any(labels != 0, axis=(1, 2))):
        label_mask = labels[ap] != 0
        signal_mask = nissl[ap] > 0
        label_pixels = int(label_mask.sum())
        covered = int(np.logical_and(label_mask, signal_mask).sum())
        coverage = covered / label_pixels if label_pixels else 0.0
        coverages.append(coverage)
        label_coords = np.argwhere(label_mask)
        signal_coords = np.argwhere(signal_mask)
        label_bbox = [label_coords.min(axis=0).tolist(), label_coords.max(axis=0).tolist()]
        signal_bbox = ([signal_coords.min(axis=0).tolist(), signal_coords.max(axis=0).tolist()]
                       if signal_coords.size else None)
        planes.append({"ap": int(ap), "label_pixels": label_pixels,
                       "label_pixels_with_nissl_signal": covered,
                       "coverage_fraction": round(coverage, 6),
                       "label_bbox_si_lr": label_bbox,
                       "nissl_signal_bbox_si_lr": signal_bbox})
    return {
        "definition": "Fraction of labeled pixels containing non-zero Nissl signal; diagnostic only.",
        "plane_count": len(planes),
        "coverage_fraction_min": round(min(coverages), 6) if coverages else None,
        "coverage_fraction_median": round(float(np.median(coverages)), 6) if coverages else None,
        "coverage_fraction_max": round(max(coverages), 6) if coverages else None,
        "planes": planes,
        "pixels_modified": False,
    }


def import_registered_stack(
    source: Path, stack_order: str, target_sequence_offset: int,
    anterior_edge_policy: str = "duplicate_first_registered_plane",
) -> dict:
    annotation_path = find_annotation_tiff()
    labels = orient_annotation(tifffile.imread(annotation_path), annotation_path)
    fixed_ap = np.flatnonzero(np.any(labels != 0, axis=(1, 2)))
    stack = np.squeeze(np.asarray(tifffile.memmap(source)))
    if stack.ndim != 3:
        raise ValueError(f"Registered stack must be 3-D, got {stack.shape}: {source}")
    axes = [axis for axis, size in enumerate(stack.shape) if size == 588]
    if len(axes) != 1:
        raise ValueError(f"Expected exactly one 588-plane stack axis, got shape {stack.shape}")
    stack = np.moveaxis(stack, axes[0], 0)
    if stack_order == "posterior-to-anterior":
        stack = stack[::-1]
    stack, spatial_mapping = resample_abba_canvas(stack)
    if stack.dtype == np.uint8:
        stack_u16 = stack.astype(np.uint16) * 257
    elif np.issubdtype(stack.dtype, np.integer):
        stack_u16 = np.clip(stack, 0, 65535).astype(np.uint16)
    else:
        finite = np.nan_to_num(stack.astype(np.float32), copy=False)
        low, high = np.percentile(finite, (0.5, 99.5))
        stack_u16 = np.clip((finite - low) / max(high - low, 1e-6) * 65535, 0, 65535).astype(np.uint16)
    if fixed_ap.size != 589:
        raise ValueError(f"Expected 589 non-empty Paxinos AP planes, found {fixed_ap.size}")
    start = int(target_sequence_offset)
    target_ap = fixed_ap[start:start + stack_u16.shape[0]]
    if target_ap.size != stack_u16.shape[0]:
        raise ValueError("The configured target sequence offset exceeds the Paxinos AP sequence.")
    volume = np.zeros(TARGET_SHAPE, dtype=np.uint16)
    volume[target_ap] = stack_u16
    duplicated_target_ap: int | None = None
    if start == 1 and anterior_edge_policy == "duplicate_first_registered_plane":
        # There is no separately registered section for the leading target
        # position. Reusing the nearest registered section avoids an empty Ch03
        # edge while preserving the validated +1 alignment for all real pairs.
        duplicated_target_ap = int(fixed_ap[0])
        volume[duplicated_target_ap] = stack_u16[0]
    OPTIONAL_DIR.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(ACTIVE_PATH, volume, bigtiff=True)
    report = {
        "source": str(source), "source_sha256": sha256_file(source),
        "stack_order": stack_order, "target_sequence_offset": start,
        "anterior_edge_policy": anterior_edge_policy,
        "duplicated_anterior_target_ap": duplicated_target_ap,
        "mapped_plane_count": int(target_ap.size),
        "mapped_target_ap_min_max": [int(target_ap[0]), int(target_ap[-1])],
        "unused_target_sequence_positions": {
            "before": int(start), "after": int(fixed_ap.size - start - target_ap.size),
        },
        "spatial_mapping": spatial_mapping, "active_tiff": str(ACTIVE_PATH),
        "edge_coverage": measure_edge_coverage(labels, volume),
    }
    write_report({"ch03_import": report})
    print(f"  AP mapping       : offset {start:+d}; {target_ap.size} planes -> AP {target_ap[0]}..{target_ap[-1]}")
    print(f"  Spatial mapping  : {spatial_mapping}")
    return report


def write_nifti(active: np.ndarray, atlas: Path, name: str) -> Path:
    annotation = nib.load(str(atlas / "annotation.nii.gz"))
    target_shape = tuple(int(v) for v in annotation.shape[:3])
    if target_shape == tuple(active.shape):
        data = active
    elif target_shape == (active.shape[2], active.shape[0], active.shape[1]):
        data = active.transpose(2, 0, 1)
    else:
        raise ValueError(f"Cannot orient Ch03 {active.shape} to annotation NIfTI {target_shape}: {atlas}")
    destination = atlas / f"{name}.nii.gz"
    nib.save(nib.Nifti1Image(data, annotation.affine, annotation.header.copy()), destination)
    return destination


def install_channel(import_report: dict) -> list[dict]:
    if (import_report.get("renderer_backend") != "native_abba_0.11" or
            import_report.get("native_parity_verified") is not True):
        raise NisslBuildError(
            "NISSL_INSTALL_UNVERIFIED",
            "refusing to install Ch03: renderer_backend must be native_abba_0.11 and "
            "native_parity_verified must be true",
        )
    active = tifffile.imread(ACTIVE_PATH)
    name = "waxholm_anatomy_reference"
    installed: list[dict] = []
    for atlas in atlas_candidates():
        metadata_path = atlas / "metadata.json"
        if not (metadata_path.is_file() and (atlas / "annotation.tiff").is_file() and (atlas / "annotation.nii.gz").is_file()):
            continue
        tiff_path = atlas / f"{name}.tiff"
        shutil.copy2(ACTIVE_PATH, tiff_path)
        nifti_path = write_nifti(active, atlas, name)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        references = metadata.get("additional_references", [])
        if isinstance(references, str): references = [references]
        if name not in references: references.append(name)
        metadata["additional_references"] = references
        metadata["optional_ch03_registration"] = {
            "installed": True, "release": "0.3.0-prerelease", "reference_name": name,
            "stack_order": import_report["stack_order"],
            "target_sequence_offset": import_report["target_sequence_offset"],
            "renderer_backend": import_report["renderer_backend"],
            "native_parity_verified": import_report["native_parity_verified"],
            "interpretation": "Manually BigWarp-registered WHS Nissl visual aid; Paxinos labels remain authoritative.",
            "display_preferences": {
                "color_hex": "FFD54F",
                "color_name": "warm yellow",
                "opacity": 0.22,
                "status": "client_hint",
                "note": "Preferred ABBA display only; clients may require applying this converter setting manually.",
            },
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        installed.append({"atlas": str(atlas), "tiff": str(tiff_path), "nifti": str(nifti_path)})
    if not installed:
        raise FileNotFoundError("No generated or installed Paxinos atlas accepted the Ch03 channel.")
    write_report({"ch03_install": installed})
    print(f"  Installed targets: {len(installed)}")
    return installed


def repack_candidate() -> Path:
    candidate = ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
    if not candidate.is_dir():
        raise FileNotFoundError(f"Official candidate is missing: {candidate}")
    archive = candidate.parent / f"{candidate.name}.tar.gz"
    archive.unlink(missing_ok=True)
    with tarfile.open(archive, "w:gz") as output:
        output.add(candidate, arcname=candidate.name)
    write_report({"candidate_archive": {"path": str(archive), "sha256": sha256_file(archive)}})
    return archive


def close_memmap(array: np.ndarray) -> None:
    """Close a numpy memmap deterministically (required before Windows moves)."""
    mapping = getattr(array, "_mmap", None)
    if mapping is not None:
        mapping.close()


def activate_validated_tiff(temporary: Path, destination: Path) -> None:
    """Validate with a closed TIFF handle, then atomically activate the file."""
    with tifffile.TiffFile(temporary) as tif:
        shape = tuple(int(value) for value in tif.series[0].shape)
        dtype = np.dtype(tif.series[0].dtype)
    if shape != TARGET_SHAPE or dtype != np.dtype(np.uint16):
        raise NisslBuildError(
            "OUTPUT_VALIDATION",
            f"temporary TIFF must be uint16 {TARGET_SHAPE}, got {dtype} {shape}",
        )
    # The context above must be exited before replace(): Windows refuses to
    # rename an open source file (WinError 32).
    temporary.replace(destination)


def require_scientific_render_readiness(experimental_python_render: bool) -> None:
    """Prevent an unverified Python reinterpretation from becoming release data."""
    if experimental_python_render:
        print("WARNING [EXPERIMENTAL_BIGWARP_RENDER]: using unvalidated Python transform reproduction")
        return
    raise NisslBuildError(
        "ABBA_NATIVE_PARITY_REQUIRED",
        "SacBigWarp2DRegistration/ThinplateSplineTransform affects source_id 0..587. "
        "The .abba archive records moving-source BDV affines and landmarks, but not the external "
        "fixed Paxinos SourceAndConverter pixel-to-world transform or hashes of the original "
        "whs_nissl_40um_ap_*.tiff pixels. A Python TPS reconstruction therefore cannot be claimed "
        "to reproduce the curated ABBA display 1:1. Next step: render with the native ABBA 0.11/"
        "BigWarp Java transform stack against the same installed Paxinos atlas, then validate that "
        "output against the separate v0.3.0 reference before enabling it for releases.",
    )


def build_from_package(package_path: str, experimental_python_render: bool = False) -> int:
    package = Path(package_path).expanduser().resolve()
    manifest = load_package_manifest(package)
    print("\n  [NISSL PACKAGE]")
    print("  " + "-" * 66)
    inspect_package(package, manifest)
    require_scientific_render_readiness(experimental_python_render)
    state = validate_abba(package / manifest["abba_state_file"], manifest["abba_state_sha256"])
    source_path, source_report = find_waxholm_source(manifest)
    source = tifffile.memmap(source_path)
    annotation_path = find_annotation_tiff()
    labels = orient_annotation(tifffile.imread(annotation_path), annotation_path)
    target_ap_indices, duplicated_target_ap = registered_target_ap_mapping(labels)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    raw = REPORT_DIR / "waxholm_anatomy_reference.raw.partial"
    temporary_tiff = ACTIVE_PATH.with_suffix(".tiff.partial")
    raw.unlink(missing_ok=True)
    temporary_tiff.unlink(missing_ok=True)
    volume = None
    try:
        reconstruction = render_volume(
            state, source, raw, target_ap_indices, duplicated_target_ap
        )
        close_memmap(source)
        source = None
        volume = np.memmap(raw, mode="r", dtype=np.uint16, shape=TARGET_SHAPE)
        tifffile.imwrite(temporary_tiff, volume, bigtiff=True)
        close_memmap(volume)
        volume = None
        activate_validated_tiff(temporary_tiff, ACTIVE_PATH)
    finally:
        if source is not None:
            close_memmap(source)
        if volume is not None:
            close_memmap(volume)
        temporary_tiff.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)
    report = {"source": source_report, "reconstruction": reconstruction,
              "stack_order": "anterior-to-posterior", "target_sequence_offset": 1,
              "authoritative_registration_source": str(state.path),
              "legacy_registered_stack_used": False,
              "renderer_backend": "experimental_python_tps",
              "native_parity_verified": False}
    write_report({"abba_reconstruction": report})
    if experimental_python_render:
        print("  Experimental reconstruction completed for diagnostics only.")
        print("  It was NOT installed, packaged, or marked as a successful native Nissl channel.")
        return 0
    install_channel(report)
    archive = repack_candidate()
    print(f"  Candidate archive: {archive}")
    print("  Nissl channel build completed.")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the registered WHS/Nissl atlas channel")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect-package")
    inspect.add_argument("package")
    build = sub.add_parser("build-from-package")
    build.add_argument("package")
    build.add_argument(
        "--experimental-python-render", action="store_true",
        help="Development only: permit the unvalidated Python BigWarp reproduction.",
    )
    args = parser.parse_args(argv)
    try:
        package = Path(args.package).expanduser().resolve()
        manifest = load_package_manifest(package)
        if args.command == "inspect-package":
            inspect_package(package, manifest)
            return 0
        return build_from_package(str(package), args.experimental_python_render)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
