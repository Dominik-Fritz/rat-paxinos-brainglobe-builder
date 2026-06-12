#!/usr/bin/env python3
"""
V32.16 Border Reference Display Proxy

Purpose:
- Keep the real annotation label volume intact for ABBA label lookup.
- Stop relying on ABBA's generated Borders source if it renders filled/odd panels.
- Recreate reference.nii.gz and reference.tiff as a pure border/edge image derived from annotation.
- Leave annotation.nii.gz, annotation.tiff, structures.json/csv untouched.
- Patch both project official candidate and BrainGlobe cache stable atlas.

This is display-only and not an MRI/reference-channel experiment.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np


def _now() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _import_optional():
    mods = {}
    for name in ["nibabel", "tifffile"]:
        try:
            mods[name] = __import__(name)
        except Exception as e:
            mods[name] = e
    return mods


def _copy_backup(src: Path, backup_dir: Path, actions: list, dry_run: bool) -> Optional[Path]:
    if not src.exists():
        return None
    dst = backup_dir / src.name
    actions.append({"action": "backup_file", "src": str(src), "dst": str(dst), "dry_run": dry_run})
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dst


def _load_annotation(atlas_dir: Path, nib, tifffile) -> Tuple[np.ndarray, Optional[Any], str]:
    nii = atlas_dir / "annotation.nii.gz"
    tiff = atlas_dir / "annotation.tiff"
    if nii.exists() and nib is not None and not isinstance(nib, Exception):
        img = nib.load(str(nii))
        arr = np.asarray(img.get_fdata(dtype=np.float32))
        # Preserve label values as integers; using get_fdata is safe for uint labels in this range.
        arr = np.rint(arr).astype(np.uint32)
        return arr, img, str(nii)
    if tiff.exists() and tifffile is not None and not isinstance(tifffile, Exception):
        arr = tifffile.imread(str(tiff))
        return np.asarray(arr).astype(np.uint32), None, str(tiff)
    raise FileNotFoundError(f"No readable annotation.nii.gz or annotation.tiff in {atlas_dir}")


def _make_border_reference(labels: np.ndarray, include_outer: bool = True) -> np.ndarray:
    """Create a thin border image from a 3D label volume.

    Marks voxels that touch a neighbor with a different label. By default also includes
    the brain/background outer boundary, because that is useful as a display outline.
    """
    if labels.ndim != 3:
        raise ValueError(f"Expected 3D label volume, got shape {labels.shape}")

    border = np.zeros(labels.shape, dtype=bool)

    # Differences along each axis. Mark both sides of each transition.
    for ax in range(3):
        sl1 = [slice(None)] * 3
        sl2 = [slice(None)] * 3
        sl1[ax] = slice(1, None)
        sl2[ax] = slice(None, -1)
        a = labels[tuple(sl1)]
        b = labels[tuple(sl2)]
        diff = a != b
        if not include_outer:
            diff &= (a != 0) & (b != 0)

        mark1 = [slice(None)] * 3
        mark2 = [slice(None)] * 3
        mark1[ax] = slice(1, None)
        mark2[ax] = slice(None, -1)
        border[tuple(mark1)] |= diff
        border[tuple(mark2)] |= diff

    # Remove pure background interior if requested? We keep label/background borders.
    ref = np.zeros(labels.shape, dtype=np.uint16)
    ref[border] = np.iinfo(np.uint16).max
    return ref


def _write_nifti(ref: np.ndarray, out_path: Path, template_img: Optional[Any], nib, dry_run: bool, actions: list):
    actions.append({"action": "write_border_reference_nifti", "path": str(out_path), "shape": list(ref.shape), "dtype": str(ref.dtype), "dry_run": dry_run})
    if dry_run:
        return
    if nib is None or isinstance(nib, Exception):
        raise RuntimeError("nibabel is required to write reference.nii.gz")
    if template_img is not None:
        img = nib.Nifti1Image(ref, affine=template_img.affine, header=template_img.header)
        img.set_data_dtype(np.uint16)
    else:
        img = nib.Nifti1Image(ref, affine=np.eye(4))
        img.set_data_dtype(np.uint16)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(img, str(out_path))


def _write_tiff(ref: np.ndarray, out_path: Path, tifffile, dry_run: bool, actions: list):
    actions.append({"action": "write_border_reference_tiff", "path": str(out_path), "shape": list(ref.shape), "dtype": str(ref.dtype), "dry_run": dry_run})
    if dry_run:
        return
    if tifffile is None or isinstance(tifffile, Exception):
        raise RuntimeError("tifffile is required to write reference.tiff")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # BigTIFF helps with future larger references; compression kept absent for speed/compatibility.
    tifffile.imwrite(str(out_path), ref, bigtiff=True)


def _patch_metadata(atlas_dir: Path, dry_run: bool, actions: list) -> Dict[str, Any]:
    meta_path = atlas_dir / "metadata.json"
    info: Dict[str, Any] = {"path": str(meta_path), "exists": meta_path.exists(), "patched": False}
    if not meta_path.exists():
        return info
    before = json.loads(meta_path.read_text(encoding="utf-8"))
    after = dict(before)
    after["reference_strategy"] = "labelatlas_border_reference_display_proxy_no_mri"
    after["labelatlas_display_mode"] = "Use reference source for pure border display; keep annotation intact for label lookup. Do not patch annotation.tiff border-only."
    after["additional_references"] = []
    after["warning"] = "LabelAtlas baseline. MRI/reference-channel experiments postponed. Reference is a synthetic annotation-border display proxy, not anatomical MRI."
    info["before"] = {k: before.get(k) for k in ["reference_strategy", "labelatlas_display_mode", "additional_references", "warning", "shape", "orientation", "name"]}
    info["after"] = {k: after.get(k) for k in ["reference_strategy", "labelatlas_display_mode", "additional_references", "warning", "shape", "orientation", "name"]}
    info["patched"] = True
    actions.append({"action": "patch_metadata", "path": str(meta_path), "dry_run": dry_run})
    if not dry_run:
        meta_path.write_text(json.dumps(after, indent=2, ensure_ascii=False), encoding="utf-8")
    return info


def _process_atlas(label: str, atlas_dir: Path, backup_root: Path, mods: dict, dry_run: bool, actions: list) -> Dict[str, Any]:
    result: Dict[str, Any] = {"label": label, "atlas_dir": str(atlas_dir), "exists": atlas_dir.exists(), "patched": False}
    if not atlas_dir.exists():
        return result

    nib = mods.get("nibabel")
    tifffile = mods.get("tifffile")
    ann, template_img, ann_src = _load_annotation(atlas_dir, nib, tifffile)
    ref = _make_border_reference(ann, include_outer=True)

    backup_dir = backup_root / label
    for fn in ["reference.nii.gz", "reference.tiff", "metadata.json"]:
        _copy_backup(atlas_dir / fn, backup_dir, actions, dry_run)
    # Also back up hemispheres, but do not rewrite it here. V32.13 zeroing stays as-is.
    _copy_backup(atlas_dir / "hemispheres.tiff", backup_dir, actions, dry_run)

    _write_nifti(ref, atlas_dir / "reference.nii.gz", template_img, nib, dry_run, actions)
    _write_tiff(ref, atlas_dir / "reference.tiff", tifffile, dry_run, actions)
    metadata = _patch_metadata(atlas_dir, dry_run, actions)

    result.update({
        "patched": True,
        "annotation_source": ann_src,
        "shape": list(ann.shape),
        "annotation_nonzero_fraction": float(np.count_nonzero(ann) / ann.size),
        "reference_border_nonzero_fraction": float(np.count_nonzero(ref) / ref.size),
        "reference_dtype": str(ref.dtype),
        "reference_min": int(ref.min()),
        "reference_max": int(ref.max()),
        "metadata": metadata,
    })
    return result


def _restore_latest(project_root: Path) -> int:
    backup_base = project_root / "data" / "output" / "v32_16_border_reference_display_proxy_backups"
    if not backup_base.exists():
        print(f"ERROR: No backup base found: {backup_base}")
        return 2
    candidates = sorted([p for p in backup_base.iterdir() if p.is_dir()], reverse=True)
    if not candidates:
        print(f"ERROR: No backup folders found in: {backup_base}")
        return 2
    latest = candidates[0]
    print(f"Restoring latest V32.16 backup: {latest}")

    mapping = {
        latest / "project_stable": project_root / "data" / "output" / "brainglobe_official_candidate" / "paxinos_watson_rat_40um",
        latest / "cache_stable": Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0",
    }
    restored = 0
    for src_dir, dst_dir in mapping.items():
        if not src_dir.exists():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for fn in ["reference.nii.gz", "reference.tiff", "metadata.json", "hemispheres.tiff"]:
            src = src_dir / fn
            if src.exists():
                shutil.copy2(src, dst_dir / fn)
                print(f"Restored {dst_dir / fn}")
                restored += 1
    print(f"Restored files: {restored}")
    return 0 if restored else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cache-only", action="store_true", help="Patch only the BrainGlobe cache atlas.")
    parser.add_argument("--project-and-cache", action="store_true", help="Patch project official candidate and BrainGlobe cache.")
    parser.add_argument("--restore-latest", action="store_true")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root or os.getcwd()).resolve()
    if args.restore_latest:
        return _restore_latest(project_root)

    stable_name = "paxinos_watson_rat_40um"
    report_dir = project_root / "reports" / "v32_16_border_reference_display_proxy"
    backup_root = project_root / "data" / "output" / "v32_16_border_reference_display_proxy_backups" / _now()
    report_dir.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)

    mods = _import_optional()
    actions = []
    errors = []

    targets = []
    if args.project_and_cache:
        targets.append(("project_stable", project_root / "data" / "output" / "brainglobe_official_candidate" / stable_name))
    # Default and cache-only both patch cache, because ABBA reads this.
    targets.append(("cache_stable", Path.home() / ".brainglobe" / f"{stable_name}_v1.0"))

    results = []
    for label, atlas_dir in targets:
        try:
            results.append(_process_atlas(label, atlas_dir, backup_root, mods, args.dry_run, actions))
        except Exception as e:
            errors.append({"target": label, "atlas_dir": str(atlas_dir), "error": repr(e)})

    passed = len(errors) == 0 and any(r.get("patched") for r in results)
    report = {
        "generated_at": _iso(),
        "passed": passed,
        "dry_run": args.dry_run,
        "mode": "project-and-cache" if args.project_and_cache else "cache-only",
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "backup_root": str(backup_root),
        "purpose": "Create pure border reference display proxy while preserving full annotation for ABBA labels.",
        "important": "Use ZSliced_paxinos_watson_rat_40um_reference as display layer and keep ZSliced_Borders/annotation inactive if ABBA-generated borders show filled panels.",
        "backend_status": {k: {"available": not isinstance(v, Exception), "version": getattr(v, "__version__", None), "error": None if not isinstance(v, Exception) else repr(v)} for k, v in mods.items()},
        "results": results,
        "actions": actions,
        "errors": errors,
    }

    (report_dir / "v32_16_border_reference_display_proxy_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = []
    lines.append("V32.16 Border Reference Display Proxy")
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"PASSED: {passed}")
    lines.append(f"Dry run: {args.dry_run}")
    lines.append(f"Mode: {report['mode']}")
    lines.append(f"Project root: {project_root}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- Restore a pure border-only display source through reference.tiff/reference.nii.gz.")
    lines.append("- Preserve annotation.tiff/annotation.nii.gz as full label volumes for ABBA lookup.")
    lines.append("- No MRI/Waxholm/SIGMA/NeuroRat reference channel is enabled.")
    lines.append("")
    for r in results:
        lines.append(f"Target {r.get('label')}: exists={r.get('exists')} patched={r.get('patched')}")
        if r.get("patched"):
            lines.append(f"  shape={r.get('shape')}")
            lines.append(f"  annotation_nonzero_fraction={r.get('annotation_nonzero_fraction')}")
            lines.append(f"  reference_border_nonzero_fraction={r.get('reference_border_nonzero_fraction')}")
            lines.append(f"  atlas_dir={r.get('atlas_dir')}")
    lines.append("")
    lines.append("ABBA test:")
    lines.append("- Restart Fiji/ABBA completely.")
    lines.append("- Open paxinos_watson_rat_40um.")
    lines.append("- In Sources, activate ZSliced_paxinos_watson_rat_40um_reference.")
    lines.append("- Deactivate ZSliced_Borders_paxinos_watson_rat_40um_annotation if it still creates filled panels.")
    lines.append("- Keep annotation files untouched; this proxy is display-only.")
    lines.append("")
    if errors:
        lines.append("Errors:")
        for e in errors:
            lines.append(f"- {e}")
    else:
        lines.append("Errors: none")
    (report_dir / "v32_16_border_reference_display_proxy_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
