from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import nibabel as nib
except Exception as e:
    nib = None
    _NIB_ERR = repr(e)
else:
    _NIB_ERR = None

try:
    import tifffile
except Exception as e:
    tifffile = None
    _TIFF_ERR = repr(e)
else:
    _TIFF_ERR = None

DEFAULT_PROJECT_ROOT = Path(r"G:\rat-paxinos-brainglobe-builder")
CACHE_ROOT = Path.home() / ".brainglobe"
ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR_NAME = "paxinos_watson_rat_40um_v1.0"


def stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def project_atlas_dir(project_root: Path) -> Path:
    return project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME


def cache_atlas_dir() -> Path:
    return CACHE_ROOT / CACHE_DIR_NAME


def ensure_deps() -> None:
    if nib is None:
        raise RuntimeError(f"nibabel is required but missing: {_NIB_ERR}")
    if tifffile is None:
        raise RuntimeError(f"tifffile is required but missing: {_TIFF_ERR}")


def backup_file(src: Path, backup_dir: Path, rel_label: str, actions: List[Dict[str, Any]]) -> Optional[Path]:
    if not src.exists():
        actions.append({"action": "backup_skip_missing", "src": str(src), "label": rel_label})
        return None
    dst = backup_dir / rel_label / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    actions.append({"action": "backup_file", "src": str(src), "dst": str(dst)})
    return dst


def load_nifti_annotation(path: Path) -> Tuple[np.ndarray, Dict[str, Any]]:
    ensure_deps()
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj)
    # BrainGlobe/ABBA works best with integer labels. Existing Paxinos labels are uint16.
    if np.issubdtype(data.dtype, np.integer):
        arr = np.asarray(data)
    else:
        arr = np.rint(np.asarray(data)).astype(np.int64)
    max_val = int(np.nanmax(arr)) if arr.size else 0
    min_val = int(np.nanmin(arr)) if arr.size else 0
    if min_val < 0:
        raise ValueError(f"Annotation contains negative labels in {path}: min={min_val}")
    if max_val <= np.iinfo(np.uint16).max:
        arr = arr.astype(np.uint16, copy=False)
    else:
        arr = arr.astype(np.uint32, copy=False)
    stats = volume_stats(arr)
    stats.update({"source": str(path), "affine": np.asarray(img.affine).tolist()})
    return arr, stats


def load_tiff(path: Path) -> Tuple[np.ndarray, Dict[str, Any]]:
    ensure_deps()
    arr = tifffile.imread(str(path))
    arr = np.asarray(arr)
    return arr, volume_stats(arr) | {"source": str(path)}


def volume_stats(arr: np.ndarray) -> Dict[str, Any]:
    return {
        "shape": [int(x) for x in arr.shape],
        "dtype": str(arr.dtype),
        "min": float(np.nanmin(arr)) if arr.size else None,
        "max": float(np.nanmax(arr)) if arr.size else None,
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size) if arr.size else 0.0,
        "unique_count_estimate": int(len(np.unique(arr))) if arr.size and arr.size < 30_000_000 else None,
    }


def write_full_annotation_tiff(path: Path, arr: np.ndarray, actions: List[Dict[str, Any]]) -> None:
    ensure_deps()
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(str(path), arr, photometric="minisblack")
    actions.append({"action": "write_full_annotation_tiff", "path": str(path), "stats": volume_stats(arr)})


def copy_file(src: Path, dst: Path, actions: List[Dict[str, Any]]) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    actions.append({"action": "copy_file", "src": str(src), "dst": str(dst)})


def find_latest_v32_14_backup(project_root: Path, target_label: str = "cache_stable") -> Optional[Path]:
    base = project_root / "data" / "output" / "v32_14_annotation_tiff_display_proxy_backups"
    if not base.exists():
        return None
    candidates = sorted([p for p in base.iterdir() if p.is_dir()])
    for cand in reversed(candidates):
        # V32.14 backup layout: <stamp>/<target_label>/annotation.tiff
        if (cand / target_label / "annotation.tiff").exists():
            return cand
    return None


def cleanup_metadata(meta_path: Path, actions: List[Dict[str, Any]], source_note: str) -> Dict[str, Any]:
    before = read_json(meta_path)
    after = dict(before)
    after.pop("annotation_tiff_display_proxy", None)
    after["reference_strategy"] = "strict_label_only_zero_reference_no_mri"
    after["additional_references"] = []
    after["labelatlas_rebaseline"] = {
        "active": True,
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "annotation_tiff_restored_full_label_volume": True,
        "source_note": source_note,
        "mri_reference_channels_postponed": True,
        "warning": "Do not patch annotation.tiff to border-only again; ABBA may use it for label display/lookup.",
    }
    write_json(meta_path, after)
    actions.append({
        "action": "cleanup_metadata",
        "path": str(meta_path),
        "before_keys": sorted(before.keys()),
        "after_keys": sorted(after.keys()),
        "source_note": source_note,
    })
    return {"before": before, "after": after}


