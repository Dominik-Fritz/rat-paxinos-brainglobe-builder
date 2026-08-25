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
import tarfile
import tempfile
import os
import errno
import gzip
from typing import Iterable

import nibabel as nib
import numpy as np
import tifffile
from brainglobe_atlasapi import config as brainglobe_config
from scipy import ndimage

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
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    REPORT_JSON.write_text(payload, encoding="utf-8")
    build_id = os.environ.get("PAXINOS_BUILD_ID")
    if build_id:
        isolated = ROOT / "reports" / "builds" / build_id / "ch03_nissl_report.json"
        isolated.parent.mkdir(parents=True, exist_ok=True)
        isolated.write_text(payload, encoding="utf-8")


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


def load_package_manifest(package: Path) -> dict:
    path = package / PACKAGE_MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"Required package manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {"stack_file", "state_file", "stack_order", "target_sequence_offset", "anterior_edge_policy"}
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
    for field in ("stack_file", "state_file"):
        candidate = package / str(manifest[field])
        if not candidate.is_file():
            raise FileNotFoundError(f"Manifest file {field} is missing: {candidate}")
    return manifest


def inspect_package(package: Path, manifest: dict) -> dict:
    stack = package / manifest["stack_file"]
    state = package / manifest["state_file"]
    with tifffile.TiffFile(stack) as tif:
        series = tif.series[0]
        stack_info = {
            "path": str(stack), "sha256": sha256_file(stack),
            "shape": [int(v) for v in series.shape], "axes": series.axes,
            "dtype": str(series.dtype), "is_imagej": bool(tif.is_imagej),
        }
    result = {
        "package": str(package), "manifest": manifest,
        "stack": stack_info,
        "state": {"path": str(state), "sha256": sha256_file(state), "bytes": state.stat().st_size},
    }
    write_report({"package_inventory": result})
    print(f"  Package manifest : {package / PACKAGE_MANIFEST_NAME}")
    print(f"  Registered stack : {stack.name}  shape={tuple(stack_info['shape'])}")
    print(f"  ABBA state       : {state.name}")
    print(f"  Sequence offset  : {manifest['target_sequence_offset']:+d} target position")
    return result


def resample_plane(plane: np.ndarray) -> tuple[np.ndarray, str]:
    """Resample one registered plane, never allocating a full converted stack."""
    if plane.shape == TARGET_SHAPE[1:]:
        return plane, "native Paxinos SI/LR grid"
    if plane.shape == TARGET_SHAPE[1:][::-1]:
        return plane.T, "transposed native Paxinos SI/LR grid"
    if plane.shape != ABBA_CANVAS_SHAPE:
        raise ValueError(
            f"Unsupported registered plane shape {plane.shape}; expected {TARGET_SHAPE[1:]}, "
            f"{TARGET_SHAPE[1:][::-1]}, or calibrated ABBA canvas {ABBA_CANVAS_SHAPE}."
        )
    source_si = ((np.arange(TARGET_SHAPE[1]) - (TARGET_SHAPE[1] - 1) / 2) *
                 (TARGET_VOXEL_MM / ABBA_PIXEL_MM) + (plane.shape[0] - 1) / 2)
    source_lr = ((np.arange(TARGET_SHAPE[2]) - (TARGET_SHAPE[2] - 1) / 2) *
                 (TARGET_VOXEL_MM / ABBA_PIXEL_MM) + (plane.shape[1] - 1) / 2)
    if min(source_si[0], source_lr[0]) < 0 or source_si[-1] > plane.shape[0] - 1 or source_lr[-1] > plane.shape[1] - 1:
        raise ValueError("The Paxinos field of view lies outside the calibrated ABBA canvas.")
    grid_si, grid_lr = np.meshgrid(source_si, source_lr, indexing="ij")
    converted = ndimage.map_coordinates(plane, [grid_si, grid_lr], order=1, mode="nearest", prefilter=False)
    return converted, "centered 19.5-um ABBA canvas sampled on the 40-um Paxinos grid"


def float_bounds(stack: np.ndarray) -> tuple[float, float]:
    """Calculate the original exact percentiles through a disk memmap, not a RAM copy."""
    OPTIONAL_DIR.mkdir(parents=True, exist_ok=True)
    path = OPTIONAL_DIR / ".nissl-float-percentiles.tmp.dat"
    values = np.memmap(path, mode="w+", dtype=np.float32, shape=(stack.size,))
    offset = 0
    try:
        for index in range(stack.shape[0]):
            plane = np.nan_to_num(np.asarray(stack[index], dtype=np.float32))
            flattened = plane.reshape(-1)
            values[offset:offset + flattened.size] = flattened
            offset += flattened.size
        values.flush()
        low, high = np.percentile(values, (0.5, 99.5), overwrite_input=True)
        return float(low), float(high)
    finally:
        del values
        path.unlink(missing_ok=True)


