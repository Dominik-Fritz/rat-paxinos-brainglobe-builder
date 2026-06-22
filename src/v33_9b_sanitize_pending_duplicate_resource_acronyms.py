#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V33.9b Sanitize Pending Duplicate Resource Acronyms

Purpose
-------
The V33.3b validator is intentionally strict and fails when *any* proposed_acronym
in the curation resource is duplicated, even when duplicate rows are not approved
and therefore would not be applied to structures.json.

V33.9 imported DeepSeek proposals safely: duplicate proposals were left pending,
so duplicate_final_acronyms_if_approved_applied remained zero. However, the raw
pending duplicate proposals still sit in proposed_acronym and make the strict
resource validator fail.

This script fixes only that validator-blocking bookkeeping issue:
- approved rows are never changed;
- annotation volumes are never changed;
- structures.json is never changed;
- non-approved rows that participate in proposed_acronym duplicate groups are
  given a unique non-applied placeholder proposal, preferably their current atlas
  acronym, so the resource table regains global acronym uniqueness;
- review_status remains pending_review/do_not_apply, so these rows are still not
  applied later.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
ROOT_ID = 997
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
TXT_RESOURCE_NAME = "Paxinos_Watson_Labels_Acronyms.txt"


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def norm_id(x) -> int:
    return int(str(x).strip())


def find_project_root() -> Path:
    return Path.cwd()


def candidate_atlas_dirs(project_root: Path) -> List[Path]:
    home = Path.home()
    return [
        project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_official_candidate" / f"{ATLAS_NAME}_v1.0",
        project_root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_provisional" / f"{ATLAS_NAME}_v1.0",
        project_root / "data" / "output" / "brainglobe_local_cache" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_local_cache" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / ATLAS_NAME,
    ]


def find_atlas_dir(project_root: Path) -> Tuple[Path, List[Dict]]:
    records = []
    selected = None
    for p in candidate_atlas_dirs(project_root):
        rec = {
            "atlas_dir": str(p),
            "exists": p.exists(),
            "structures_json": (p / "structures.json").exists(),
            "usable": p.exists() and (p / "structures.json").exists(),
        }
        records.append(rec)
        if rec["usable"] and selected is None:
            selected = p
    if selected is None:
        raise FileNotFoundError("No usable paxinos_watson_rat_40um atlas dir with structures.json found.")
    return selected, records


def load_structures(atlas_dir: Path) -> Dict[int, Dict]:
    with (atlas_dir / "structures.json").open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("structures.json is not a list")
    return {norm_id(s["id"]): s for s in data if "id" in s}


