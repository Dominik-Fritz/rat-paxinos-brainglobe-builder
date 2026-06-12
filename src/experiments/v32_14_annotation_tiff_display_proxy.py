
from __future__ import annotations
import argparse
import datetime as _dt
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import tifffile
except Exception as e:
    tifffile = None
    _TIFF_ERR = repr(e)
else:
    _TIFF_ERR = None

try:
    import nibabel as nib
except Exception as e:
    nib = None
    _NIB_ERR = repr(e)
else:
    _NIB_ERR = None

DEFAULT_PROJECT_ROOT = Path(r"G:\rat-paxinos-brainglobe-builder")
DEFAULT_CACHE_ROOT = Path.home() / ".brainglobe"
ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR_NAME = "paxinos_watson_rat_40um_v1.0"


def now_stamp() -> str:
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


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def atlas_dirs(project_root: Path, target: str) -> List[Tuple[str, Path]]:
    dirs: List[Tuple[str, Path]] = []
    project_dir = project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME
    cache_dir = DEFAULT_CACHE_ROOT / CACHE_DIR_NAME
    if target in ("project", "both"):
        dirs.append(("project_stable", project_dir))
    if target in ("cache", "both"):
        dirs.append(("cache_stable", cache_dir))
    return dirs


def resolve_shape_dtype(atlas_dir: Path) -> Tuple[Tuple[int, int, int], np.dtype, str]:
    tiff_path = atlas_dir / "annotation.tiff"
    nii_path = atlas_dir / "annotation.nii.gz"
    if tiff_path.exists():
        if tifffile is None:
            raise RuntimeError(f"tifffile missing: {_TIFF_ERR}")
        arr = tifffile.memmap(str(tiff_path))
        return tuple(int(x) for x in arr.shape), np.dtype(arr.dtype), "annotation.tiff"
    if nii_path.exists():
        if nib is None:
            raise RuntimeError(f"nibabel missing: {_NIB_ERR}")
        img = nib.load(str(nii_path))
        data = np.asanyarray(img.dataobj)
        return tuple(int(x) for x in data.shape), np.dtype(data.dtype), "annotation.nii.gz"
    raise FileNotFoundError(f"No annotation.tiff or annotation.nii.gz found in {atlas_dir}")


def load_annotation(atlas_dir: Path) -> Tuple[np.ndarray, str]:
    tiff_path = atlas_dir / "annotation.tiff"
    nii_path = atlas_dir / "annotation.nii.gz"
    if tiff_path.exists():
        if tifffile is None:
            raise RuntimeError(f"tifffile missing: {_TIFF_ERR}")
        arr = tifffile.imread(str(tiff_path))
        return np.asarray(arr), "annotation.tiff"
    if nii_path.exists():
        if nib is None:
            raise RuntimeError(f"nibabel missing: {_NIB_ERR}")
        img = nib.load(str(nii_path))
        arr = np.asanyarray(img.dataobj)
        return np.asarray(arr), "annotation.nii.gz"
    raise FileNotFoundError(f"No annotation volume found in {atlas_dir}")


def make_boundary_label_volume(arr: np.ndarray) -> np.ndarray:
    """Keep only voxels touching a different label; preserve IDs at border voxels."""
    if arr.ndim != 3:
        raise ValueError(f"Expected 3D annotation, got shape {arr.shape}")
    arr = np.asarray(arr)
    mask = arr != 0
    border = np.zeros(arr.shape, dtype=bool)

    # Differences between adjacent voxels on each axis. Mark both sides of the interface,
    # but only where the original annotation is nonzero.
    for axis in range(3):
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[axis] = slice(0, -1)
        sl1[axis] = slice(1, None)
        a0 = tuple(sl0)
        a1 = tuple(sl1)
        diff = arr[a0] != arr[a1]
        border[a0] |= diff & mask[a0]
        border[a1] |= diff & mask[a1]

    out = np.zeros(arr.shape, dtype=arr.dtype)
    out[border & mask] = arr[border & mask]
    return out


def backup_file(src: Path, backup_dir: Path, label: str, actions: List[Dict[str, Any]], dry_run: bool) -> Optional[Path]:
    if not src.exists():
        actions.append({"action": "backup_skip_missing", "src": str(src), "label": label})
        return None
    dst = backup_dir / label / src.name
    actions.append({"action": "backup_file", "src": str(src), "dst": str(dst), "dry_run": dry_run})
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dst


