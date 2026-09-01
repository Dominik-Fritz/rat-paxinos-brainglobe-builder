#!/usr/bin/env python3
"""Release data manager for the Paxinos/Watson rat BrainGlobe builder.

Default mode is intentionally minimal and label-atlas only.
It downloads/verifies only the Paxinos/Watson source atlas and label tables
needed by the stable LabelAtlas pipeline. It does not run MRI/reference-channel
experiments and does not download Waxholm/SIGMA/NeuroRat optional channels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.request

ZENODO_RECORD = "10926947"
ZENODO_BASE = f"https://zenodo.org/records/{ZENODO_RECORD}/files"

# Minimal stable LabelAtlas sources. MD5 values are from the Zenodo record page.
FILES = {
    "paxinos_atlas": {
        "filename": "Paxinos_Watson_Atlas.nii.gz",
        "required": True,
        "md5": "6971da9c57ba4178f185d99a2c8ddac8",
        "size_mb_expected_min": 1.0,
        "size_mb_expected_max": 10.0,
    },
    "paxinos_labels": {
        "filename": "Paxinos_Watson_Labels.txt",
        "required": True,
        "md5": "2fb62017ca58c78f74c985506717f32a",
        "size_mb_expected_min": 0.001,
        "size_mb_expected_max": 1.0,
    },
    "paxinos_labels_cortex": {
        "filename": "Paxinos_Watson_Labels_Cortex.txt",
        "required": False,
        "recommended": True,
        "md5": "5e12331e392257cf302adca271be83a8",
        "size_mb_expected_min": 0.0005,
        "size_mb_expected_max": 1.0,
    },
}

REPORT_SUBDIR = Path("reports") / "release_data_manager"


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def file_url(filename: str) -> str:
    # Zenodo supports this stable records/<id>/files/<filename>?download=1 endpoint.
    return f"{ZENODO_BASE}/{urllib.parse.quote(filename)}?download=1"


def safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def download_file(url: str, dest: Path, timeout: int = 60) -> tuple[bool, str | None]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    safe_unlink(tmp)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "rat-paxinos-builder-data-manager/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(dest)
        return True, None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        safe_unlink(tmp)
        return False, str(e)


def inspect_file(raw_dir: Path, key: str, spec: dict) -> dict:
    path = raw_dir / spec["filename"]
    row = {
        "key": key,
        "filename": spec["filename"],
        "path": str(path),
        "required": bool(spec.get("required", False)),
        "recommended": bool(spec.get("recommended", False)),
        "exists": path.exists(),
        "size_mb": None,
        "md5_expected": spec.get("md5"),
        "md5_actual": None,
        "md5_ok": None,
        "size_plausible": None,
        "status": "missing",
        "url": file_url(spec["filename"]),
    }
    if path.exists():
        size_mb = path.stat().st_size / (1024 * 1024)
        row["size_mb"] = round(size_mb, 6)
        row["size_plausible"] = spec.get("size_mb_expected_min", 0) <= size_mb <= spec.get("size_mb_expected_max", 10**9)
        try:
            actual = md5_file(path)
            row["md5_actual"] = actual
            row["md5_ok"] = (not spec.get("md5")) or actual.lower() == spec["md5"].lower()
        except Exception as e:
            row["status"] = f"md5_error: {e}"
            return row
        if row["md5_ok"] and row["size_plausible"]:
            row["status"] = "ok"
        elif not row["md5_ok"]:
            row["status"] = "md5_mismatch"
        elif not row["size_plausible"]:
            row["status"] = "size_implausible"
    return row


def write_reports(root: Path, report: dict) -> None:
    report_dir = root / REPORT_SUBDIR
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "release_data_manager_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = []
    lines.append("Release Data Manager")
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Root: {report['root']}")
    lines.append(f"Raw dir: {report['raw_dir']}")
    lines.append(f"Mode: {report['mode']}")
    lines.append(f"PASSED: {report['passed']}")
    lines.append("")
    lines.append("Files:")
    for row in report["files"]:
        lines.append(f"- {row['key']}: {row['status']} :: {row['filename']} :: exists={row['exists']} size_mb={row['size_mb']} md5_ok={row['md5_ok']}")
    lines.append("")
    if report.get("downloads"):
        lines.append("Downloads:")
        for d in report["downloads"]:
            lines.append(f"- {d['filename']}: {d['action']} success={d.get('success')} error={d.get('error')}")
        lines.append("")
    if report.get("warnings"):
        lines.append("Warnings:")
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    if report.get("errors"):
        lines.append("Errors:")
        for e in report["errors"]:
            lines.append(f"- {e}")
    else:
        lines.append("Errors: none")
    lines.append("")
    lines.append("Manual fallback:")
    lines.append(f"Put the required files into: {report['raw_dir']}")
    lines.append("Required: Paxinos_Watson_Atlas.nii.gz and Paxinos_Watson_Labels.txt")
    lines.append("Recommended optional: Paxinos_Watson_Labels_Cortex.txt")
    (report_dir / "release_data_manager_summary.txt").write_text("\n".join(lines), encoding="utf-8")


def run(root: Path, mode: str, include_optional: bool) -> int:
    raw_dir = root / "data" / "raw" / "bluebrainheadmodels"
    raw_dir.mkdir(parents=True, exist_ok=True)
    downloads = []
    errors = []
    warnings = []

    before = [inspect_file(raw_dir, k, v) for k, v in FILES.items()]

    wants_download = mode in {"download-minimal", "ensure-minimal", "repair"}
    if wants_download:
        for key, spec in FILES.items():
            if not spec.get("required") and not (include_optional or spec.get("recommended")):
                continue
            row = inspect_file(raw_dir, key, spec)
            needs = (not row["exists"]) or row["status"] in {"md5_mismatch", "size_implausible", "md5_error"} or mode == "repair"
            if not needs:
                downloads.append({"filename": spec["filename"], "action": "already_ok", "success": True, "error": None})
                continue
            url = file_url(spec["filename"])
            ok, err = download_file(url, raw_dir / spec["filename"])
            downloads.append({"filename": spec["filename"], "action": "downloaded" if ok else "download_failed", "success": ok, "error": err, "url": url})
            if not ok:
                message = f"Could not download {spec['filename']}: {err}"
                if spec.get("required"):
                    errors.append(message)
                else:
                    warnings.append(message + " (optional; build continues)")

    after = [inspect_file(raw_dir, k, v) for k, v in FILES.items()]
    required_ok = all((not r["required"]) or r["status"] == "ok" for r in after)
    optional_bad = [r for r in after if (not r["required"]) and r["exists"] and r["status"] != "ok"]
    if optional_bad:
        for r in optional_bad:
            warnings.append(
                f"Optional file failed validation and will not be trusted: "
                f"{r['filename']} status={r['status']}"
            )

    report = {
        "version": "V32.26 release data manager",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "root": str(root),
        "raw_dir": str(raw_dir),
        "zenodo_record": ZENODO_RECORD,
        "minimal_labelatlas_only": True,
        "downloads": downloads,
        "files_before": before,
        "files": after,
        "errors": errors,
        "warnings": warnings,
        "passed": bool(required_ok and not errors),
    }
    write_reports(root, report)

    print("Release Data Manager")
    print("=" * 72)
    print(f"Mode: {mode}")
    print(f"Root: {root}")
    print(f"Raw dir: {raw_dir}")
    print(f"PASSED: {report['passed']}")
    print("")
    for r in after:
        print(f"- {r['key']}: {r['status']} :: {r['filename']}")
    if warnings:
        print("\nWarnings:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"- {e}")
    print(f"\nReports: {root / REPORT_SUBDIR}")
    return 0 if report["passed"] else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="Release candidate/project root. Defaults to current working directory.")
    ap.add_argument("--mode", default="verify", choices=["verify", "download-minimal", "ensure-minimal", "repair"])
    ap.add_argument("--include-optional", action="store_true", help="Download recommended optional Paxinos cortex label table too.")
    ns = ap.parse_args(argv)
    root = Path(ns.root or os.getcwd()).resolve()
    return run(root, ns.mode, ns.include_optional)


if __name__ == "__main__":
    raise SystemExit(main())
