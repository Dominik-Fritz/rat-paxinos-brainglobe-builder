#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V44 patch: hide ABBA native 'borders' display channel for BrainGlobe atlases.

This patch modifies abba_python/abba_map.py, not the atlas data.

Reason:
abba_python's AbbaMap.initialize() explicitly adds a native display channel:

    image_keys.add(JString('borders'))
    structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac)
    self.maxValues['borders'] = 256

That is exactly why ABBA shows 'borders (Ch. 3)' even though the atlas already has
our useful Ch0/Ch1/Ch2 display channels.

V44 disables only that display source registration. It leaves:

    self.annotation_sac
    getLabelImage()

unchanged. Therefore annotation labels remain available for mapping/export.

This is an ABBA Python loader patch. It does not touch:
    annotation.tiff
    annotation.nii.gz
    structures.json
    reference.tiff
    soft_region_fill_reference
    distance_to_2d_outline_reference

Yes, we finally patch the right thing. Software archaeology occasionally finds the fossil.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPORT_DIR_NAME = "v44_hide_abba_native_borders_channel"
MARKER = "# V44_HIDE_NATIVE_BORDERS_DISPLAY_CHANNEL"
START_MARKER = "# V44_HIDE_NATIVE_BORDERS_DISPLAY_CHANNEL_START"
END_MARKER = "# V44_HIDE_NATIVE_BORDERS_DISPLAY_CHANNEL_END"


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def project_root_from_arg(arg: Optional[str]) -> Path:
    return Path(arg).resolve() if arg else Path.cwd().resolve()


def is_abba_map_file(path: Path) -> bool:
    return (
        path.name == "abba_map.py"
        and path.parent.name == "abba_python"
        and path.exists()
        and path.is_file()
    )


def candidate_python_exes(project_root: Path) -> List[Path]:
    candidates: List[Path] = []

    for p in [
        project_root / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]:
        if p.exists():
            candidates.append(p)

    home = Path.home()
    for base in [
        home / "miniconda3" / "envs",
        home / "anaconda3" / "envs",
        home / "AppData" / "Local" / "Programs" / "Python",
        home / "AppData" / "Local" / "Programs",
        home / "AppData" / "Local",
    ]:
        if base.exists():
            try:
                candidates.extend(base.glob("**/python.exe"))
            except Exception:
                pass

    # De-duplicate while preserving order.
    out: List[Path] = []
    seen = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp).lower()
        if key not in seen and rp.exists():
            seen.add(key)
            out.append(rp)
    return out


def import_probe(python_exe: Path) -> Optional[Path]:
    code = (
        "import inspect, abba_python.abba_map as m; "
        "print(inspect.getfile(m))"
    )
    try:
        proc = subprocess.run(
            [str(python_exe), "-c", code],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if proc.returncode == 0:
            p = Path(proc.stdout.strip())
            if is_abba_map_file(p):
                return p
    except Exception:
        return None
    return None


def scan_common_locations(project_root: Path, deep_search: bool) -> List[Path]:
    candidates: List[Path] = []

    env_file = os.environ.get("ABBA_MAP_PY")
    if env_file:
        candidates.append(Path(env_file))

    env_root = os.environ.get("ABBA_PYTHON_ROOT")
    if env_root:
        candidates.extend(Path(env_root).glob("**/abba_python/abba_map.py"))

    # Project-local or unpacked ABBA python.
    for p in [
        project_root / "abba_python" / "abba_map.py",
        project_root / "src" / "abba_python" / "abba_map.py",
        project_root / ".venv" / "Lib" / "site-packages" / "abba_python" / "abba_map.py",
    ]:
        candidates.append(p)

    home = Path.home()
    common_roots = [
        home / "AppData" / "Local" / "Programs" / "Python",
        home / "AppData" / "Roaming" / "Python",
        home / "miniconda3" / "envs",
        home / "anaconda3" / "envs",
        home / "AppData" / "Local" / "Programs",
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")),
        Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))),
    ]

    patterns = [
        "**/Lib/site-packages/abba_python/abba_map.py",
        "**/site-packages/abba_python/abba_map.py",
    ]

    for root in common_roots:
        if not root.exists():
            continue
        for pat in patterns:
            try:
                candidates.extend(root.glob(pat))
            except Exception:
                pass

    if deep_search:
        # Slow fallback. Use only if normal search failed.
        for root in [home, Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))]:
            if root.exists():
                try:
                    candidates.extend(root.glob("**/abba_python/abba_map.py"))
                except Exception:
                    pass

    # De-duplicate.
    out: List[Path] = []
    seen = set()
    for p in candidates:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp).lower()
        if key not in seen and is_abba_map_file(rp):
            seen.add(key)
            out.append(rp)
    return out


