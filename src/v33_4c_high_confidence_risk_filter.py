from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

RISK_LABEL_IDS = {
    # suffix/forced-disambiguation rows or explicit review/conflict rows from V33.4B dry-run
    177, 194, 350, 352, 405, 461, 482, 483, 582, 591, 594, 636, 637, 648, 657, 836,
}
RISK_WORDS = re.compile(r"\b(review|conflict|overlap)\b", re.IGNORECASE)
SUFFIX_RE = re.compile(r"_\d+$")
ALLOWED_STATUS = {"approved", "pending_review", "do_not_apply", "rejected", "needs_manual_review"}


def read_csv_dicts(path: Path) -> Tuple[List[dict], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def write_csv_dicts(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def get_col(row: dict, *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name])
    return ""


def parse_int(value: str, default: int = -1) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def risk_reason(row: dict) -> List[str]:
    reasons = []
    sid = parse_int(get_col(row, "label_id", "id"))
    acr = get_col(row, "proposed_acronym", "new_acronym", "acronym")
    detail = get_col(row, "basis_detail", "curation_note", "manual_note")
    status = get_col(row, "review_status", "curation_status").strip()

    if status != "approved":
        return reasons
    if sid in RISK_LABEL_IDS:
        reasons.append("risk_label_id_from_v33_4b_review")
    if SUFFIX_RE.search(acr):
        reasons.append("numeric_suffix_acronym_needs_manual_review")
    if RISK_WORDS.search(detail):
        reasons.append("basis_detail_contains_review_conflict_or_overlap")
    # Extra hard block: never let root-like structural identity be changed through this filter
    name = get_col(row, "paxinos_name", "proposed_name", "name")
    if sid == 997 or name.strip().lower() == "root":
        reasons.append("root_or_root_like_row_needs_manual_review")
    return reasons


def rebuild_txt_from_resource(csv_path: Path, txt_path: Path) -> None:
    rows, _ = read_csv_dicts(csv_path)
    with txt_path.open("w", encoding="utf-8", newline="") as f:
        for r in rows:
            sid = get_col(r, "label_id", "id")
            acr = get_col(r, "proposed_acronym", "new_acronym", "acronym")
            name = get_col(r, "proposed_name", "paxinos_name", "name")
            # Preserve simple Cortex-like format: ID<TAB>ACRONYM<TAB>"NAME"
            name = name.replace('"', "'")
            f.write(f'{sid}\t{acr}\t"{name}"\n')


def main() -> int:
    parser = argparse.ArgumentParser(description="V33.4C demote risky approved high-confidence acronym rows back to pending_review.")
    parser.add_argument("--root", default=None, help="Project root. Default: parent of script parent.")
    parser.add_argument("--apply", action="store_true", help="Actually write resource CSV/TXT. Default is dry-run.")
    args = parser.parse_args()

    if args.root:
        root = Path(args.root).resolve()
    else:
        root = Path(__file__).resolve().parents[1]

    resource_dir = root / "resources" / "label_curation"
    csv_path = resource_dir / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
    txt_path = resource_dir / "Paxinos_Watson_Labels_Acronyms.txt"
    report_dir = root / "reports" / "v33_4c_high_confidence_risk_filter"
    report_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now().isoformat(timespec="seconds")
    errors = []
    warnings = []
    if not csv_path.exists():
        errors.append(f"Missing resource CSV: {csv_path}")
        report = {"version": "V33.4C High-Confidence Risk Filter", "generated_at": generated_at, "project_root": str(root), "mode": "apply" if args.apply else "dry-run", "errors": errors, "passed": False}
        (report_dir / "v33_4c_high_confidence_risk_filter_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (report_dir / "v33_4c_high_confidence_risk_filter_summary.txt").write_text("\n".join(["V33.4C High-Confidence Risk Filter", "="*72, f"PASSED: False", *errors]), encoding="utf-8")
        return 2

    rows, fieldnames = read_csv_dicts(csv_path)
    if "review_status" not in fieldnames:
        errors.append("Resource CSV has no review_status column.")

    demoted = []
    kept_approved = []
    status_counts_before: Dict[str, int] = {}
    status_counts_after: Dict[str, int] = {}

    new_rows = []
    for r in rows:
        status = get_col(r, "review_status").strip()
        status_counts_before[status] = status_counts_before.get(status, 0) + 1
        rr = dict(r)
        reasons = risk_reason(rr)
        if reasons:
            rr["review_status"] = "pending_review"
            if "manual_note" in fieldnames:
                old_note = rr.get("manual_note", "")
                note = "; ".join(reasons)
                rr["manual_note"] = (old_note + "; " + note).strip("; ") if old_note else note
            out = dict(rr)
            out["demotion_reason"] = ";".join(reasons)
            demoted.append(out)
        elif status == "approved":
            kept_approved.append(rr)
        new_rows.append(rr)
        status_after = rr.get("review_status", "")
        status_counts_after[status_after] = status_counts_after.get(status_after, 0) + 1

    # Validate statuses after demotion
    invalid_after = sorted({r.get("review_status", "") for r in new_rows if r.get("review_status", "") not in ALLOWED_STATUS})
    if invalid_after:
        errors.append(f"Invalid review_status values after demotion: {invalid_after}")

    # Write reports
    demoted_fields = list(fieldnames) + ["demotion_reason"] if fieldnames else []
    write_csv_dicts(report_dir / "v33_4c_demoted_high_confidence_rows.csv", demoted, demoted_fields if demoted_fields else ["demotion_reason"])
    write_csv_dicts(report_dir / "v33_4c_kept_approved_rows.csv", kept_approved, fieldnames)

    written = False
    backups = []
    if args.apply and not errors:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if csv_path.exists():
            b = csv_path.with_name(csv_path.name + f".before_v33_4c_{stamp}.bak")
            b.write_bytes(csv_path.read_bytes())
            backups.append(str(b))
        if txt_path.exists():
            b = txt_path.with_name(txt_path.name + f".before_v33_4c_{stamp}.bak")
            b.write_bytes(txt_path.read_bytes())
            backups.append(str(b))
        write_csv_dicts(csv_path, new_rows, fieldnames)
        rebuild_txt_from_resource(csv_path, txt_path)
        written = True

    report = {
        "version": "V33.4C High-Confidence Risk Filter",
        "generated_at": generated_at,
        "project_root": str(root),
        "mode": "apply" if args.apply else "dry-run",
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "resource_csv": str(csv_path),
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "resource_rows": len(rows),
            "approved_before": status_counts_before.get("approved", 0),
            "demoted_approved_rows": len(demoted),
            "approved_after": status_counts_after.get("approved", 0),
            "pending_after": status_counts_after.get("pending_review", 0),
            "do_not_apply_after": status_counts_after.get("do_not_apply", 0),
            "invalid_status_after_count": len(invalid_after),
        },
        "status_counts_before": status_counts_before,
        "status_counts_after": status_counts_after,
        "written_to_resources": written,
        "backups": backups,
        "outputs": {
            "demoted_rows": str(report_dir / "v33_4c_demoted_high_confidence_rows.csv"),
            "kept_approved_rows": str(report_dir / "v33_4c_kept_approved_rows.csv"),
        },
        "passed": not errors,
    }
    (report_dir / "v33_4c_high_confidence_risk_filter_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V33.4C High-Confidence Risk Filter",
        "=" * 72,
        f"Generated: {generated_at}",
        f"Project root: {root}",
        f"Mode: {'apply' if args.apply else 'dry-run'}",
        f"PASSED: {not errors}",
        "",
        "This step does not modify annotation volumes or structures.json.",
        "It only demotes approved high-confidence rows that still contain review/conflict/suffix risk signals.",
        "",
        "Counts:",
        f"- resource_rows: {len(rows)}",
        f"- approved_before: {status_counts_before.get('approved', 0)}",
        f"- demoted_approved_rows: {len(demoted)}",
        f"- approved_after: {status_counts_after.get('approved', 0)}",
        f"- pending_after: {status_counts_after.get('pending_review', 0)}",
        f"- do_not_apply_after: {status_counts_after.get('do_not_apply', 0)}",
        "",
        "Outputs:",
        f"- {report_dir / 'v33_4c_demoted_high_confidence_rows.csv'}",
        f"- {report_dir / 'v33_4c_kept_approved_rows.csv'}",
        "",
        "Next step:",
        "- If dry-run looks sane, run APPLY, then run RUN_V33_3B_LABEL_ACRONYM_VALIDATE.bat again.",
    ]
    if errors:
        lines.extend(["", "Errors:", *[f"- {e}" for e in errors]])
    (report_dir / "v33_4c_high_confidence_risk_filter_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
