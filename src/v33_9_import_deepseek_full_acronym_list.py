#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V33.9 DeepSeek Full Acronym Import

Read a 977-entry acronym list in V33.8 atlas-name order, lock root, skip dash/REVIEW
entries, flag duplicate acronym conflicts, and optionally update the label curation
resource files. Does not touch annotation volumes or structures.json.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
ROOT_ID = 997
DASH_MARKERS = {"", "-", "—", "–", "REVIEW", "review", "Review", "NA", "N/A", "n/a", "None", "none"}

RESOURCE_COLUMNS = [
    "label_id",
    "paxinos_name",
    "proposed_acronym",
    "proposed_name",
    "acronym_basis",
    "basis_detail",
    "confidence",
    "review_status",
    "duplicate_resolution",
]


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def norm_id(value) -> int:
    return int(str(value).strip())


def find_project_root() -> Path:
    # Batch runs from project root after package copy. If run from package folder in Downloads,
    # still allow read-only installed atlas use, but resources may be missing.
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


def load_structures(atlas_dir: Path) -> List[Dict]:
    path = atlas_dir / "structures.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("structures.json is not a list")
    for s in data:
        if "id" not in s:
            raise ValueError("structure without id found")
    return data


def atlas_order(structures: List[Dict]) -> List[Dict]:
    def key(s):
        return (int(s.get("graph_order", 10**12)), int(s.get("id", 10**12)))
    return sorted(structures, key=key)


def find_resource_csv(project_root: Path) -> Path:
    p = project_root / "resources" / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
    if not p.exists():
        raise FileNotFoundError(f"Missing resource CSV: {p}")
    return p