def discover_abba_map_files(project_root: Path, explicit: Optional[str], deep_search: bool) -> List[Path]:
    files: List[Path] = []

    if explicit:
        p = Path(explicit).expanduser()
        if is_abba_map_file(p):
            files.append(p.resolve())

    for py in candidate_python_exes(project_root):
        hit = import_probe(py)
        if hit:
            files.append(hit)

    files.extend(scan_common_locations(project_root, deep_search=deep_search))

    out: List[Path] = []
    seen = set()
    for p in files:
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp).lower()
        if key not in seen and is_abba_map_file(rp):
            seen.add(key)
            out.append(rp)
    return out


def active_borders_lines(text: str) -> Dict[str, bool]:
    # Ignore commented-out lines.
    image_key = False
    structural = False
    maxval = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "image_keys.add" in stripped and "borders" in stripped:
            image_key = True
        if "structural_images" in stripped and "borders" in stripped and "=" in stripped:
            structural = True
        if "maxValues" in stripped and "borders" in stripped and "=" in stripped:
            maxval = True
    return {
        "active_image_keys_add_borders": image_key,
        "active_structural_images_borders": structural,
        "active_maxvalues_borders": maxval,
    }


def already_patched(text: str) -> bool:
    flags = active_borders_lines(text)
    return MARKER in text and not any(flags.values())


def patch_text(text: str) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "changed": False,
        "already_patched": already_patched(text),
        "errors": [],
        "before_flags": active_borders_lines(text),
        "after_flags": None,
    }

    if report["already_patched"]:
        report["after_flags"] = active_borders_lines(text)
        return {"text": text, "report": report}

    new = text

    # Patch exact current abba_python pattern.
    patterns = [
        (
            r"(?m)^(\s*)image_keys\.add\(JString\('borders'\)\)\s*$",
            (
                r"\1" + START_MARKER + "\n"
                r"\1# Native ABBA borders display channel disabled by V44.\n"
                r"\1# The label image remains available through self.annotation_sac / getLabelImage().\n"
                r"\1# image_keys.add(JString('borders'))\n"
                r"\1" + END_MARKER
            ),
            "image_keys.add(JString('borders'))",
        ),
        (
            r"(?m)^(\s*)structural_images\['borders'\]\s*=\s*SourceVoxelProcessor\.getBorders\(self\.annotation_sac\)\s*$",
            (
                r"\1" + START_MARKER + "\n"
                r"\1# structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac)\n"
                r"\1" + END_MARKER
            ),
            "structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac)",
        ),
        (
            r"(?m)^(\s*)self\.maxValues\['borders'\]\s*=\s*256.*$",
            (
                r"\1" + START_MARKER + "\n"
                r"\1# self.maxValues['borders'] = 256\n"
                r"\1" + END_MARKER
            ),
            "self.maxValues['borders'] = 256",
        ),
    ]

    for rx, repl, label in patterns:
        new2, n = re.subn(rx, repl, new, count=1)
        if n > 0:
            new = new2
            report["changed"] = True
        else:
            # Some ABBA versions are minified to one line. Handle simple one-line fallback below.
            report.setdefault("missing_patterns", []).append(label)

    # One-line fallback for the raw 0.11 abba_map.py style.
    if active_borders_lines(new)["active_image_keys_add_borders"]:
        new2 = new.replace(
            "image_keys.add(JString('borders'))",
            f"{START_MARKER} # image_keys.add(JString('borders')) disabled by V44 {END_MARKER}",
            1,
        )
        if new2 != new:
            new = new2
            report["changed"] = True

    if active_borders_lines(new)["active_structural_images_borders"]:
        new2 = new.replace(
            "structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac)",
            f"{START_MARKER} # structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac) disabled by V44 {END_MARKER}",
            1,
        )
        if new2 != new:
            new = new2
            report["changed"] = True

    if active_borders_lines(new)["active_maxvalues_borders"]:
        new2 = new.replace(
            "self.maxValues['borders'] = 256 # we know this one.",
            f"{START_MARKER} # self.maxValues['borders'] = 256 disabled by V44 {END_MARKER}",
            1,
        )
        new2 = new2.replace(
            "self.maxValues['borders'] = 256",
            f"{START_MARKER} # self.maxValues['borders'] = 256 disabled by V44 {END_MARKER}",
            1,
        )
        if new2 != new:
            new = new2
            report["changed"] = True

    flags_after = active_borders_lines(new)
    report["after_flags"] = flags_after

    if any(flags_after.values()):
        report["errors"].append(
            "V44 could not fully disable active borders lines in abba_map.py."
        )

    if MARKER not in new:
        # Add a header marker if the exact line patches used only one-line fallback.
        new = (
            f"# {MARKER}: native ABBA borders display source disabled; "
            "label image remains available via getLabelImage().\n"
            + new
        )
        report["changed"] = True

    return {"text": new, "report": report}


