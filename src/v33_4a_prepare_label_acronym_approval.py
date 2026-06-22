#!/usr/bin/env python3
"""
V33.4A Label Acronym Approval Preparer

Purpose:
- Reads resources/label_curation/Paxinos_Watson_Labels_Acronyms_with_basis.csv
- Creates an approval plan for safe label acronym metadata rows
- Optionally changes review_status to approved in the resource CSV
- Does NOT modify annotation volumes
- Does NOT modify structures.json

Scopes:
- official_local: approve only Paxinos cortex exact-ID rows
- high_confidence: approve official_local + high confidence manual_common_neuroanatomy rows
- report_only: approve nothing, only write report tables
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

VALID_SCOPES = {"report_only", "official_local", "high_confidence"}

REQUIRED_COLUMNS = [
    "label_id",
    "paxinos_name",
    "proposed_acronym",
    "proposed_name",
    "acronym_basis",
    "basis_detail",
    "confidence",
    "review_status",
    "current_structure_acronym",
    "current_structure_name",
]

DO_NOT_APPROVE_STATUSES = {"do_not_apply", "rejected", "defer", "needs_manual_review"}


def read_csv_dicts(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames or []
    return rows, fields


def write_csv_dicts(path: Path, rows: List[Dict[str, str]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_acronym_txt(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def sid(row):
        try:
            return int(row.get("label_id", "0"))
        except Exception:
            return 0
    with path.open("w", encoding="utf-8", newline="") as f:
        for row in sorted(rows, key=sid):
            label_id = str(row.get("label_id", "")).strip()
            acronym = str(row.get("proposed_acronym", "")).strip()
            name = str(row.get("proposed_name", "")).strip().replace('"', '""')
            f.write(f'{label_id}\t{acronym}\t"{name}"\n')


def norm(s: str) -> str:
    return " ".join((s or "").strip().lower().replace("assocn", "association").replace("associatin", "association").replace("telencepahlon", "telencephalon").replace("medulla-oblongolata", "medulla oblongata").split())


def is_official_local(row: Dict[str, str]) -> bool:
    return (
        row.get("confidence", "").strip() == "official_local" and
        row.get("acronym_basis", "").strip() == "paxinos_cortex_file_exact_id"
    )


def is_high_common(row: Dict[str, str]) -> bool:
    return (
        row.get("confidence", "").strip() == "high" and
        row.get("acronym_basis", "").strip() == "manual_common_neuroanatomy"
    )


def has_basic_required_values(row: Dict[str, str]) -> bool:
    return bool(row.get("label_id", "").strip() and row.get("proposed_acronym", "").strip() and row.get("proposed_name", "").strip())


def choose_action(row: Dict[str, str], scope: str) -> Tuple[str, str]:
    """Return (action, reason) where action is approve/keep/do_not_apply/problem."""
    status = row.get("review_status", "").strip()
    if status in DO_NOT_APPROVE_STATUSES:
        return "do_not_apply", f"review_status={status}"
    if not has_basic_required_values(row):
        return "problem", "missing label_id/proposed_acronym/proposed_name"
    if row.get("acronym_basis", "").strip() in {"paxinos_placeholder", "builder_generated_internal_structure"}:
        return "keep", "placeholder/internal builder row; manual review required"
    if scope == "report_only":
        return "keep", "report-only scope"
    if scope == "official_local":
        if is_official_local(row):
            return "approve", "official Paxinos cortex exact-ID acronym"
        return "keep", "not official_local"
    if scope == "high_confidence":
        if is_official_local(row):
            return "approve", "official Paxinos cortex exact-ID acronym"
        if is_high_common(row):
            return "approve", "high-confidence common neuroanatomy acronym"
        return "keep", "not official/high-confidence common"
    return "problem", f"unknown scope={scope}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare/optionally apply approved label acronym curation flags.")
    parser.add_argument("--root", required=True, help="Project root, e.g. G:\\rat-paxinos-brainglobe-builder")
    parser.add_argument("--mode", choices=["dry-run", "apply"], default="dry-run")
    parser.add_argument("--scope", choices=sorted(VALID_SCOPES), default="official_local")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    resource_dir = root / "resources" / "label_curation"
    resource_csv = resource_dir / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
    acronym_txt = resource_dir / "Paxinos_Watson_Labels_Acronyms.txt"
    report_dir = root / "reports" / "v33_4a_label_acronym_approval"
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "version": "V33.4A Label Acronym Approval Preparer",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(root),
        "mode": args.mode,
        "scope": args.scope,
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "resource_csv": str(resource_csv),
        "errors": [],
        "warnings": [],
    }

    if not resource_csv.exists():
        report["errors"].append(f"Missing resource CSV: {resource_csv}")
        (report_dir / "v33_4a_label_acronym_approval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2

    rows, fields = read_csv_dicts(resource_csv)
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in fields]
    if missing_cols:
        report["errors"].append(f"Missing required columns: {missing_cols}")
        (report_dir / "v33_4a_label_acronym_approval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2

    planned_rows = []
    approval_candidates = []
    keep_rows = []
    problem_rows = []
    updated_rows = []

    for row in rows:
        row = dict(row)
        action, reason = choose_action(row, args.scope)
        old_status = row.get("review_status", "")
        new_status = old_status
        if action == "approve":
            new_status = "approved"
        elif action == "do_not_apply":
            new_status = "do_not_apply"
        else:
            # Preserve pending_review or other valid states unless explicitly excluded.
            new_status = old_status

        plan_row = {
            "label_id": row.get("label_id", ""),
            "current_structure_acronym": row.get("current_structure_acronym", ""),
            "current_structure_name": row.get("current_structure_name", ""),
            "proposed_acronym": row.get("proposed_acronym", ""),
            "proposed_name": row.get("proposed_name", ""),
            "acronym_basis": row.get("acronym_basis", ""),
            "confidence": row.get("confidence", ""),
            "old_review_status": old_status,
            "new_review_status": new_status,
            "planned_action": action,
            "reason": reason,
            "name_changed": str(norm(row.get("current_structure_name", "")) != norm(row.get("proposed_name", ""))),
            "acronym_changed": str((row.get("current_structure_acronym", "") or "") != (row.get("proposed_acronym", "") or "")),
        }
        planned_rows.append(plan_row)
        if action == "approve":
            approval_candidates.append(plan_row)
        elif action == "problem":
            problem_rows.append(plan_row)
        else:
            keep_rows.append(plan_row)

        row["review_status"] = new_status
        updated_rows.append(row)

    # Check duplicate final acronyms among approved+current kept state after proposed approved rows.
    final_acronyms = []
    for original, updated in zip(rows, updated_rows):
        if updated.get("review_status") == "approved":
            final_acronyms.append(updated.get("proposed_acronym", ""))
        else:
            final_acronyms.append(updated.get("current_structure_acronym", "") or updated.get("proposed_acronym", ""))
    dup_final = sorted([a for a, c in Counter(final_acronyms).items() if a and c > 1])
    if dup_final:
        report["errors"].append(f"Duplicate final acronyms would occur: {dup_final[:50]}")

    # Write outputs.
    plan_fields = [
        "label_id", "current_structure_acronym", "current_structure_name", "proposed_acronym", "proposed_name",
        "acronym_basis", "confidence", "old_review_status", "new_review_status", "planned_action",
        "reason", "name_changed", "acronym_changed"
    ]
    write_csv_dicts(report_dir / "v33_4a_approval_plan.csv", planned_rows, plan_fields)
    write_csv_dicts(report_dir / "v33_4a_approval_candidates.csv", approval_candidates, plan_fields)
    write_csv_dicts(report_dir / "v33_4a_kept_for_review.csv", keep_rows, plan_fields)
    write_csv_dicts(report_dir / "v33_4a_problem_rows.csv", problem_rows, plan_fields)

    # In apply mode, backup and write resource CSV + txt.
    backups = []
    if args.mode == "apply" and not report["errors"]:
        for path in [resource_csv, acronym_txt]:
            if path.exists():
                backup = path.with_suffix(path.suffix + f".before_v33_4a_{timestamp}.bak")
                shutil.copy2(path, backup)
                backups.append(str(backup))
        write_csv_dicts(resource_csv, updated_rows, fields)
        write_acronym_txt(acronym_txt, updated_rows)

    counts = {
        "resource_rows": len(rows),
        "planned_approve": len(approval_candidates),
        "planned_keep_or_defer": len(keep_rows),
        "planned_problem": len(problem_rows),
        "approved_after_plan": sum(1 for r in updated_rows if r.get("review_status") == "approved"),
        "pending_after_plan": sum(1 for r in updated_rows if r.get("review_status") == "pending_review"),
        "do_not_apply_after_plan": sum(1 for r in updated_rows if r.get("review_status") == "do_not_apply"),
        "duplicate_final_acronyms": len(dup_final),
        "by_basis_approved": dict(Counter(r.get("acronym_basis", "") for r in updated_rows if r.get("review_status") == "approved")),
        "by_confidence_approved": dict(Counter(r.get("confidence", "") for r in updated_rows if r.get("review_status") == "approved")),
    }
    report["counts"] = counts
    report["duplicate_final_acronyms_preview"] = dup_final[:100]
    report["written_to_resources"] = bool(args.mode == "apply" and not report["errors"])
    report["backups"] = backups
    report["outputs"] = {
        "approval_plan": str(report_dir / "v33_4a_approval_plan.csv"),
        "approval_candidates": str(report_dir / "v33_4a_approval_candidates.csv"),
        "kept_for_review": str(report_dir / "v33_4a_kept_for_review.csv"),
        "problem_rows": str(report_dir / "v33_4a_problem_rows.csv"),
    }
    report["passed"] = not bool(report["errors"])

    (report_dir / "v33_4a_label_acronym_approval_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary_lines = [
        "V33.4A Label Acronym Approval Preparer",
        "========================================================================",
        f"Generated: {report['generated_at']}",
        f"Project root: {root}",
        f"Mode: {args.mode}",
        f"Scope: {args.scope}",
        f"PASSED: {report['passed']}",
        "",
        "This step does not modify annotation volumes or structures.json.",
        "It only prepares or applies review_status flags in resources/label_curation.",
        "",
        "Counts:",
        f"- resource_rows: {counts['resource_rows']}",
        f"- planned_approve: {counts['planned_approve']}",
        f"- planned_keep_or_defer: {counts['planned_keep_or_defer']}",
        f"- planned_problem: {counts['planned_problem']}",
        f"- approved_after_plan: {counts['approved_after_plan']}",
        f"- pending_after_plan: {counts['pending_after_plan']}",
        f"- do_not_apply_after_plan: {counts['do_not_apply_after_plan']}",
        f"- duplicate_final_acronyms: {counts['duplicate_final_acronyms']}",
        "",
        "Approved basis breakdown:",
    ]
    for k, v in counts["by_basis_approved"].items():
        summary_lines.append(f"- {k}: {v}")
    summary_lines.extend([
        "",
        "Outputs:",
        f"- {report_dir / 'v33_4a_approval_plan.csv'}",
        f"- {report_dir / 'v33_4a_approval_candidates.csv'}",
        f"- {report_dir / 'v33_4a_kept_for_review.csv'}",
        f"- {report_dir / 'v33_4a_problem_rows.csv'}",
        "",
        "Next step:",
    ])
    if args.mode == "dry-run":
        summary_lines.append("- Review v33_4a_approval_candidates.csv. If sane, rerun the matching APPLY bat.")
    else:
        summary_lines.append("- Run the V33.3b validator again, then run V33.4B dry-run apply-to-structures.")
    if report["errors"]:
        summary_lines.extend(["", "Errors:"] + [f"- {e}" for e in report["errors"]])
    (report_dir / "v33_4a_label_acronym_approval_summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("\n".join(summary_lines))
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