def read_resource(path: Path) -> Tuple[List[Dict], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not rows:
        raise ValueError("Resource CSV is empty")
    if "label_id" not in fieldnames:
        raise ValueError("Resource CSV lacks label_id column")
    return rows, fieldnames


def write_resource(path: Path, rows: List[Dict], fieldnames: List[str]) -> None:
    # Preserve all existing columns; ensure required ones exist.
    for col in RESOURCE_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def write_txt_resource(path: Path, rows: List[Dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        for r in rows:
            label_id = str(r.get("label_id", "")).strip()
            acro = str(r.get("proposed_acronym", "")).strip()
            name = str(r.get("proposed_name", r.get("paxinos_name", ""))).strip()
            f.write(f'{label_id}\t{acro}\t"{name}"\n')


def find_deepseek_input(project_root: Path) -> Path:
    candidates = [
        project_root / "resources" / "label_curation" / "deepseek_acronyms_977_raw.txt",
        project_root / "deepseek_acronyms_977_raw.txt",
        project_root / "reports" / "v33_9_deepseek_full_acronym_import" / "deepseek_acronyms_977_raw.txt",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Missing DeepSeek acronym input. Put deepseek_acronyms_977_raw.txt in resources/label_curation/ or project root."
    )


def strip_code_fence(text: str) -> str:
    # If pasted chat contains code fences, use the longest fenced block.
    blocks = re.findall(r"```(?:[a-zA-Z0-9_-]+)?\s*(.*?)```", text, flags=re.S)
    # Also tolerate double backticks, as some chats mangle Markdown.
    blocks += re.findall(r"``\s*(.*?)``", text, flags=re.S)
    if blocks:
        return max(blocks, key=len)
    return text


def parse_acronyms(raw_text: str) -> List[str]:
    text = strip_code_fence(raw_text)
    lines = []
    for line in text.splitlines():
        s = line.strip().strip("`").strip()
        if not s:
            continue
        # Strip markdown bullets accidentally copied.
        s = re.sub(r"^[\-•*]\s+", "", s).strip()
        # Ignore obvious prose/table lines if user pasted a whole chat.
        if s.startswith("|") or s.lower().startswith(("benutzer:", "assistent:", "hier ", "das ", "die ", "quelle", "einträge", "#")):
            continue
        # Keep single acronym-ish token or dash marker. Avoid full sentences.
        if len(s.split()) > 1 and s not in DASH_MARKERS:
            continue
        # Remove weird trailing punctuation from copied code block.
        s = s.rstrip("`").strip()
        lines.append(s)
    return lines


def normalize_acronym(a: str) -> str:
    a = a.strip().strip('"').strip("'").strip()
    a = a.replace("−", "-")
    return a


def is_dash(a: str) -> bool:
    return normalize_acronym(a) in DASH_MARKERS


def ensure_report_dir(project_root: Path) -> Path:
    out = project_root / "reports" / "v33_9_deepseek_full_acronym_import"
    out.mkdir(parents=True, exist_ok=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dry-run", "apply-resource"], required=True)
    args = parser.parse_args()

    project_root = find_project_root()
    report_dir = ensure_report_dir(project_root)
    generated_at = _dt.datetime.now().isoformat(timespec="seconds")
    errors, warnings = [], []

    try:
        atlas_dir, atlas_candidates = find_atlas_dir(project_root)
        structures = load_structures(atlas_dir)
        ordered = atlas_order(structures)
        resource_csv = find_resource_csv(project_root)
        resource_rows, resource_fields = read_resource(resource_csv)
        deepseek_input = find_deepseek_input(project_root)
        raw = deepseek_input.read_text(encoding="utf-8-sig", errors="replace")
        acronyms_raw = [normalize_acronym(x) for x in parse_acronyms(raw)]
    except Exception as e:
        errors.append(str(e))
        report = {
            "version": "V33.9 DeepSeek Full Acronym Import",
            "generated_at": generated_at,
            "project_root": str(project_root),
            "mode": args.mode,
            "errors": errors,
            "warnings": warnings,
            "passed": False,
        }
        (report_dir / "v33_9_deepseek_full_acronym_import_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (report_dir / "v33_9_deepseek_full_acronym_import_summary.txt").write_text(
            "V33.9 DeepSeek Full Acronym Import\n" + "="*72 + "\nPASSED: False\n\n" + "\n".join(errors) + "\n",
            encoding="utf-8",
        )
        print("PASSED: False")
        for err in errors:
            print("ERROR:", err)
        return 2

    if len(ordered) != 977:
        warnings.append(f"Expected 977 structures, found {len(ordered)}")
    if len(acronyms_raw) != len(ordered):
        errors.append(f"Expected {len(ordered)} acronyms from DeepSeek input, parsed {len(acronyms_raw)}")

    resource_by_id = {norm_id(r["label_id"]): r for r in resource_rows}
    structure_ids = [norm_id(s["id"]) for s in ordered]
    missing_resource_ids = [sid for sid in structure_ids if sid not in resource_by_id]
    if missing_resource_ids:
        errors.append(f"Resource missing {len(missing_resource_ids)} structure IDs; first: {missing_resource_ids[:10]}")

    proposed_by_id: Dict[int, str] = {}
    if not errors:
        for s, acro in zip(ordered, acronyms_raw):
            sid = norm_id(s["id"])
            if sid == ROOT_ID:
                proposed_by_id[sid] = "root"
            elif is_dash(acro):
                proposed_by_id[sid] = ""
            else:
                proposed_by_id[sid] = acro

    # Duplicate detection among non-empty proposals, including root after lock.
    counts = Counter(a for a in proposed_by_id.values() if a)
    duplicate_groups = {a: [] for a, c in counts.items() if c > 1}
    if duplicate_groups:
        for s in ordered:
            sid = norm_id(s["id"])
            a = proposed_by_id.get(sid, "")
            if a in duplicate_groups:
                duplicate_groups[a].append({"label_id": sid, "name": s.get("name", "")})

    imported_rows = []
    dash_rows = []
    duplicate_conflict_rows = []
    root_lock_rows = []
    approved_import_rows = []
    preview_rows = []

    updated_resource_rows = [dict(r) for r in resource_rows]
    updated_by_id = {norm_id(r["label_id"]): r for r in updated_resource_rows}

    if not errors:
        for idx, s in enumerate(ordered, start=1):
            sid = norm_id(s["id"])
            name = str(s.get("name", ""))
            current_acro = str(s.get("acronym", ""))
            ds_acro = proposed_by_id.get(sid, "")
            res = updated_by_id.get(sid)
            if res is None:
                continue
            base = {
                "deep_research_index": idx,
                "label_id": sid,
                "name": name,
                "current_acronym": current_acro,
                "deepseek_acronym": ds_acro if ds_acro else "REVIEW",
            }
            if sid == ROOT_ID:
                res.update({
                    "paxinos_name": "root",
                    "proposed_acronym": "root",
                    "proposed_name": "root",
                    "acronym_basis": "builder_root_lock",
                    "basis_detail": "Root is hard-locked to acronym root regardless of external acronym suggestions.",
                    "confidence": "locked",
                    "review_status": "approved",
                    "duplicate_resolution": "root_locked",
                })
                root_lock_rows.append({**base, "decision": "root_locked_approved"})
                approved_import_rows.append({**base, "decision": "approved"})
                continue
            if not ds_acro:
                # Keep existing row as-is, but ensure it is not accidentally approved by this import.
                # If it was already approved by prior curation, leave it approved. The dash means no new evidence.
                dash_rows.append({**base, "decision": "dash_or_review_no_change"})
                continue
            if ds_acro in duplicate_groups:
                res.update({
                    "paxinos_name": name,
                    "proposed_acronym": ds_acro,
                    "proposed_name": name,
                    "acronym_basis": "deepseek_paxinos_full_list_duplicate_conflict",
                    "basis_detail": "DeepSeek/Paxinos full-list proposal produced a duplicate acronym; left pending for manual disambiguation.",
                    "confidence": "conflict",
                    "review_status": "pending_review",
                    "duplicate_resolution": "duplicate_conflict_not_auto_approved",
                })
                duplicate_conflict_rows.append({**base, "decision": "duplicate_conflict_pending"})
                continue
            # Safe non-dash, non-duplicate import.
            res.update({
                "paxinos_name": name,
                "proposed_acronym": ds_acro,
                "proposed_name": name,
                "acronym_basis": "deepseek_paxinos_full_list",
                "basis_detail": "Imported from ordered DeepSeek/Paxinos acronym list using V33.8 graph_order/label_id order. Non-dash and non-duplicate proposal.",
                "confidence": "deepseek_paxinos_candidate",
                "review_status": "approved",
                "duplicate_resolution": "unique_imported",
            })
            approved_import_rows.append({**base, "decision": "approved"})
            if current_acro != ds_acro or name != str(res.get("proposed_name", "")):
                preview_rows.append({
                    **base,
                    "new_acronym": ds_acro,
                    "new_name": name,
                    "would_change_acronym": current_acro != ds_acro,
                })
            imported_rows.append({**base, "decision": "approved"})

    applied = False
    backups = []
    if args.mode == "apply-resource" and not errors:
        # Back up both resource files.
        stamp = now_stamp()
        txt_path = resource_csv.with_name("Paxinos_Watson_Labels_Acronyms.txt")
        bak_csv = resource_csv.with_suffix(resource_csv.suffix + f".before_v33_9_{stamp}.bak")
        shutil.copy2(resource_csv, bak_csv)
        backups.append(str(bak_csv))
        if txt_path.exists():
            bak_txt = txt_path.with_suffix(txt_path.suffix + f".before_v33_9_{stamp}.bak")
            shutil.copy2(txt_path, bak_txt)
            backups.append(str(bak_txt))
        write_resource(resource_csv, updated_resource_rows, resource_fields)
        write_txt_resource(txt_path, updated_resource_rows)
        applied = True

    # Outputs
    def write_csv(name: str, rows: List[Dict]):
        path = report_dir / name
        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = ["deep_research_index", "label_id", "name", "current_acronym", "deepseek_acronym", "decision"]
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return str(path)

    out_approved = write_csv("v33_9_approved_import_rows.csv", approved_import_rows)
    out_dash = write_csv("v33_9_dash_or_review_rows.csv", dash_rows)
    out_dups = write_csv("v33_9_duplicate_conflict_rows.csv", duplicate_conflict_rows)
    out_preview = write_csv("v33_9_structures_change_preview_from_deepseek.csv", preview_rows)
    out_root = write_csv("v33_9_root_lock_rows.csv", root_lock_rows)

    dup_preview = {
        acro: vals[:10]
        for acro, vals in sorted(duplicate_groups.items(), key=lambda kv: kv[0].lower())
    }
    status_after = Counter(r.get("review_status", "") for r in updated_resource_rows)

    report = {
        "version": "V33.9 DeepSeek Full Acronym Import",
        "generated_at": generated_at,
        "project_root": str(project_root),
        "mode": args.mode,
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "does_modify_resources": args.mode == "apply-resource",
        "selected_atlas_dir": str(atlas_dir),
        "structures_json": str(atlas_dir / "structures.json"),
        "resource_csv": str(resource_csv),
        "deepseek_input": str(deepseek_input),
        "ordering": "graph_order ascending, then label_id ascending; root label_id 997 hard-locked to root",
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "structure_count": len(ordered),
            "resource_rows": len(resource_rows),
            "parsed_deepseek_acronym_count": len(acronyms_raw),
            "dash_or_review_count": len(dash_rows),
            "root_locked_count": len(root_lock_rows),
            "approved_import_count": len(approved_import_rows),
            "duplicate_acronym_group_count": len(duplicate_groups),
            "duplicate_conflict_row_count": len(duplicate_conflict_rows),
            "structures_change_preview_count": len(preview_rows),
            "status_after": dict(status_after),
        },
        "duplicate_acronym_preview": dup_preview,
        "written_to_resources": applied,
        "backups": backups,
        "outputs": {
            "approved_import_rows": out_approved,
            "dash_or_review_rows": out_dash,
            "duplicate_conflict_rows": out_dups,
            "structures_change_preview_from_deepseek": out_preview,
            "root_lock_rows": out_root,
            "report": str(report_dir / "v33_9_deepseek_full_acronym_import_report.json"),
            "summary": str(report_dir / "v33_9_deepseek_full_acronym_import_summary.txt"),
        },
        "passed": not errors,
    }

    report_path = report_dir / "v33_9_deepseek_full_acronym_import_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("V33.9 DeepSeek Full Acronym Import")
    lines.append("=" * 72)
    lines.append(f"Generated: {generated_at}")
    lines.append(f"Project root: {project_root}")
    lines.append(f"Selected atlas dir: {atlas_dir}")
    lines.append(f"Mode: {args.mode}")
    lines.append(f"PASSED: {not errors}")
    lines.append("")
    lines.append("This step does not modify annotation volumes or structures.json.")
    if args.mode == "dry-run":
        lines.append("This dry-run does not modify resource files.")
    else:
        lines.append("This apply-resource step modifies only resources/label_curation files.")
    lines.append("")
    lines.append("Counts:")
    for k, v in report["counts"].items():
        lines.append(f"- {k}: {v}")
    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors:
            lines.append(f"- {e}")
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    lines.append("Outputs:")
    for k, v in report["outputs"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    if not errors and args.mode == "dry-run":
        lines.append("Next step:")
        lines.append("- Review duplicate_conflict_rows and dash_or_review_rows.")
        lines.append("- If acceptable, run RUN_V33_9_IMPORT_DEEPSEEK_ACRONYMS_APPLY_RESOURCE.bat.")
    elif not errors and args.mode == "apply-resource":
        lines.append("Next step:")
        lines.append("- Run RUN_V33_3B_LABEL_ACRONYM_VALIDATE.bat.")
        lines.append("- Then run RUN_V33_4B_APPLY_APPROVED_DRY_RUN.bat before applying to structures.json.")
    summary_path = report_dir / "v33_9_deepseek_full_acronym_import_summary.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
