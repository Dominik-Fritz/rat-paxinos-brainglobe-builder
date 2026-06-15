#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Release data preflight for the Rat Paxinos/Watson BrainGlobe Builder.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
REQUIRED_GROUPS = {
    "paxinos_atlas": ["Paxinos_Watson_Atlas.nii.gz", "Paxinos_Watson_Atlas.nii"],
    "paxinos_labels": ["Paxinos_Watson_Labels.txt"],
}
OPTIONAL_GROUPS = {
    "paxinos_labels_cortex": ["Paxinos_Watson_Labels_Cortex.txt"],
}


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def raw_dir(root: Path) -> Path:
    return root / "data" / "raw" / "bluebrainheadmodels"


def report_dir(root: Path) -> Path:
    return root / "reports"


def find_first(base: Path, names: List[str]) -> Path | None:
    for name in names:
        p = base / name
        if p.exists() and p.is_file():
            return p
    return None


def file_info(p: Path | None) -> Dict[str, Any]:
    if p is None:
        return {"exists": False, "path": None, "size_mb": None}
    try:
        size_mb = round(p.stat().st_size / 1024 / 1024, 4)
    except OSError:
        size_mb = None
    return {"exists": True, "path": str(p), "size_mb": size_mb}


def check_groups(base: Path, groups: Dict[str, List[str]]) -> Tuple[Dict[str, Any], List[str]]:
    out: Dict[str, Any] = {}
    missing: List[str] = []
    for key, names in groups.items():
        found = find_first(base, names)
        out[key] = {"accepted_names": names, "found": file_info(found)}
        if found is None:
            missing.append(key)
    return out, missing


def main() -> int:
    root = project_root()
    rd = raw_dir(root)
    rep = report_dir(root)
    rep.mkdir(parents=True, exist_ok=True)
    rd.mkdir(parents=True, exist_ok=True)

    required, missing_required = check_groups(rd, REQUIRED_GROUPS)
    optional, missing_optional = check_groups(rd, OPTIONAL_GROUPS)
    passed = not missing_required

    report = {
        "version": "V32.25 release data preflight",
        "generated_at": now(),
        "project_root": str(root),
        "raw_dir": str(rd),
        "atlas_name": ATLAS_NAME,
        "mode": "minimal_labelatlas_only",
        "required": required,
        "optional": optional,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "passed": passed,
        "next_step_if_missing": [
            "Put required source files into data/raw/bluebrainheadmodels/.",
            "Minimal required: Paxinos_Watson_Atlas.nii.gz or .nii, and Paxinos_Watson_Labels.txt.",
            "Optional but useful: Paxinos_Watson_Labels_Cortex.txt.",
            "For local testing, copy these files from the development project if available.",
            "Automated download/data-manager support is planned as the next release block.",
        ],
    }

    (rep / "release_data_preflight_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V32.25 Release Data Preflight",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Project root: {root}",
        f"Raw dir: {rd}",
        f"Mode: {report['mode']}",
        f"PASSED: {passed}",
        "",
        "Required source files:",
    ]
    for key, info in required.items():
        found = info["found"]
        lines.append(f"- {key}: {'FOUND' if found['exists'] else 'MISSING'}")
        lines.append(f"  accepted names: {', '.join(info['accepted_names'])}")
        if found["exists"]:
            lines.append(f"  path: {found['path']}")
            lines.append(f"  size_mb: {found['size_mb']}")
    lines += ["", "Optional source files:"]
    for key, info in optional.items():
        found = info["found"]
        lines.append(f"- {key}: {'FOUND' if found['exists'] else 'missing optional'}")
        lines.append(f"  accepted names: {', '.join(info['accepted_names'])}")
        if found["exists"]:
            lines.append(f"  path: {found['path']}")
            lines.append(f"  size_mb: {found['size_mb']}")

    if not passed:
        lines += [
            "",
            "STOP:",
            "- Required Paxinos source files are missing.",
            "- The builder cannot continue until these files exist locally.",
            "- No MRI/reference-channel experiment was run.",
            "",
            "Put files here:",
            f"  {rd}",
        ]
    else:
        lines += ["", "OK: Minimal source data are present. Builder can continue."]

    (rep / "release_data_preflight_summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