def read_resource(path: Path) -> Tuple[List[Dict], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    required = {"label_id", "proposed_acronym", "review_status"}
    missing = [c for c in required if c not in fieldnames]
    if missing:
        raise ValueError(f"Resource missing columns: {missing}")
    return rows, fieldnames


def write_resource(path: Path, rows: List[Dict], fieldnames: List[str]) -> None:
    needed = ["label_id", "paxinos_name", "proposed_acronym", "proposed_name", "acronym_basis", "basis_detail", "confidence", "review_status", "duplicate_resolution"]
    for c in needed:
        if c not in fieldnames:
            fieldnames.append(c)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_txt_resource(path: Path, rows: List[Dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        for r in rows:
            label_id = str(r.get("label_id", "")).strip()
            acro = str(r.get("proposed_acronym", "")).strip()
            name = str(r.get("proposed_name", r.get("paxinos_name", ""))).strip()
            f.write(f'{label_id}\t{acro}\t"{name}"\n')


def is_nonempty(a: str) -> bool:
    return bool(str(a or "").strip())


def unique_fallback(base: str, label_id: int, used: set) -> str:
    clean = str(base or "").strip()
    candidates = []
    if clean:
        candidates.append(clean)
        candidates.append(f"{clean}_{label_id}")
    candidates.append(f"PENDING_{label_id}")
    candidates.append(f"REVIEW_{label_id}")
    for c in candidates:
        if c and c not in used:
            return c
    i = 1
    while True:
        c = f"REVIEW_{label_id}_{i}"
        if c not in used:
            return c
        i += 1


def summarize_duplicates(rows: List[Dict]) -> Dict[str, List[Dict]]:
    by_acro = defaultdict(list)
    for i, r in enumerate(rows):
        acro = str(r.get("proposed_acronym", "")).strip()
        if acro:
            by_acro[acro].append((i, r))
    return {a: vals for a, vals in by_acro.items() if len(vals) > 1}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry-run", "apply"], required=True)
    args = ap.parse_args()

    project_root = find_project_root()
    report_dir = project_root / "reports" / "v33_9b_sanitize_pending_duplicate_resource_acronyms"
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    errors, warnings = [], []
    backups = []

    try:
        resource_csv = project_root / RESOURCE_REL
        if not resource_csv.exists():
            raise FileNotFoundError(f"Missing resource CSV: {resource_csv}")
        atlas_dir, atlas_candidates = find_atlas_dir(project_root)
        structures_by_id = load_structures(atlas_dir)
        rows, fieldnames = read_resource(resource_csv)
    except Exception as e:
        errors.append(str(e))
        report = {
            "version": "V33.9b Sanitize Pending Duplicate Resource Acronyms",
            "generated_at": generated_at,
            "project_root": str(project_root),
            "mode": args.mode,
            "errors": errors,
            "warnings": warnings,
            "passed": False,
        }
        (report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_summary.txt").write_text(
            "V33.9b Sanitize Pending Duplicate Resource Acronyms\n" + "="*72 + "\nPASSED: False\n" + "\n".join(errors) + "\n",
            encoding="utf-8",
        )
        print("PASSED: False")
        for e in errors:
            print("ERROR:", e)
        return 2

    duplicates_before = summarize_duplicates(rows)
    duplicate_rows_before = []
    changed_rows = []
    critical_approved_duplicates = []

    # If duplicate groups contain >1 approved rows, do not alter them automatically.
    for acro, vals in duplicates_before.items():
        approved = [(i, r) for i, r in vals if str(r.get("review_status", "")).strip() == "approved"]
        if len(approved) > 1:
            critical_approved_duplicates.append({
                "proposed_acronym": acro,
                "approved_label_ids": [str(r.get("label_id", "")).strip() for _, r in approved],
                "approved_names": [str(r.get("proposed_name", r.get("paxinos_name", ""))).strip() for _, r in approved],
            })

    if critical_approved_duplicates:
        errors.append(f"Found {len(critical_approved_duplicates)} duplicate groups with multiple approved rows; refusing automatic sanitize.")

    updated_rows = [dict(r) for r in rows]
    used = set()
    for r in updated_rows:
        acro = str(r.get("proposed_acronym", "")).strip()
        if acro and str(r.get("review_status", "")).strip() == "approved":
            used.add(acro)

    if not errors:
        for acro, vals in sorted(duplicates_before.items(), key=lambda kv: kv[0].lower()):
            for idx, r_orig in vals:
                r = updated_rows[idx]
                label_id_s = str(r.get("label_id", "")).strip()
                try:
                    label_id = norm_id(label_id_s)
                except Exception:
                    label_id = -1
                status = str(r.get("review_status", "")).strip()
                name = str(r.get("proposed_name", r.get("paxinos_name", ""))).strip()
                struct = structures_by_id.get(label_id, {})
                current_acro = str(struct.get("acronym", "")).strip()
                duplicate_rows_before.append({
                    "label_id": label_id_s,
                    "name": name,
                    "review_status": status,
                    "old_proposed_acronym": acro,
                    "current_atlas_acronym": current_acro,
                })
                if status == "approved":
                    # Keep approved row untouched. Its acronym is already in used.
                    continue
                old = acro
                new = unique_fallback(current_acro, label_id, used)
                used.add(new)
                r["proposed_acronym"] = new
                if "duplicate_resolution" in r:
                    old_res = str(r.get("duplicate_resolution", "")).strip()
                    r["duplicate_resolution"] = (old_res + "; " if old_res else "") + "v33_9b_pending_duplicate_sanitized_to_unique_current_acronym"
                if "basis_detail" in r:
                    old_detail = str(r.get("basis_detail", "")).strip()
                    note = "V33.9b: non-approved duplicate proposal sanitized to a unique non-applied placeholder/current acronym so strict resource validator can pass; review_status was preserved."
                    r["basis_detail"] = (old_detail + " | " if old_detail else "") + note
                changed_rows.append({
                    "label_id": label_id_s,
                    "name": name,
                    "review_status": status,
                    "old_proposed_acronym": old,
                    "new_proposed_acronym": new,
                    "current_atlas_acronym": current_acro,
                    "reason": "non-approved duplicate proposed_acronym sanitized; approved rows untouched",
                })

    duplicates_after = summarize_duplicates(updated_rows) if not errors else duplicates_before
    approved_counts_after = Counter(
        str(r.get("proposed_acronym", "")).strip()
        for r in updated_rows
        if str(r.get("review_status", "")).strip() == "approved" and str(r.get("proposed_acronym", "")).strip()
    )
    duplicate_approved_after = {a: c for a, c in approved_counts_after.items() if c > 1}
    if duplicate_approved_after:
        errors.append(f"Sanitizer would leave approved duplicate final acronyms: {duplicate_approved_after}")

    applied = False
    if args.mode == "apply" and not errors:
        stamp = now_stamp()
        bak_csv = resource_csv.with_suffix(resource_csv.suffix + f".before_v33_9b_{stamp}.bak")
        shutil.copy2(resource_csv, bak_csv)
        backups.append(str(bak_csv))
        txt_path = resource_csv.with_name(TXT_RESOURCE_NAME)
        if txt_path.exists():
            bak_txt = txt_path.with_suffix(txt_path.suffix + f".before_v33_9b_{stamp}.bak")
            shutil.copy2(txt_path, bak_txt)
            backups.append(str(bak_txt))
        write_resource(resource_csv, updated_rows, fieldnames)
        write_txt_resource(txt_path, updated_rows)
        applied = True

    def write_csv(filename: str, records: List[Dict]) -> str:
        path = report_dir / filename
        if records:
            cols = list(records[0].keys())
        else:
            cols = ["label_id", "name", "review_status", "old_proposed_acronym", "new_proposed_acronym", "current_atlas_acronym", "reason"]
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        return str(path)

    out_changed = write_csv("v33_9b_sanitized_pending_duplicate_rows.csv", changed_rows)
    out_before = write_csv("v33_9b_duplicate_rows_before.csv", duplicate_rows_before)

    report = {
        "version": "V33.9b Sanitize Pending Duplicate Resource Acronyms",
        "generated_at": generated_at,
        "project_root": str(project_root),
        "mode": args.mode,
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "does_modify_resources": args.mode == "apply",
        "resource_csv": str(resource_csv),
        "selected_atlas_dir": str(atlas_dir),
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "resource_rows": len(rows),
            "duplicate_resource_acronym_group_count_before": len(duplicates_before),
            "duplicate_resource_acronym_group_count_after": len(duplicates_after),
            "non_approved_duplicate_rows_sanitized": len(changed_rows),
            "critical_approved_duplicate_groups": len(critical_approved_duplicates),
            "approved_duplicate_final_acronyms_after": len(duplicate_approved_after),
        },
        "duplicate_acronyms_before": sorted(duplicates_before.keys()),
        "duplicate_acronyms_after": sorted(duplicates_after.keys()),
        "critical_approved_duplicates": critical_approved_duplicates,
        "written_to_resources": applied,
        "backups": backups,
        "outputs": {
            "sanitized_pending_duplicate_rows": out_changed,
            "duplicate_rows_before": out_before,
            "report": str(report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_report.json"),
            "summary": str(report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_summary.txt"),
        },
        "passed": not errors,
    }
    (report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V33.9b Sanitize Pending Duplicate Resource Acronyms",
        "="*72,
        f"Generated: {generated_at}",
        f"Project root: {project_root}",
        f"Selected atlas dir: {atlas_dir}",
        f"Mode: {args.mode}",
        f"PASSED: {not errors}",
        "",
        "This step never modifies annotation volumes or structures.json.",
        "It changes only non-approved duplicate proposed_acronym bookkeeping rows in the resource CSV when run in apply mode.",
        "Approved rows are left untouched.",
        "",
        "Counts:",
    ]
    for k, v in report["counts"].items():
        lines.append(f"- {k}: {v}")
    if errors:
        lines += ["", "Errors:"] + [f"- {e}" for e in errors]
    if warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in warnings]
    lines += ["", "Outputs:"] + [f"- {k}: {v}" for k, v in report["outputs"].items()]
    lines.append("")
    if not errors and args.mode == "dry-run":
        lines.append("Next step:")
        lines.append("- If the sanitized rows look acceptable, run RUN_V33_9B_SANITIZE_PENDING_DUPLICATE_RESOURCE_ACRONYMS_APPLY.bat.")
    elif not errors and args.mode == "apply":
        lines.append("Next step:")
        lines.append("- Run RUN_V33_3B_LABEL_ACRONYM_VALIDATE.bat again.")
        lines.append("- Then run RUN_V33_4B_APPLY_APPROVED_DRY_RUN.bat before applying to structures.json.")

    (report_dir / "v33_9b_sanitize_pending_duplicate_resource_acronyms_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
