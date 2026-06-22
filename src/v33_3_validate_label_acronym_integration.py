#!/usr/bin/env python3
"""
V33.3 Label Acronym Integration Validator

Validates the curated Paxinos/Watson acronym resource before it is used by the
builder. This script is intentionally conservative: it never modifies atlas
files. It only writes reports.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
ACRONYM_TXT_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms.txt"
REPORT_REL = Path("reports") / "v33_3_label_acronym_integration_validator"
ALLOWED_STATUS = {"pending_review", "approved", "rejected", "needs_manual_review", "defer", "do_not_apply"}
REQUIRED_COLS = {
    "label_id", "paxinos_name", "proposed_acronym", "proposed_name",
    "acronym_basis", "basis_detail", "confidence", "review_status"
}
HIGH_TRUST_BASIS = {"official_paxinos_cortex", "manual_common_neuroanatomy"}
PLACEHOLDER_NAME_RE = re.compile(r"^[-–—_\s]+$")


def norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def norm_acr(s: Any) -> str:
    return str(s or "").strip()


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_dicts(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def load_structures(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"structures.json is not a list: {path}")
    return data


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


def find_usable_atlas(root: Path, explicit: Optional[str]) -> Tuple[Optional[Path], List[Dict[str, Any]]]:
    checked = []
    selected = None
    for d in candidate_atlas_dirs(root, explicit):
        structures = d / "structures.json"
        annotation_nii = d / "annotation.nii.gz"
        annotation_tiff = d / "annotation.tiff"
        metadata = d / "metadata.json"
        usable = d.exists() and structures.exists()
        checked.append({
            "atlas_dir": str(d),
            "exists": d.exists(),
            "structures_json": structures.exists(),
            "annotation_nii": annotation_nii.exists(),
            "annotation_tiff": annotation_tiff.exists(),
            "metadata_json": metadata.exists(),
            "usable": usable,
        })
        if selected is None and usable:
            selected = d
    return selected, checked


def validate_rows(rows: List[Dict[str, str]], structures_by_id: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    problems = []
    warnings = []
    rows_by_id = {}
    duplicate_resource_ids = []

    for i, row in enumerate(rows, start=2):
        try:
            label_id = int(str(row.get("label_id", "")).strip())
        except Exception:
            problems.append({"line": i, "type": "bad_label_id", "detail": row.get("label_id", "")})
            continue
        if label_id in rows_by_id:
            duplicate_resource_ids.append(label_id)
        rows_by_id[label_id] = row
        status = norm_text(row.get("review_status"))
        if status not in ALLOWED_STATUS:
            problems.append({"line": i, "label_id": label_id, "type": "bad_review_status", "detail": status})
        acr = norm_acr(row.get("proposed_acronym"))
        name = str(row.get("proposed_name") or "").strip()
        if not acr:
            problems.append({"line": i, "label_id": label_id, "type": "empty_proposed_acronym"})
        if not name:
            problems.append({"line": i, "label_id": label_id, "type": "empty_proposed_name"})
        if status == "approved":
            basis = norm_text(row.get("acronym_basis"))
            conf = norm_text(row.get("confidence"))
            detail = str(row.get("basis_detail") or "").strip()
            if conf in {"low", ""}:
                problems.append({"line": i, "label_id": label_id, "type": "approved_low_confidence"})
            if basis not in {norm_text(x) for x in HIGH_TRUST_BASIS} and not detail:
                problems.append({"line": i, "label_id": label_id, "type": "approved_without_basis_detail"})
            if acr.upper().startswith("UNL") or PLACEHOLDER_NAME_RE.match(name):
                problems.append({"line": i, "label_id": label_id, "type": "approved_placeholder_like_label"})

    resource_ids = set(rows_by_id)
    structure_ids = set(structures_by_id)
    resource_ids_missing_in_structures = sorted(resource_ids - structure_ids)
    structure_ids_missing_in_resource = sorted(i for i in structure_ids - resource_ids if i != 997)  # root may be absent from raw Paxinos labels

    # Duplicate acronyms in resource and after hypothetical approved apply.
    acr_counter = Counter(norm_acr(r.get("proposed_acronym")) for r in rows if norm_acr(r.get("proposed_acronym")))
    duplicate_resource_acronyms = sorted([a for a, c in acr_counter.items() if c > 1])

    approved_rows = [r for r in rows if norm_text(r.get("review_status")) == "approved"]
    final_acronyms = {}
    for sid, st in structures_by_id.items():
        final_acronyms[sid] = norm_acr(st.get("acronym"))
    for r in approved_rows:
        try:
            sid = int(str(r.get("label_id", "")).strip())
        except Exception:
            continue
        if sid in final_acronyms:
            final_acronyms[sid] = norm_acr(r.get("proposed_acronym"))
    final_counter = Counter(a for a in final_acronyms.values() if a)
    duplicate_final_acronyms = sorted([a for a, c in final_counter.items() if c > 1])

    # Name mismatch check only as warning because current structures may already be generated/curated.
    name_mismatches = []
    for sid, r in rows_by_id.items():
        st = structures_by_id.get(sid)
        if not st:
            continue
        current_name = norm_text(st.get("name"))
        paxinos_name = norm_text(r.get("paxinos_name"))
        proposed_name = norm_text(r.get("proposed_name"))
        if current_name and paxinos_name and current_name != paxinos_name and current_name != proposed_name:
            name_mismatches.append({
                "label_id": sid,
                "current_acronym": st.get("acronym", ""),
                "current_name": st.get("name", ""),
                "paxinos_name": r.get("paxinos_name", ""),
                "proposed_acronym": r.get("proposed_acronym", ""),
                "proposed_name": r.get("proposed_name", ""),
                "review_status": r.get("review_status", ""),
                "confidence": r.get("confidence", ""),
                "acronym_basis": r.get("acronym_basis", ""),
            })
    if duplicate_resource_ids:
        problems.append({"type": "duplicate_resource_ids", "count": len(duplicate_resource_ids), "ids": duplicate_resource_ids[:100]})
    if resource_ids_missing_in_structures:
        problems.append({"type": "resource_ids_missing_in_structures", "count": len(resource_ids_missing_in_structures), "ids": resource_ids_missing_in_structures[:100]})
    if duplicate_resource_acronyms:
        problems.append({"type": "duplicate_resource_acronyms", "count": len(duplicate_resource_acronyms), "acronyms": duplicate_resource_acronyms[:100]})
    if duplicate_final_acronyms:
        warnings.append({"type": "duplicate_final_acronyms_if_approved_applied", "count": len(duplicate_final_acronyms), "acronyms": duplicate_final_acronyms[:100]})

    by_status = Counter(norm_text(r.get("review_status")) for r in rows)
    by_conf = Counter(norm_text(r.get("confidence")) for r in rows)
    by_basis = Counter(str(r.get("acronym_basis") or "").strip() for r in rows)

    return {
        "problems": problems,
        "warnings": warnings,
        "row_count": len(rows),
        "approved_count": len(approved_rows),
        "pending_count": by_status.get("pending_review", 0),
        "rejected_count": by_status.get("rejected", 0) + by_status.get("do_not_apply", 0),
        "resource_ids_missing_in_structures_count": len(resource_ids_missing_in_structures),
        "structure_ids_missing_in_resource_count": len(structure_ids_missing_in_resource),
        "structure_ids_missing_in_resource_preview": structure_ids_missing_in_resource[:100],
        "duplicate_resource_acronyms_count": len(duplicate_resource_acronyms),
        "duplicate_final_acronyms_count": len(duplicate_final_acronyms),
        "name_mismatches_count": len(name_mismatches),
        "by_status": dict(by_status),
        "by_confidence": dict(by_conf),
        "by_basis": dict(by_basis),
        "name_mismatches": name_mismatches,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Project root. Default: current directory.")
    ap.add_argument("--atlas-dir", default=None, help="Optional explicit atlas directory containing structures.json.")
    ap.add_argument("--resource", default=None, help="Optional curated acronym CSV path.")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    report_dir = root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat(timespec="seconds")

    resource = Path(args.resource).expanduser().resolve() if args.resource else root / RESOURCE_REL
    acronym_txt = root / ACRONYM_TXT_REL
    errors = []
    warnings = []

    selected_atlas, checked = find_usable_atlas(root, args.atlas_dir)
    if not resource.exists():
        errors.append(f"Missing resource CSV: {resource}")
    if not acronym_txt.exists():
        warnings.append(f"Missing optional txt label file: {acronym_txt}")
    if selected_atlas is None:
        errors.append("No usable atlas directory found. Run the builder once, or pass --atlas-dir.")

    report: Dict[str, Any] = {
        "version": "V33.3 Label Acronym Integration Validator",
        "generated_at": generated_at,
        "project_root": str(root),
        "does_modify_atlas": False,
        "resource_csv": str(resource),
        "acronym_txt": str(acronym_txt),
        "candidate_atlas_dirs": checked,
        "selected_atlas_dir": str(selected_atlas) if selected_atlas else None,
        "errors": errors,
        "warnings": warnings,
    }

    if errors:
        report["passed"] = False
        (report_dir / "v33_3_label_acronym_integration_validator_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (report_dir / "v33_3_label_acronym_integration_validator_summary.txt").write_text("V33.3 Label Acronym Integration Validator\n" + "="*72 + "\nPASSED: False\n\n" + "\n".join(errors) + "\n", encoding="utf-8")
        print("FAILED. See reports.")
        return 2

    rows = read_csv_dicts(resource)
    missing_cols = sorted(REQUIRED_COLS - set(rows[0].keys() if rows else []))
    if missing_cols:
        report["passed"] = False
        report["errors"].append(f"Resource CSV missing columns: {missing_cols}")
    else:
        structures_path = selected_atlas / "structures.json"  # type: ignore[operator]
        structures = load_structures(structures_path)
        structures_by_id = {int(s["id"]): s for s in structures if "id" in s}
        validation = validate_rows(rows, structures_by_id)
        report.update({
            "structures_json": str(structures_path),
            "structure_count": len(structures),
            "validation": {k: v for k, v in validation.items() if k != "name_mismatches"},
        })
        write_csv_dicts(
            report_dir / "v33_3_name_mismatches_current_vs_resource.csv",
            validation["name_mismatches"],
            ["label_id", "current_acronym", "current_name", "paxinos_name", "proposed_acronym", "proposed_name", "review_status", "confidence", "acronym_basis"],
        )
        # Planned approved changes preview
        planned = []
        for r in rows:
            if norm_text(r.get("review_status")) != "approved":
                continue
            try:
                sid = int(str(r.get("label_id", "")).strip())
            except Exception:
                continue
            st = structures_by_id.get(sid)
            if not st:
                continue
            new_acr = norm_acr(r.get("proposed_acronym"))
            new_name = str(r.get("proposed_name") or "").strip()
            if new_acr != norm_acr(st.get("acronym")) or new_name != str(st.get("name") or "").strip():
                planned.append({
                    "label_id": sid,
                    "old_acronym": st.get("acronym", ""),
                    "old_name": st.get("name", ""),
                    "new_acronym": new_acr,
                    "new_name": new_name,
                    "acronym_basis": r.get("acronym_basis", ""),
                    "basis_detail": r.get("basis_detail", ""),
                    "confidence": r.get("confidence", ""),
                })
        write_csv_dicts(
            report_dir / "v33_3_approved_change_preview.csv",
            planned,
            ["label_id", "old_acronym", "old_name", "new_acronym", "new_name", "acronym_basis", "basis_detail", "confidence"],
        )
        report["approved_change_preview_count"] = len(planned)
        report["passed"] = len(validation["problems"]) == 0

    summary_lines = [
        "V33.3 Label Acronym Integration Validator",
        "="*72,
        f"Generated: {generated_at}",
        f"Project root: {root}",
        f"Selected atlas dir: {selected_atlas}",
        f"Resource CSV: {resource}",
        f"PASSED: {report.get('passed')}",
        "",
    ]
    if "validation" in report:
        v = report["validation"]
        summary_lines += [
            "Resource / validation counts:",
            f"- row_count: {v.get('row_count')}",
            f"- approved_count: {v.get('approved_count')}",
            f"- pending_count: {v.get('pending_count')}",
            f"- rejected_count: {v.get('rejected_count')}",
            f"- resource_ids_missing_in_structures: {v.get('resource_ids_missing_in_structures_count')}",
            f"- structure_ids_missing_in_resource: {v.get('structure_ids_missing_in_resource_count')}",
            f"- duplicate_resource_acronyms: {v.get('duplicate_resource_acronyms_count')}",
            f"- duplicate_final_acronyms_if_approved_applied: {v.get('duplicate_final_acronyms_count')}",
            f"- name_mismatches_current_vs_resource: {v.get('name_mismatches_count')}",
            f"- approved_change_preview_count: {report.get('approved_change_preview_count')}",
            "",
            "Status breakdown:",
        ]
        for k, val in sorted(v.get("by_status", {}).items()):
            summary_lines.append(f"- {k}: {val}")
        summary_lines += ["", "Problems:"]
        probs = v.get("problems", [])
        if probs:
            for p in probs[:30]:
                summary_lines.append(f"- {p}")
        else:
            summary_lines.append("- none")
        summary_lines += ["", "Warnings:"]
        warns = report.get("warnings", []) + v.get("warnings", [])
        if warns:
            for w in warns[:30]:
                summary_lines.append(f"- {w}")
        else:
            summary_lines.append("- none")
    else:
        summary_lines += ["Errors:"] + [f"- {e}" for e in report.get("errors", [])]

    (report_dir / "v33_3_label_acronym_integration_validator_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (report_dir / "v33_3_label_acronym_integration_validator_summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("PASSED" if report.get("passed") else "FAILED")
    print(f"Reports: {report_dir}")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