def patch_one(path: Path, project_root: Path, dry_run: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "patched": False,
        "already_patched": False,
        "backup": None,
        "dry_run": dry_run,
        "errors": [],
    }

    if not is_abba_map_file(path):
        result["errors"].append("Not an abba_python/abba_map.py file")
        return result

    text = path.read_text(encoding="utf-8", errors="replace")
    patched = patch_text(text)
    new_text = patched["text"]
    pr = patched["report"]
    result.update(pr)

    if pr.get("errors"):
        result["errors"].extend(pr["errors"])
        return result

    if pr.get("already_patched"):
        result["already_patched"] = True
        result["patched"] = True
        return result

    if not pr.get("changed"):
        result["errors"].append("No patch changes were made")
        return result

    backup_dir = project_root / "backups" / REPORT_DIR_NAME / stamp()
    backup_path = backup_dir / f"{path.name}.backup"
    result["backup"] = str(backup_path)

    if dry_run:
        result["patched"] = True
        result["dry_run_would_write"] = True
        return result

    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    path.write_text(new_text, encoding="utf-8")
    result["patched"] = True
    return result


def validate_one(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "valid": False,
        "flags": {},
        "errors": [],
    }

    if not is_abba_map_file(path):
        result["errors"].append("Not an abba_python/abba_map.py file")
        return result

    text = path.read_text(encoding="utf-8", errors="replace")
    flags = active_borders_lines(text)
    result["flags"] = flags
    result["has_v44_marker"] = MARKER in text or START_MARKER in text
    result["valid"] = not any(flags.values())

    if not result["valid"]:
        result["errors"].append("Active borders registration lines still present")
    return result


