#!/usr/bin/env python3
"""
V33.4 Apply Approved Label Acronym Curation

Applies only approved rows from the curated Paxinos/Watson acronym resource to
structures.json metadata. It never changes annotation volumes, voxel IDs,
parent_structure_id, or structure_id_path.

Default mode is dry-run. Use --apply to write changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
REPORT_REL = Path("reports") / "v33_4_apply_approved_label_acronym_curation"


def norm(s: Any) -> str:
    return str(s or "").strip()


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def candidate_atlas_dirs(root: Path, explicit: Optional[str]) -> List[Path]:
    if explicit:
        return [Path(explicit).expanduser().resolve()]
    home = Path.home()
    return [
        root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_official_candidate" / f"{ATLAS_NAME}_v1.0",
        root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_provisional" / f"{ATLAS_NAME}_v1.0",
        root / "data" / "output" / "brainglobe_local_cache" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_local_cache" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / ATLAS_NAME,
    ]


def select_atlas_dirs(root: Path, explicit: Optional[str], all_dirs: bool) -> Tuple[List[Path], List[Dict[str, Any]]]:
    checked = []
    usable = []
    for d in candidate_atlas_dirs(root, explicit):
        st = d / "structures.json"
        ok = d.exists() and st.exists()
        checked.append({"atlas_dir": str(d), "exists": d.exists(), "structures_json": st.exists(), "usable": ok})
        if ok:
            usable.append(d)
    if explicit:
        return usable, checked
    if all_dirs:
        return usable, checked
    return usable[:1], checked


def load_structures(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"structures.json is not a list: {path}")
    return data


def build_approved_map(rows: List[Dict[str, str]]) -> Dict[int, Dict[str, str]]:
    approved: Dict[int, Dict[str, str]] = {}
    for row in rows:
        if norm(row.get("review_status")).lower() != "approved":
            continue
        if norm(row.get("proposed_acronym")).upper().startswith("UNL"):
            # Do not apply placeholder labels even if someone accidentally approves them.
            continue
        try:
            sid = int(norm(row.get("label_id")))
        except Exception:
            continue
        approved[sid] = row
    return approved


def apply_to_structures(structures: List[Dict[str, Any]], approved: Dict[int, Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    planned: List[Dict[str, Any]] = []
    sid_to_index = {int(s.get("id")): i for i, s in enumerate(structures) if "id" in s}
    output = json.loads(json.dumps(structures))  # deep copy via json-compatible content

    for sid, row in sorted(approved.items()):
        if sid not in sid_to_index:
            warnings.append(f"approved label_id missing from structures.json: {sid}")
            continue
        idx = sid_to_index[sid]
        st = output[idx]
        old_acr = norm(st.get("acronym"))
        old_name = norm(st.get("name"))
        new_acr = norm(row.get("proposed_acronym"))
        new_name = norm(row.get("proposed_name")) or norm(row.get("paxinos_name"))
        if not new_acr or not new_name:
            warnings.append(f"approved row has empty acronym/name and was skipped: {sid}")
            continue
        if old_acr == new_acr and old_name == new_name:
            continue
        planned.append({
            "label_id": sid,
            "old_acronym": old_acr,
            "old_name": old_name,
            "new_acronym": new_acr,
            "new_name": new_name,
            "acronym_basis": row.get("acronym_basis", ""),
            "basis_detail": row.get("basis_detail", ""),
            "confidence": row.get("confidence", ""),
        })
        st["acronym"] = new_acr
        st["name"] = new_name
        # Preserve all other metadata. Add non-invasive provenance fields if already absent.
        st.setdefault("label_curation", {})
        if isinstance(st["label_curation"], dict):
            st["label_curation"].update({
                "v33_label_acronym_curation": True,
                "acronym_basis": row.get("acronym_basis", ""),
                "basis_detail": row.get("basis_detail", ""),
                "confidence": row.get("confidence", ""),
            })
    # Safety: no duplicate acronyms after proposed apply.
    acrs = [norm(s.get("acronym")) for s in output if norm(s.get("acronym"))]
    dup = sorted([a for a, c in Counter(acrs).items() if c > 1])
    if dup:
        warnings.append(f"duplicate acronyms after planned apply: {dup[:50]}")
    return output, planned, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Project root. Default: current directory.")
    ap.add_argument("--atlas-dir", default=None, help="Optional explicit atlas dir containing structures.json.")
    ap.add_argument("--resource", default=None, help="Optional curated acronym CSV path.")
    ap.add_argument("--all-usable-atlas-dirs", action="store_true", help="Apply to all detected atlas dirs instead of first usable one.")
    ap.add_argument("--apply", action="store_true", help="Actually write structures.json. Without this, dry-run only.")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    resource = Path(args.resource).expanduser().resolve() if args.resource else root / RESOURCE_REL
    report_dir = root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat(timespec="seconds")

    errors: List[str] = []
    warnings: List[str] = []
    if not resource.exists():
        errors.append(f"Missing resource CSV: {resource}")
    atlas_dirs, checked = select_atlas_dirs(root, args.atlas_dir, args.all_usable_atlas_dirs)
    if not atlas_dirs:
        errors.append("No usable atlas dir found. Run builder first or pass --atlas-dir.")

    report: Dict[str, Any] = {
        "version": "V33.4 Apply Approved Label Acronym Curation",
        "generated_at": generated_at,
        "project_root": str(root),
        "resource_csv": str(resource),
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "does_modify_annotation_volumes": False,
        "does_modify_structure_ids": False,
        "does_modify_parent_relationships": False,
        "candidate_atlas_dirs": checked,
        "selected_atlas_dirs": [str(d) for d in atlas_dirs],
        "errors": errors,
        "warnings": warnings,
        "results": [],
    }

    if errors:
        report["passed"] = False
        (report_dir / "v33_4_apply_approved_label_acronym_curation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print("FAILED before processing. See report.")
        return 2

    rows = read_csv_dicts(resource)
    approved = build_approved_map(rows)
    all_planned: List[Dict[str, Any]] = []
    any_failed = False

    for atlas_dir in atlas_dirs:
        structures_path = atlas_dir / "structures.json"
        structures = load_structures(structures_path)
        output, planned, local_warnings = apply_to_structures(structures, approved)
        warnings.extend([f"{atlas_dir}: {w}" for w in local_warnings])
        all_planned.extend([{**p, "atlas_dir": str(atlas_dir)} for p in planned])
        blocked = [w for w in local_warnings if w.startswith("duplicate acronyms after")]
        result = {
            "atlas_dir": str(atlas_dir),
            "structures_json": str(structures_path),
            "approved_rows": len(approved),
            "planned_changes": len(planned),
            "warnings": local_warnings,
            "written": False,
            "backup": None,
        }
        if blocked:
            any_failed = True
            result["blocked"] = True
        elif args.apply and planned:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = structures_path.with_name(f"structures.json.v33_4_before_label_acronyms_{stamp}.bak")
            shutil.copy2(structures_path, backup)
            structures_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
            result["written"] = True
            result["backup"] = str(backup)
        report["results"].append(result)

    write_csv(report_dir / "v33_4_planned_or_applied_changes.csv", all_planned, [
        "atlas_dir", "label_id", "old_acronym", "old_name", "new_acronym", "new_name", "acronym_basis", "basis_detail", "confidence"
    ])
    report["warnings"] = warnings
    report["approved_resource_rows"] = len(approved)
    report["total_planned_changes"] = len(all_planned)
    report["passed"] = not any_failed

    summary = [
        "V33.4 Apply Approved Label Acronym Curation",
        "="*72,
        f"Generated: {generated_at}",
        f"Project root: {root}",
        f"Mode: {'APPLY' if args.apply else 'DRY_RUN'}",
        f"Resource CSV: {resource}",
        f"Selected atlas dirs: {len(atlas_dirs)}",
        f"Approved resource rows: {len(approved)}",
        f"Total planned changes: {len(all_planned)}",
        f"PASSED: {report['passed']}",
        "",
        "Safety:",
        "- annotation volumes are not modified",
        "- label IDs are not modified",
        "- parent_structure_id is not modified",
        "- structure_id_path is not modified",
        "",
        "Warnings:",
    ]
    summary.extend([f"- {w}" for w in warnings[:50]] or ["- none"])
    (report_dir / "v33_4_apply_approved_label_acronym_curation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (report_dir / "v33_4_apply_approved_label_acronym_curation_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("PASSED" if report["passed"] else "FAILED")
    print(f"Reports: {report_dir}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