def restore_cache_auto(project_root: Path, report_dir: Path, actions: List[Dict[str, Any]], backup_root: Path) -> Dict[str, Any]:
    pdir = project_atlas_dir(project_root)
    cdir = cache_atlas_dir()
    result: Dict[str, Any] = {
        "project_atlas_dir": str(pdir),
        "cache_atlas_dir": str(cdir),
        "project_exists": pdir.exists(),
        "cache_exists": cdir.exists(),
        "method": None,
    }
    if not cdir.exists():
        raise FileNotFoundError(f"Cache atlas does not exist: {cdir}")
    # Back up risky/current cache files first.
    for fn in ["annotation.tiff", "annotation.nii.gz", "metadata.json"]:
        backup_file(cdir / fn, backup_root, "current_cache_before_restore", actions)

    latest = find_latest_v32_14_backup(project_root, "cache_stable")
    if latest is not None:
        src_tiff = latest / "cache_stable" / "annotation.tiff"
        src_meta = latest / "cache_stable" / "metadata.json"
        copy_file(src_tiff, cdir / "annotation.tiff", actions)
        if src_meta.exists():
            copy_file(src_meta, cdir / "metadata.json", actions)
        result["method"] = "restore_latest_v32_14_cache_backup"
        result["restored_from"] = str(latest)
        # Make sure the proxy metadata flag is removed even if the restored metadata is unexpected.
        meta_info = cleanup_metadata(cdir / "metadata.json", actions, "restored annotation.tiff from latest V32.14 cache backup")
        result["metadata_cleanup"] = {"before_reference_strategy": meta_info["before"].get("reference_strategy"), "after_reference_strategy": meta_info["after"].get("reference_strategy")}
        arr, stats = load_tiff(cdir / "annotation.tiff")
        result["restored_annotation_tiff_stats"] = stats
        # Also restore annotation.nii.gz from project if present; V32.14 never changed it, but this keeps cache/project aligned.
        if (pdir / "annotation.nii.gz").exists():
            copy_file(pdir / "annotation.nii.gz", cdir / "annotation.nii.gz", actions)
        return result

    # Fallback: restore from project stable annotation.tiff if it exists and looks like a full label volume.
    if (pdir / "annotation.tiff").exists():
        arr, stats = load_tiff(pdir / "annotation.tiff")
        # Border-only files usually have a low nonzero fraction. The real Paxinos annotation is around 0.43.
        if stats.get("nonzero_fraction", 0.0) > 0.20:
            copy_file(pdir / "annotation.tiff", cdir / "annotation.tiff", actions)
            if (pdir / "annotation.nii.gz").exists():
                copy_file(pdir / "annotation.nii.gz", cdir / "annotation.nii.gz", actions)
            result["method"] = "copy_project_full_annotation_tiff_to_cache"
            result["project_annotation_tiff_stats"] = stats
            meta_info = cleanup_metadata(cdir / "metadata.json", actions, "copied full annotation.tiff from project stable atlas")
            result["metadata_cleanup"] = {"before_reference_strategy": meta_info["before"].get("reference_strategy"), "after_reference_strategy": meta_info["after"].get("reference_strategy")}
            arr2, stats2 = load_tiff(cdir / "annotation.tiff")
            result["restored_annotation_tiff_stats"] = stats2
            return result

    # Last fallback: rebuild full annotation.tiff from annotation.nii.gz.
    nifti_source = pdir / "annotation.nii.gz"
    if not nifti_source.exists():
        nifti_source = cdir / "annotation.nii.gz"
    arr, stats = load_nifti_annotation(nifti_source)
    write_full_annotation_tiff(cdir / "annotation.tiff", arr, actions)
    result["method"] = "rebuild_cache_annotation_tiff_from_annotation_nifti"
    result["nifti_source_stats"] = stats
    meta_info = cleanup_metadata(cdir / "metadata.json", actions, f"rebuilt annotation.tiff from {nifti_source}")
    result["metadata_cleanup"] = {"before_reference_strategy": meta_info["before"].get("reference_strategy"), "after_reference_strategy": meta_info["after"].get("reference_strategy")}
    result["restored_annotation_tiff_stats"] = volume_stats(arr)
    return result