def plane_to_u16(plane: np.ndarray, bounds: tuple[float, float] | None) -> np.ndarray:
    if plane.dtype == np.uint8:
        return plane.astype(np.uint16) * 257
    if np.issubdtype(plane.dtype, np.integer):
        return np.clip(plane, 0, 65535).astype(np.uint16)
    assert bounds is not None
    low, high = bounds
    finite = np.nan_to_num(plane.astype(np.float32), copy=False)
    return np.clip((finite - low) / max(high - low, 1e-6) * 65535, 0, 65535).astype(np.uint16)


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
    _, spatial_mapping = resample_plane(np.asarray(stack[0]))
    bounds = float_bounds(stack) if np.issubdtype(stack.dtype, np.floating) else None
    if fixed_ap.size != 589:
        raise ValueError(f"Expected 589 non-empty Paxinos AP planes, found {fixed_ap.size}")
    start = int(target_sequence_offset)
    target_ap = fixed_ap[start:start + stack.shape[0]]
    if target_ap.size != stack.shape[0]:
        raise ValueError("The configured target sequence offset exceeds the Paxinos AP sequence.")
    OPTIONAL_DIR.mkdir(parents=True, exist_ok=True)
    memmap_path = OPTIONAL_DIR / ".waxholm_anatomy_reference.tmp.dat"
    volume = np.memmap(memmap_path, mode="w+", dtype=np.uint16, shape=TARGET_SHAPE)
    volume[:] = 0
    for source_plane, destination_plane in enumerate(target_ap):
        converted, _ = resample_plane(np.asarray(stack[source_plane]))
        volume[int(destination_plane)] = plane_to_u16(converted, bounds)
    duplicated_target_ap: int | None = None
    if start == 1 and anterior_edge_policy == "duplicate_first_registered_plane":
        # There is no separately registered section for the leading target
        # position. Reusing the nearest registered section avoids an empty Ch03
        # edge while preserving the validated +1 alignment for all real pairs.
        duplicated_target_ap = int(fixed_ap[0])
        first_plane, _ = resample_plane(np.asarray(stack[0]))
        volume[duplicated_target_ap] = plane_to_u16(first_plane, bounds)
    temporary_tiff = ACTIVE_PATH.with_suffix(".tiff.tmp")
    try:
        volume.flush(); tifffile.imwrite(temporary_tiff, volume, bigtiff=True)
        with tifffile.TiffFile(temporary_tiff) as check:
            if tuple(check.series[0].shape) != TARGET_SHAPE or check.series[0].dtype != np.dtype("uint16"):
                raise ValueError("Transactional Nissl TIFF validation failed")
        temporary_tiff.replace(ACTIVE_PATH)
    except OSError as exc:
        if exc.errno == errno.ENOSPC: raise RuntimeError("DISK_FULL: no space while writing the Nissl memmap/TIFF") from exc
        raise
    except MemoryError as exc: raise RuntimeError("MEMORY_EXHAUSTED: insufficient RAM for a Nissl plane") from exc
    finally:
        del volume; memmap_path.unlink(missing_ok=True); temporary_tiff.unlink(missing_ok=True)
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
    }
    write_report({"ch03_import": report})
    print(f"  AP mapping       : offset {start:+d}; {target_ap.size} planes -> AP {target_ap[0]}..{target_ap[-1]}")
    print(f"  Spatial mapping  : {spatial_mapping}")
    return report


def write_nifti(active: np.ndarray, atlas: Path, name: str, output_dir: Path | None = None) -> Path:
    annotation = nib.load(str(atlas / "annotation.nii.gz"))
    target_shape = tuple(int(v) for v in annotation.shape[:3])
    if target_shape == tuple(active.shape):
        data = active
    elif target_shape == (active.shape[2], active.shape[0], active.shape[1]):
        data = active.transpose(2, 0, 1)
    else:
        raise ValueError(f"Cannot orient Ch03 {active.shape} to annotation NIfTI {target_shape}: {atlas}")
    destination = (output_dir or atlas) / f"{name}.nii.gz"
    nib.save(nib.Nifti1Image(data, annotation.affine, annotation.header.copy()), destination)
    return destination