def write_tiff(path: Path, arr: np.ndarray, dry_run: bool, actions: List[Dict[str, Any]], mode: str) -> None:
    if tifffile is None:
        raise RuntimeError(f"tifffile missing: {_TIFF_ERR}")
    actions.append({
        "action": f"write_{mode}_annotation_tiff",
        "path": str(path),
        "shape": list(map(int, arr.shape)),
        "dtype": str(arr.dtype),
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size) if arr.size else 0.0,
        "dry_run": dry_run,
    })
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        tifffile.imwrite(str(path), arr, photometric="minisblack")


def patch_metadata(atlas_dir: Path, mode: str, dry_run: bool, actions: List[Dict[str, Any]]) -> None:
    meta_path = atlas_dir / "metadata.json"
    if not meta_path.exists():
        return
    meta = read_json(meta_path)
    before = {
        "reference_strategy": meta.get("reference_strategy"),
        "annotation_tiff_display_proxy": meta.get("annotation_tiff_display_proxy"),
        "name": meta.get("name"),
        "shape": meta.get("shape"),
        "orientation": meta.get("orientation"),
    }
    meta["annotation_tiff_display_proxy"] = {
        "active": True,
        "mode": mode,
        "annotation_nii_gz_preserved": True,
        "purpose": "Hide ABBA filled annotation display source while preserving annotation.nii.gz and structures.",
        "warning": "This is an ABBA display workaround. Restore if ABBA needs annotation.tiff for label lookup.",
    }
    after = {
        "reference_strategy": meta.get("reference_strategy"),
        "annotation_tiff_display_proxy": meta.get("annotation_tiff_display_proxy"),
        "name": meta.get("name"),
        "shape": meta.get("shape"),
        "orientation": meta.get("orientation"),
    }
    actions.append({"action": "patch_metadata", "path": str(meta_path), "before": before, "after": after, "dry_run": dry_run})
    if not dry_run:
        write_json(meta_path, meta)


def run_patch(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = Path(args.project_root)
    report_dir = project_root / "reports" / "v32_14_annotation_tiff_display_proxy"
    backup_root = project_root / "data" / "output" / "v32_14_annotation_tiff_display_proxy_backups" / now_stamp()
    actions: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    results: Dict[str, Any] = {}

    for label, adir in atlas_dirs(project_root, args.target):
        item: Dict[str, Any] = {"atlas_dir": str(adir), "exists": adir.exists(), "patched": False}
        results[label] = item
        try:
            if not adir.exists():
                continue
            shape, dtype, source = resolve_shape_dtype(adir)
            item.update({"shape": list(shape), "dtype": str(dtype), "source": source})
            # Back up the risky display file plus metadata. Annotation NIfTI is intentionally NOT modified,
            # but we back up annotation.tiff because that is the display source being tested.
            backup_file(adir / "annotation.tiff", backup_root, label, actions, args.dry_run)
            backup_file(adir / "metadata.json", backup_root, label, actions, args.dry_run)

            if args.mode == "zero":
                arr = np.zeros(shape, dtype=dtype)
                item["new_nonzero_fraction"] = 0.0
                write_tiff(adir / "annotation.tiff", arr, args.dry_run, actions, "zero")
            elif args.mode == "border":
                arr, loaded_from = load_annotation(adir)
                item["loaded_from"] = loaded_from
                item["old_nonzero_fraction"] = float(np.count_nonzero(arr) / arr.size) if arr.size else 0.0
                out = make_boundary_label_volume(arr)
                item["new_nonzero_fraction"] = float(np.count_nonzero(out) / out.size) if out.size else 0.0
                write_tiff(adir / "annotation.tiff", out, args.dry_run, actions, "border_only")
            else:
                raise ValueError(f"Unsupported mode {args.mode}")
            patch_metadata(adir, args.mode, args.dry_run, actions)
            item["patched"] = not args.dry_run
        except Exception as e:
            errors.append({"atlas": label, "dir": str(adir), "error": repr(e)})

    report = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "passed": len(errors) == 0,
        "dry_run": bool(args.dry_run),
        "mode": args.mode,
        "target": args.target,
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "backup_root": str(backup_root),
        "stable_atlas_name": ATLAS_NAME,
        "results": results,
        "actions": actions,
        "errors": errors,
        "important_note": (
            "annotation.nii.gz, structures.json/csv, reference files, and raw data are intentionally preserved. "
            "Only annotation.tiff is patched as an ABBA display-source workaround. "
            "If ABBA label lookup breaks, restore immediately."
        ),
    }
    if not args.dry_run:
        manifest = {
            "created_at": report["generated_at"],
            "mode": args.mode,
            "target": args.target,
            "actions": actions,
        }
        write_json(backup_root / "restore_manifest.json", manifest)

    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "v32_14_annotation_tiff_display_proxy_report.json", report)
    summary = make_summary(report)
    (report_dir / "v32_14_annotation_tiff_display_proxy_summary.txt").write_text(summary, encoding="utf-8")
    print(summary)
    return report