def restore_project_and_cache_from_nifti(project_root: Path, actions: List[Dict[str, Any]], backup_root: Path) -> Dict[str, Any]:
    """Aggressive repair: rebuild full annotation.tiff in both project and cache from project annotation.nii.gz."""
    pdir = project_atlas_dir(project_root)
    cdir = cache_atlas_dir()
    if not (pdir / "annotation.nii.gz").exists():
        raise FileNotFoundError(f"Project annotation.nii.gz missing: {pdir / 'annotation.nii.gz'}")
    for label, adir in [("project", pdir), ("cache", cdir)]:
        for fn in ["annotation.tiff", "annotation.nii.gz", "metadata.json"]:
            backup_file(adir / fn, backup_root, f"current_{label}_before_aggressive_restore", actions)
    arr, stats = load_nifti_annotation(pdir / "annotation.nii.gz")
    write_full_annotation_tiff(pdir / "annotation.tiff", arr, actions)
    copy_file(pdir / "annotation.nii.gz", cdir / "annotation.nii.gz", actions)
    write_full_annotation_tiff(cdir / "annotation.tiff", arr, actions)
    cleanup_metadata(pdir / "metadata.json", actions, "rebuilt project/cache annotation.tiff from project annotation.nii.gz")
    cleanup_metadata(cdir / "metadata.json", actions, "rebuilt project/cache annotation.tiff from project annotation.nii.gz")
    return {"method": "aggressive_project_and_cache_rebuild_from_project_nifti", "nifti_stats": stats}


def run(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = Path(args.project_root)
    report_dir = project_root / "reports" / "v32_15_emergency_restore_label_annotation"
    backup_root = project_root / "data" / "output" / "v32_15_emergency_restore_label_annotation_backups" / stamp()
    actions: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    result: Dict[str, Any] = {}
    try:
        if args.mode == "cache-auto":
            result = restore_cache_auto(project_root, report_dir, actions, backup_root)
        elif args.mode == "aggressive-both-from-nifti":
            result = restore_project_and_cache_from_nifti(project_root, actions, backup_root)
        else:
            raise ValueError(f"Unsupported mode: {args.mode}")
    except Exception as e:
        errors.append({"error": repr(e)})

    report = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "passed": len(errors) == 0,
        "mode": args.mode,
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "backup_root": str(backup_root),
        "stable_atlas_name": ATLAS_NAME,
        "result": result,
        "actions": actions,
        "errors": errors,
        "important_note": (
            "This is an emergency rollback after V32.14 annotation.tiff border-only made ABBA display worse. "
            "It restores annotation.tiff as a full label volume. reference/hemispheres zeroing from V32.13 is not changed. "
            "If filled ABBA views remain after this, they are ABBA's normal annotation rendering and must be hidden in the viewer/workflow, not by corrupting annotation.tiff."
        ),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "v32_15_emergency_restore_label_annotation_report.json", report)
    summary = make_summary(report)
    (report_dir / "v32_15_emergency_restore_label_annotation_summary.txt").write_text(summary, encoding="utf-8")
    print(summary)
    return report


def make_summary(report: Dict[str, Any]) -> str:
    result = report.get("result", {}) or {}
    lines: List[str] = []
    lines.append("V32.15 Emergency restore LabelAtlas annotation display")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append(f"Mode: {report.get('mode')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Report dir: {report.get('report_dir')}")
    lines.append(f"Backup root: {report.get('backup_root')}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- Undo the failed V32.14 annotation.tiff display proxy.")
    lines.append("- Restore annotation.tiff to a full label volume so ABBA label display/lookup is not broken.")
    lines.append("- Keep the V32.13 zero-reference/no-MRI baseline; do not re-enable MRI/reference helper channels.")
    lines.append("")
    lines.append(f"Method used: {result.get('method')}")
    if result.get("restored_from"):
        lines.append(f"Restored from: {result.get('restored_from')}")
    stats = result.get("restored_annotation_tiff_stats") or result.get("nifti_stats") or result.get("project_annotation_tiff_stats")
    if stats:
        lines.append(f"Annotation stats: shape={stats.get('shape')} dtype={stats.get('dtype')} nonzero_fraction={stats.get('nonzero_fraction')} min={stats.get('min')} max={stats.get('max')}")
    lines.append("")
    if report.get("errors"):
        lines.append("Errors:")
        for e in report.get("errors", []):
            lines.append(f"- {e}")
    else:
        lines.append("Errors: none")
    lines.append("")
    lines.append("ABBA/Fiji next step:")
    lines.append("- Close Fiji/ABBA completely before running this package; after running, restart Fiji/ABBA.")
    lines.append("- Open paxinos_watson_rat_40um only.")
    lines.append("- This should undo the V32.14 worsening. If filled annotation views remain, do not patch annotation.tiff again; hide/ignore that ABBA source instead.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="V32.15 emergency restore full LabelAtlas annotation.tiff after V32.14 display proxy test")
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    parser.add_argument("--mode", choices=["cache-auto", "aggressive-both-from-nifti"], default="cache-auto")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