def install_channel(import_report: dict) -> list[dict]:
    """Stage every target, then activate all of them as one rollback-capable transaction."""
    active = tifffile.memmap(ACTIVE_PATH)
    name = "waxholm_anatomy_reference"
    staged: list[dict] = []
    activated: list[tuple[Path, Path | None]] = []
    try:
        for atlas in atlas_candidates():
            metadata_path = atlas / "metadata.json"
            if not (metadata_path.is_file() and (atlas / "annotation.tiff").is_file() and
                    (atlas / "annotation.nii.gz").is_file()):
                continue
            transaction = Path(tempfile.mkdtemp(prefix=".nissl-transaction-", dir=atlas))
            targets = [atlas / f"{name}.tiff", atlas / f"{name}.nii.gz", metadata_path]
            temp_tiff = transaction / targets[0].name
            shutil.copy2(ACTIVE_PATH, temp_tiff)
            temp_nifti = write_nifti(active, atlas, name, transaction)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            references = metadata.get("additional_references", [])
            if isinstance(references, str):
                references = [references]
            if name not in references:
                references.append(name)
            metadata["additional_references"] = references
            metadata["optional_ch03_registration"] = {
                "installed": True, "release": "0.3.0-prerelease", "reference_name": name,
                "stack_order": import_report["stack_order"],
                "target_sequence_offset": import_report["target_sequence_offset"],
                "interpretation": "Manually BigWarp-registered WHS Nissl visual aid; Paxinos labels remain authoritative.",
            }
            temp_metadata = transaction / "metadata.json"
            temp_metadata.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
            json.loads(temp_metadata.read_text(encoding="utf-8"))
            with tifffile.TiffFile(temp_tiff) as check:
                if tuple(check.series[0].shape) != TARGET_SHAPE or check.series[0].dtype != np.dtype("uint16"):
                    raise ValueError("staged TIFF geometry or dtype changed")
            if tuple(nib.load(str(temp_nifti)).shape) != tuple(nib.load(str(atlas / "annotation.nii.gz")).shape):
                raise ValueError("staged NIfTI shape changed")
            staged.append({"atlas": atlas, "transaction": transaction, "targets": targets,
                           "files": [temp_tiff, temp_nifti, temp_metadata]})
        if not staged:
            raise FileNotFoundError("No generated or installed Paxinos atlas accepted the Ch03 channel.")

        for item in staged:
            for staged_file, target in zip(item["files"], item["targets"]):
                backup = item["transaction"] / (target.name + ".backup") if target.exists() else None
                if backup:
                    shutil.copy2(target, backup)
                os.replace(staged_file, target)
                activated.append((target, backup))
        # Archive creation is inside the transaction: failure rolls all targets back.
        repack_candidate()
    except Exception:
        for target, backup in reversed(activated):
            if backup and backup.exists():
                restore = backup.with_suffix(backup.suffix + ".restore")
                shutil.copy2(backup, restore)
                os.replace(restore, target)
            else:
                target.unlink(missing_ok=True)
        raise
    finally:
        for item in staged:
            shutil.rmtree(item["transaction"], ignore_errors=True)

    installed = [{"atlas": str(item["atlas"]), "tiff": str(item["targets"][0]),
                  "nifti": str(item["targets"][1])} for item in staged]
    write_report({"ch03_install": installed})
    print(f"  Installed targets: {len(installed)}")
    return installed


def repack_candidate() -> Path:
    candidate = ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um"
    if not candidate.is_dir():
        raise FileNotFoundError(f"Official candidate is missing: {candidate}")
    archive = candidate.parent / f"{candidate.name}.tar.gz"
    temporary_tar = archive.with_suffix(".tar.tmp")
    temporary_gzip = archive.with_suffix(".gz.tmp")
    for path in (temporary_tar, temporary_gzip):
        path.unlink(missing_ok=True)
    with tarfile.open(temporary_tar, "w") as output:
        for path in [candidate, *sorted(candidate.rglob("*"), key=lambda item: item.as_posix())]:
            info = output.gettarinfo(path, arcname=str(Path(candidate.name) / path.relative_to(candidate)))
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as stream:
                    output.addfile(info, stream)
            else:
                output.addfile(info)
    with temporary_tar.open("rb") as source, temporary_gzip.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            shutil.copyfileobj(source, compressed)
    os.replace(temporary_gzip, archive)
    temporary_tar.unlink(missing_ok=True)
    write_report({"candidate_archive": {"path": str(archive), "sha256": sha256_file(archive)}})
    return archive


def build_from_package(package_path: str) -> int:
    package = Path(package_path).expanduser().resolve()
    manifest = load_package_manifest(package)
    print("\n  [NISSL PACKAGE]")
    print("  " + "-" * 66)
    inspect_package(package, manifest)
    report = import_registered_stack(
        package / manifest["stack_file"], manifest["stack_order"], int(manifest["target_sequence_offset"]),
        manifest["anterior_edge_policy"],
    )
    install_channel(report)
    archive = ROOT / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um.tar.gz"
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
    args = parser.parse_args(argv)
    try:
        package = Path(args.package).expanduser().resolve()
        manifest = load_package_manifest(package)
        if args.command == "inspect-package":
            inspect_package(package, manifest)
            return 0
        return build_from_package(str(package))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
