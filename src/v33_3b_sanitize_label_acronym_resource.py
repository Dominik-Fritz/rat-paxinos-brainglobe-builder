#!/usr/bin/env python3
"""
V33.3b Label Acronym Resource Sanitizer

Makes the curated Paxinos/Watson acronym resource safe for integration with the
currently built BrainGlobe atlas. This script never modifies annotation volumes.

Default mode is dry-run. Use --apply to replace the project resource CSV/TXT with
sanitized versions. A backup is created before replacement.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
TXT_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms.txt"
HIGH_CONF_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_high_confidence_review.csv"
NEEDS_REVIEW_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_needs_review.csv"
REPORT_REL = Path("reports") / "v33_3b_label_acronym_resource_sanitizer"

BASE_COLS = [
    "label_id", "paxinos_name", "proposed_acronym", "proposed_name",
    "acronym_basis", "basis_detail", "confidence", "review_status",
    "current_structure_acronym", "current_structure_name", "resource_action",
]

STATUS_MAP = {
    "approved_candidate": "pending_review",
    "do_not_apply_until_review": "do_not_apply",
    "": "pending_review",
}
ALLOWED_STATUS = {"pending_review", "approved", "rejected", "needs_manual_review", "defer", "do_not_apply"}
PLACEHOLDER_NAME_RE = re.compile(r"^[-–—_\s]+$")


def norm(s: Any) -> str:
    return str(s or "").strip()


def norm_status(s: Any) -> str:
    raw = norm(s).lower()
    return STATUS_MAP.get(raw, raw)


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


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
    checked: List[Dict[str, Any]] = []
    selected: Optional[Path] = None
    for d in candidate_atlas_dirs(root, explicit):
        st = d / "structures.json"
        ok = d.exists() and st.exists()
        checked.append({"atlas_dir": str(d), "exists": d.exists(), "structures_json": st.exists(), "usable": ok})
        if selected is None and ok:
            selected = d
    return selected, checked


def row_key(row: Dict[str, str]) -> int:
    return int(norm(row.get("label_id")))


def sanitize_resource(rows: List[Dict[str, str]], structures: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    structures_by_id = {int(s["id"]): s for s in structures if "id" in s}
    structure_ids = set(structures_by_id)

    source_by_id: Dict[int, Dict[str, str]] = {}
    duplicate_source_ids: List[int] = []
    for row in rows:
        try:
            sid = row_key(row)
        except Exception:
            continue
        if sid in source_by_id:
            duplicate_source_ids.append(sid)
        source_by_id[sid] = row

    sanitized: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    added_internal: List[Dict[str, Any]] = []

    for sid in sorted(source_by_id):
        row = dict(source_by_id[sid])
        st = structures_by_id.get(sid)
        status = norm_status(row.get("review_status"))
        if status not in ALLOWED_STATUS:
            status = "pending_review"
        row["review_status"] = status

        if st is None:
            row["resource_action"] = "excluded_source_id_not_in_current_structures"
            excluded.append(row)
            continue

        current_acr = norm(st.get("acronym"))
        current_name = norm(st.get("name"))
        row["current_structure_acronym"] = current_acr
        row["current_structure_name"] = current_name

        raw_name = norm(row.get("paxinos_name"))
        prop_name = norm(row.get("proposed_name"))
        prop_acr = norm(row.get("proposed_acronym"))
        basis = norm(row.get("acronym_basis"))

        # Placeholder raw entries should not overwrite the more useful generated current metadata.
        if basis == "paxinos_placeholder" or PLACEHOLDER_NAME_RE.match(raw_name) or PLACEHOLDER_NAME_RE.match(prop_name):
            row["proposed_acronym"] = current_acr or prop_acr
            row["proposed_name"] = current_name or prop_name or raw_name
            row["review_status"] = "do_not_apply"
            row["confidence"] = row.get("confidence") or "low"
            row["basis_detail"] = (norm(row.get("basis_detail")) + " | sanitized: placeholder raw label kept non-applying and aligned to current structure metadata").strip(" |")
            row["resource_action"] = "kept_nonapplying_placeholder_aligned_to_current"
        else:
            row["resource_action"] = "kept_for_review_or_manual_approval"

        sanitized.append(row)

    missing_in_source = sorted(structure_ids - set(source_by_id))
    for sid in missing_in_source:
        if sid == 997:
            # root is handled by the builder/ontology and should not get a Paxinos raw acronym row.
            continue
        st = structures_by_id[sid]
        row = {
            "label_id": sid,
            "paxinos_name": norm(st.get("name")),
            "proposed_acronym": norm(st.get("acronym")),
            "proposed_name": norm(st.get("name")),
            "acronym_basis": "builder_generated_internal_structure",
            "basis_detail": "Structure exists in current structures.json but not in raw Paxinos label file; retained as non-applying current metadata for complete resource coverage.",
            "confidence": "low",
            "review_status": "do_not_apply",
            "current_structure_acronym": norm(st.get("acronym")),
            "current_structure_name": norm(st.get("name")),
            "resource_action": "added_current_structure_nonapplying",
        }
        sanitized.append(row)
        added_internal.append(row)

    sanitized.sort(key=lambda r: int(r["label_id"]))

    acr_counter = Counter(norm(r.get("proposed_acronym")) for r in sanitized if norm(r.get("proposed_acronym")))
    duplicate_resource_acronyms = sorted([a for a, c in acr_counter.items() if c > 1])

    counts = {
        "source_rows": len(rows),
        "current_structure_count": len(structures),
        "sanitized_rows": len(sanitized),
        "excluded_source_ids_not_in_structures": len(excluded),
        "added_internal_structure_rows": len(added_internal),
        "duplicate_source_ids": len(duplicate_source_ids),
        "duplicate_resource_acronyms": len(duplicate_resource_acronyms),
        "by_status": dict(Counter(norm_status(r.get("review_status")) for r in sanitized)),
        "by_basis": dict(Counter(norm(r.get("acronym_basis")) for r in sanitized)),
        "duplicate_resource_acronyms_preview": duplicate_resource_acronyms[:50],
    }
    return sanitized, excluded, added_internal, counts


def write_txt(path: Path, rows: List[Dict[str, Any]]) -> None:
    lines = []
    for r in sorted(rows, key=lambda x: int(x["label_id"])):
        sid = int(r["label_id"])
        acr = norm(r.get("proposed_acronym"))
        name = norm(r.get("proposed_name")) or norm(r.get("paxinos_name"))
        lines.append(f'{sid}\t{acr}\t"{name}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="Project root. Default: current directory.")
    ap.add_argument("--atlas-dir", default=None, help="Optional explicit atlas dir containing structures.json.")
    ap.add_argument("--resource", default=None, help="Optional input resource CSV path.")
    ap.add_argument("--apply", action="store_true", help="Replace project resource CSV/TXT with sanitized versions.")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    report_dir = root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat(timespec="seconds")
    resource = Path(args.resource).expanduser().resolve() if args.resource else root / RESOURCE_REL

    selected, checked = find_usable_atlas(root, args.atlas_dir)
    errors: List[str] = []
    if not resource.exists():
        errors.append(f"Missing resource CSV: {resource}")
    if selected is None:
        errors.append("No usable atlas dir found. Run builder first or pass --atlas-dir.")

    report: Dict[str, Any] = {
        "version": "V33.3b Label Acronym Resource Sanitizer",
        "generated_at": generated_at,
        "project_root": str(root),
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "resource_csv": str(resource),
        "candidate_atlas_dirs": checked,
        "selected_atlas_dir": str(selected) if selected else None,
        "errors": errors,
    }

    if errors:
        report["passed"] = False
        (report_dir / "v33_3b_label_acronym_resource_sanitizer_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (report_dir / "v33_3b_label_acronym_resource_sanitizer_summary.txt").write_text("FAILED\n" + "\n".join(errors) + "\n", encoding="utf-8")
        print("FAILED before processing. See reports.")
        return 2

    rows = read_csv_dicts(resource)
    structures_path = selected / "structures.json"  # type: ignore[operator]
    structures = load_structures(structures_path)
    sanitized, excluded, added, counts = sanitize_resource(rows, structures)
    fieldnames = list(dict.fromkeys(BASE_COLS + [c for r in sanitized for c in r.keys()]))

    sanitized_preview = report_dir / "Paxinos_Watson_Labels_Acronyms_with_basis_SANITIZED_PREVIEW.csv"
    txt_preview = report_dir / "Paxinos_Watson_Labels_Acronyms_SANITIZED_PREVIEW.txt"
    write_csv(sanitized_preview, sanitized, fieldnames)
    write_txt(txt_preview, sanitized)
    write_csv(report_dir / "v33_3b_excluded_source_ids_not_in_current_structures.csv", excluded, fieldnames)
    write_csv(report_dir / "v33_3b_added_internal_structure_rows.csv", added, fieldnames)

    high_conf = [r for r in sanitized if norm(r.get("confidence")).lower() in {"high", "official_local"} and norm_status(r.get("review_status")) == "pending_review"]
    needs_review = [r for r in sanitized if norm_status(r.get("review_status")) in {"pending_review", "needs_manual_review"}]
    write_csv(report_dir / "v33_3b_high_confidence_pending_review.csv", high_conf, fieldnames)
    write_csv(report_dir / "v33_3b_needs_manual_review.csv", needs_review, fieldnames)

    written = False
    backups = []
    if args.apply:
        for target, source in [
            (root / RESOURCE_REL, sanitized_preview),
            (root / TXT_REL, txt_preview),
            (root / HIGH_CONF_REL, report_dir / "v33_3b_high_confidence_pending_review.csv"),
            (root / NEEDS_REVIEW_REL, report_dir / "v33_3b_needs_manual_review.csv"),
        ]:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = target.with_name(target.name + f".before_v33_3b_{stamp}.bak")
                shutil.copy2(target, backup)
                backups.append(str(backup))
            shutil.copy2(source, target)
        written = True

    report.update({
        "structures_json": str(structures_path),
        "counts": counts,
        "sanitized_preview_csv": str(sanitized_preview),
        "sanitized_preview_txt": str(txt_preview),
        "written_to_resources": written,
        "backups": backups,
        "passed": counts["duplicate_source_ids"] == 0 and counts["duplicate_resource_acronyms"] == 0,
    })

    summary = [
        "V33.3b Label Acronym Resource Sanitizer",
        "========================================================================",
        f"Generated: {generated_at}",
        f"Project root: {root}",
        f"Selected atlas dir: {selected}",
        f"Mode: {'APPLY' if args.apply else 'DRY_RUN'}",
        f"PASSED: {report['passed']}",
        "",
        "Counts:",
    ]
    for k in ["source_rows", "current_structure_count", "sanitized_rows", "excluded_source_ids_not_in_structures", "added_internal_structure_rows", "duplicate_source_ids", "duplicate_resource_acronyms"]:
        summary.append(f"- {k}: {counts.get(k)}")
    summary += ["", "Status breakdown:"]
    for k, v in sorted(counts["by_status"].items()):
        summary.append(f"- {k}: {v}")
    summary += ["", "Outputs:", f"- {sanitized_preview}", f"- {txt_preview}", "", "Next step:"]
    if args.apply:
        summary.append("- Run RUN_V33_3B_LABEL_ACRONYM_VALIDATE.bat.")
    else:
        summary.append("- Inspect preview files. If okay, run RUN_V33_3B_LABEL_ACRONYM_SANITIZE_APPLY.bat.")

    (report_dir / "v33_3b_label_acronym_resource_sanitizer_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (report_dir / "v33_3b_label_acronym_resource_sanitizer_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("PASSED" if report["passed"] else "FAILED")
    print(f"Reports: {report_dir}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