def write_report(project_root: Path, report: Dict[str, Any]) -> None:
    report_dir = project_root / "reports" / REPORT_DIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)

    (report_dir / "v44_hide_abba_native_borders_channel_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines: List[str] = []
    lines.append("# V44 Hide ABBA Native Borders Channel Report\n\n")
    lines.append(f"- Generated: `{report['generated_at']}`\n")
    lines.append(f"- Project root: `{report['project_root']}`\n")
    lines.append(f"- Mode: `{report['mode']}`\n")
    lines.append(f"- PASSED: `{report['passed']}`\n\n")

    lines.append("## What was patched\n\n")
    lines.append("V44 patches `abba_python/abba_map.py` so the native `borders` display source is not registered in ABBA.\n\n")
    lines.append("Disabled active lines:\n\n")
    lines.append("```python\n")
    lines.append("image_keys.add(JString('borders'))\n")
    lines.append("structural_images['borders'] = SourceVoxelProcessor.getBorders(self.annotation_sac)\n")
    lines.append("self.maxValues['borders'] = 256\n")
    lines.append("```\n\n")
    lines.append("The annotation source itself remains intact through `self.annotation_sac` and `getLabelImage()`.\n\n")

    lines.append("## Results\n\n")
    for item in report.get("results", []):
        lines.append(f"### `{item.get('path')}`\n\n")
        lines.append(f"- Exists: `{item.get('exists')}`\n")
        lines.append(f"- Patched/valid: `{item.get('patched', item.get('valid'))}`\n")
        lines.append(f"- Already patched: `{item.get('already_patched')}`\n")
        lines.append(f"- Backup: `{item.get('backup')}`\n")
        lines.append(f"- Flags: `{item.get('flags', item.get('after_flags'))}`\n")
        if item.get("errors"):
            lines.append("- Errors:\n")
            for e in item["errors"]:
                lines.append(f"  - `{e}`\n")
        lines.append("\n")

    lines.append("## ABBA test\n\n")
    lines.append("After patching, close Fiji/ABBA completely, restart, reload the atlas. The Atlas Display list should show Ch0/Ch1/Ch2 but not `borders`.\n")
    (report_dir / "V44_HIDE_ABBA_NATIVE_BORDERS_CHANNEL_REPORT.md").write_text(
        "".join(lines),
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Patch abba_python/abba_map.py to hide the native ABBA borders display channel.")
    ap.add_argument("--root", default=None, help="Project root. Default: current directory.")
    ap.add_argument("--abba-map-py", default=None, help="Explicit path to abba_python/abba_map.py.")
    ap.add_argument("--deep-search", action="store_true", help="Search user profile recursively if normal discovery fails.")
    ap.add_argument("--apply", action="store_true", help="Write patch. Without this, dry-run only.")
    ap.add_argument("--validate-only", action="store_true", help="Validate existing patch instead of applying.")
    ap.add_argument("--patch-all", action="store_true", help="Patch all discovered abba_map.py files instead of only the first.")
    ap.add_argument("--fail-if-none", action="store_true", help="Return nonzero if no abba_map.py file is found.")
    args = ap.parse_args()

    project_root = project_root_from_arg(args.root)
    dry_run = not args.apply

    files = discover_abba_map_files(
        project_root=project_root,
        explicit=args.abba_map_py,
        deep_search=args.deep_search,
    )

    if not args.patch_all and files:
        files = [files[0]]

    results: List[Dict[str, Any]] = []

    if args.validate_only:
        results = [validate_one(p) for p in files]
        passed = bool(results) and all(r.get("valid") for r in results)
        mode = "validate-only"
    else:
        results = [patch_one(p, project_root=project_root, dry_run=dry_run) for p in files]
        passed = bool(results) and all(r.get("patched") and not r.get("errors") for r in results)
        mode = "apply" if args.apply else "dry-run"

    if not files and args.fail_if_none:
        passed = False

    report = {
        "version": "V44 hide ABBA native borders display channel",
        "generated_at": now(),
        "project_root": str(project_root),
        "mode": mode,
        "discovered_files": [str(p) for p in files],
        "results": results,
        "passed": passed,
        "notes": [
            "This patches abba_python/abba_map.py, not atlas data.",
            "annotation.tiff and annotation.nii.gz are untouched.",
            "getLabelImage() remains available for mapping/export.",
        ],
    }
    write_report(project_root, report)

    print("V44 Hide ABBA Native Borders Channel")
    print("=" * 72)
    print(f"Root: {project_root}")
    print(f"Mode: {mode}")
    print(f"Discovered files: {len(files)}")
    for p in files:
        print(f"- {p}")
    print(f"PASSED: {passed}")
    print()
    print("Report:")
    print(project_root / "reports" / REPORT_DIR_NAME / "V44_HIDE_ABBA_NATIVE_BORDERS_CHANNEL_REPORT.md")

    if not files:
        print()
        print("No abba_python/abba_map.py found.")
        print("Rerun with --deep-search or pass --abba-map-py <full path>.")
        return 2 if args.fail_if_none else 0

    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