def make_summary(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("V32.14 Annotation TIFF display proxy")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append(f"Dry run: {report.get('dry_run')}")
    lines.append(f"Mode: {report.get('mode')}")
    lines.append(f"Target: {report.get('target')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Report dir: {report.get('report_dir')}")
    lines.append("")
    lines.append("Purpose:")
    lines.append("- ABBA still shows every third filled label view after reference/hemispheres were zeroed.")
    lines.append("- This tests whether the filled duplicate comes from annotation.tiff display rendering.")
    lines.append("- annotation.nii.gz and structures remain untouched.")
    lines.append("")
    lines.append("Results:")
    for key, item in report.get("results", {}).items():
        lines.append(f"- {key}: exists={item.get('exists')} patched={item.get('patched')} dir={item.get('atlas_dir')}")
        if item.get("shape"):
            lines.append(f"  shape={item.get('shape')} source={item.get('source')} dtype={item.get('dtype')}")
        if item.get("old_nonzero_fraction") is not None:
            lines.append(f"  old_nonzero_fraction={item.get('old_nonzero_fraction'):.6f}")
        if item.get("new_nonzero_fraction") is not None:
            lines.append(f"  new_nonzero_fraction={item.get('new_nonzero_fraction'):.6f}")
    lines.append("")
    if report.get("errors"):
        lines.append("Errors:")
        for e in report.get("errors", []):
            lines.append(f"- {e}")
    else:
        lines.append("Errors: none")
    lines.append("")
    lines.append("ABBA/Fiji next step:")
    lines.append("- Restart Fiji/ABBA completely.")
    lines.append("- Open paxinos_watson_rat_40um.")
    lines.append("- If every third filled view becomes border-only or disappears, annotation.tiff was the display source.")
    lines.append("- If ABBA label lookup/regions break, restore immediately.")
    lines.append("")
    lines.append("Restore:")
    lines.append("- RUN_V32_14_RESTORE_LATEST_BACKUP.bat")
    return "\n".join(lines)


def restore_latest(args: argparse.Namespace) -> Dict[str, Any]:
    project_root = Path(args.project_root)
    backup_base = project_root / "data" / "output" / "v32_14_annotation_tiff_display_proxy_backups"
    report_dir = project_root / "reports" / "v32_14_annotation_tiff_display_proxy"
    candidates = sorted([p for p in backup_base.iterdir() if p.is_dir()]) if backup_base.exists() else []
    if not candidates:
        raise FileNotFoundError(f"No backup directories found in {backup_base}")
    latest = candidates[-1]
    manifest_path = latest / "restore_manifest.json"
    manifest = read_json(manifest_path)
    actions = manifest.get("actions", [])
    restore_actions = []
    errors = []
    # Only restore files we backed up.
    for act in actions:
        if act.get("action") != "backup_file":
            continue
        src_original = Path(act["src"])
        backup = Path(act["dst"])
        try:
            if backup.exists():
                restore_actions.append({"action": "restore_file", "src": str(backup), "dst": str(src_original)})
                src_original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, src_original)
        except Exception as e:
            errors.append({"backup": str(backup), "dst": str(src_original), "error": repr(e)})
    report = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "passed": len(errors) == 0,
        "project_root": str(project_root),
        "restored_from": str(latest),
        "restore_actions": restore_actions,
        "errors": errors,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "v32_14_restore_latest_backup_report.json", report)
    lines = ["V32.14 restore latest backup", "="*72, f"PASSED: {report['passed']}", f"Restored from: {latest}", f"Files restored: {len(restore_actions)}"]
    if errors:
        lines.append("Errors:")
        lines.extend([str(e) for e in errors])
    text = "\n".join(lines)
    (report_dir / "v32_14_restore_latest_backup_summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="V32.14 Annotation TIFF display proxy / restore")
    ap.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    ap.add_argument("--mode", choices=["border", "zero", "restore"], default="border")
    ap.add_argument("--target", choices=["cache", "project", "both"], default="cache")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.mode == "restore":
        restore_latest(args)
    else:
        run_patch(args)


if __name__ == "__main__":
    main()
