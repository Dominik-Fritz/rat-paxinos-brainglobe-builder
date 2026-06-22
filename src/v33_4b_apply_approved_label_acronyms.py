from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple

VERSION = "V33.4B Apply Approved Label Acronym Curation"
ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
REPORT_REL = Path("reports") / "v33_4b_apply_approved_label_acronyms"

APPROVED = {"approved"}
DEFERRED = {"pending_review", "do_not_apply", "rejected", "defer", "needs_manual_review"}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys = []
        seen = set()
        for row in rows:
            for k in row.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_resource_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def get_user_home() -> Path:
    return Path.home()


def candidate_atlas_dirs(root: Path) -> List[Path]:
    # Project outputs first, then installed BrainGlobe cache. Keep both _v1.0 and non-suffixed variants.
    return [
        root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_official_candidate" / f"{ATLAS_NAME}_v1.0",
        root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_provisional" / f"{ATLAS_NAME}_v1.0",
        root / "data" / "output" / "brainglobe_local_cache" / ATLAS_NAME,
        root / "data" / "output" / "brainglobe_local_cache" / f"{ATLAS_NAME}_v1.0",
        get_user_home() / ".brainglobe" / f"{ATLAS_NAME}_v1.0",
        get_user_home() / ".brainglobe" / ATLAS_NAME,
    ]


def atlas_dir_info(path: Path) -> Dict[str, Any]:
    structures_json = path / "structures.json"
    annotation_nii = path / "annotation.nii.gz"
    annotation_tiff = path / "annotation.tiff"
    metadata_json = path / "metadata.json"
    return {
        "atlas_dir": str(path),
        "exists": path.exists(),
        "structures_json": structures_json.exists(),
        "annotation_nii": annotation_nii.exists(),
        "annotation_tiff": annotation_tiff.exists(),
        "metadata_json": metadata_json.exists(),
        "usable": structures_json.exists(),
    }


def select_atlas_dir(root: Path, explicit: str | None = None) -> Tuple[Path | None, List[Dict[str, Any]]]:
    if explicit:
        p = Path(explicit)
        return p, [atlas_dir_info(p)]
    infos = [atlas_dir_info(p) for p in candidate_atlas_dirs(root)]
    for info in infos:
        if info["usable"]:
            return Path(info["atlas_dir"]), infos
    return None, infos


def normalize_status(x: str) -> str:
    return (x or "").strip().lower()


def normalize_int(x: str) -> int | None:
    try:
        return int(str(x).strip())
    except Exception:
        return None


def structure_by_id(structures: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out = {}
    for s in structures:
        sid = s.get("id")
        try:
            sid_i = int(sid)
        except Exception:
            continue
        out[sid_i] = s
    return out


def validate_and_plan(resource_rows: List[Dict[str, str]], structures: List[Dict[str, Any]]) -> Tuple[List[str], List[str], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    errors: List[str] = []
    warnings: List[str] = []
    by_id = structure_by_id(structures)
    struct_ids = set(by_id)
    resource_ids = []
    resource_by_id: Dict[int, Dict[str, str]] = {}
    blocked: List[Dict[str, Any]] = []

    for i, row in enumerate(resource_rows, start=2):
        lid = normalize_int(row.get("label_id", ""))
        if lid is None:
            blocked.append({"line": i, "label_id": row.get("label_id", ""), "reason": "invalid_label_id"})
            continue
        if lid in resource_by_id:
            blocked.append({"line": i, "label_id": lid, "reason": "duplicate_resource_id"})
        resource_by_id[lid] = row
        resource_ids.append(lid)
        status = normalize_status(row.get("review_status", ""))
        if status not in APPROVED and status not in DEFERRED:
            blocked.append({"line": i, "label_id": lid, "reason": "unknown_review_status", "review_status": status})

    missing_in_structures = sorted(set(resource_ids) - struct_ids)
    missing_in_resource = sorted(struct_ids - set(resource_ids))
    if missing_in_structures:
        errors.append(f"Resource contains {len(missing_in_structures)} IDs not present in structures.json.")
    if missing_in_resource:
        errors.append(f"structures.json contains {len(missing_in_resource)} IDs not present in resource CSV.")

    # Build final acronym map if all approved changes were applied.
    final_acronyms: Dict[str, List[int]] = {}
    approved_rows = []
    changes = []
    noops = []

    for sid, s in by_id.items():
        r = resource_by_id.get(sid)
        current_acr = str(s.get("acronym", ""))
        current_name = str(s.get("name", ""))
        final_acr = current_acr
        final_name = current_name
        if r and normalize_status(r.get("review_status", "")) in APPROVED:
            new_acr = (r.get("proposed_acronym") or "").strip()
            new_name = (r.get("proposed_name") or r.get("paxinos_name") or "").strip()
            if not new_acr or not new_name:
                blocked.append({"label_id": sid, "reason": "approved_row_missing_new_acronym_or_name", "proposed_acronym": new_acr, "proposed_name": new_name})
            else:
                approved_rows.append(r)
                final_acr = new_acr
                final_name = new_name
                common = {
                    "label_id": sid,
                    "old_acronym": current_acr,
                    "old_name": current_name,
                    "new_acronym": new_acr,
                    "new_name": new_name,
                    "acronym_basis": r.get("acronym_basis", ""),
                    "basis_detail": r.get("basis_detail", ""),
                    "confidence": r.get("confidence", ""),
                    "review_status": r.get("review_status", ""),
                }
                if current_acr != new_acr or current_name != new_name:
                    changes.append(common)
                else:
                    noops.append(common)
        final_acronyms.setdefault(final_acr, []).append(sid)

    duplicate_final = {k: v for k, v in final_acronyms.items() if k and len(v) > 1}
    if duplicate_final:
        errors.append(f"Applying approved rows would create {len(duplicate_final)} duplicate final acronyms.")
        for acr, ids in list(duplicate_final.items())[:50]:
            blocked.append({"reason": "duplicate_final_acronym", "acronym": acr, "ids": ";".join(map(str, ids))})

    if blocked:
        warnings.append(f"Blocked/review rows found: {len(blocked)}. See CSV. Hard errors only if ID coverage or final duplicate acronym problems exist.")

    counts = {
        "structure_count": len(structures),
        "resource_rows": len(resource_rows),
        "approved_rows": len(approved_rows),
        "approved_changes": len(changes),
        "approved_noops": len(noops),
        "blocked_rows": len(blocked),
        "resource_ids_missing_in_structures_count": len(missing_in_structures),
        "structure_ids_missing_in_resource_count": len(missing_in_resource),
        "duplicate_final_acronyms_count": len(duplicate_final),
        "missing_in_structures_preview": missing_in_structures[:100],
        "missing_in_resource_preview": missing_in_resource[:100],
        "duplicate_final_acronyms_preview": {k: v for k, v in list(duplicate_final.items())[:20]},
    }
    return errors, warnings, counts, changes, noops, blocked


def apply_changes_to_structures(structures: List[Dict[str, Any]], changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    change_by_id = {int(c["label_id"]): c for c in changes}
    out = []
    for s in structures:
        sid = int(s["id"])
        ns = dict(s)
        if sid in change_by_id:
            c = change_by_id[sid]
            ns["acronym"] = c["new_acronym"]
            ns["name"] = c["new_name"]
        out.append(ns)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--root", default=None, help="Project root. Defaults to current working directory.")
    ap.add_argument("--atlas-dir", default=None, help="Explicit atlas directory containing structures.json.")
    ap.add_argument("--apply", action="store_true", help="Actually write structures.json. Without this flag, dry-run only.")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    report_dir = root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)

    resource_csv = root / RESOURCE_REL
    selected_atlas_dir, atlas_infos = select_atlas_dir(root, args.atlas_dir)

    report: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(root),
        "mode": "apply" if args.apply else "dry-run",
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": bool(args.apply),
        "resource_csv": str(resource_csv),
        "candidate_atlas_dirs": atlas_infos,
        "selected_atlas_dir": str(selected_atlas_dir) if selected_atlas_dir else None,
        "errors": [],
        "warnings": [],
    }

    if not resource_csv.exists():
        report["errors"].append(f"Missing resource CSV: {resource_csv}")
        write_json(report_dir / "v33_4b_apply_approved_label_acronyms_report.json", report)
        (report_dir / "v33_4b_apply_approved_label_acronyms_summary.txt").write_text(f"{VERSION}\nFAILED: Missing resource CSV: {resource_csv}\n", encoding="utf-8")
        return 2
    if not selected_atlas_dir:
        report["errors"].append("No usable atlas directory found.")
        write_json(report_dir / "v33_4b_apply_approved_label_acronyms_report.json", report)
        (report_dir / "v33_4b_apply_approved_label_acronyms_summary.txt").write_text(f"{VERSION}\nFAILED: No usable atlas directory found.\n", encoding="utf-8")
        return 2

    structures_json = selected_atlas_dir / "structures.json"
    if not structures_json.exists():
        report["errors"].append(f"Missing structures.json: {structures_json}")
        write_json(report_dir / "v33_4b_apply_approved_label_acronyms_report.json", report)
        return 2

    resource_rows = read_resource_csv(resource_csv)
    structures = read_json(structures_json)
    errors, warnings, counts, changes, noops, blocked = validate_and_plan(resource_rows, structures)
    report["errors"].extend(errors)
    report["warnings"].extend(warnings)
    report["structures_json"] = str(structures_json)
    report["counts"] = counts

    write_csv(report_dir / "v33_4b_approved_change_plan.csv", changes)
    write_csv(report_dir / "v33_4b_approved_noop_rows.csv", noops)
    write_csv(report_dir / "v33_4b_blocked_or_review_rows.csv", blocked)

    applied_changes: List[Dict[str, Any]] = []
    backups: List[str] = []
    if errors:
        report["passed"] = False
    elif args.apply:
        backup = structures_json.with_suffix(structures_json.suffix + f".before_v33_4b_{now_stamp()}.bak")
        shutil.copy2(structures_json, backup)
        backups.append(str(backup))
        new_structures = apply_changes_to_structures(structures, changes)
        write_json(structures_json, new_structures)
        applied_changes = changes
        write_csv(report_dir / "v33_4b_applied_changes.csv", applied_changes)
        report["passed"] = True
    else:
        write_csv(report_dir / "v33_4b_applied_changes.csv", [])
        report["passed"] = True

    report["backups"] = backups
    report["applied_change_count"] = len(applied_changes)
    report["outputs"] = {
        "change_plan": str(report_dir / "v33_4b_approved_change_plan.csv"),
        "noop_rows": str(report_dir / "v33_4b_approved_noop_rows.csv"),
        "blocked_rows": str(report_dir / "v33_4b_blocked_or_review_rows.csv"),
        "applied_changes": str(report_dir / "v33_4b_applied_changes.csv"),
        "report": str(report_dir / "v33_4b_apply_approved_label_acronyms_report.json"),
        "summary": str(report_dir / "v33_4b_apply_approved_label_acronyms_summary.txt"),
    }

    write_json(report_dir / "v33_4b_apply_approved_label_acronyms_report.json", report)

    lines = []
    lines.append(VERSION)
    lines.append("=" * 72)
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Project root: {root}")
    lines.append(f"Selected atlas dir: {selected_atlas_dir}")
    lines.append(f"Mode: {report['mode']}")
    lines.append(f"PASSED: {report['passed']}")
    lines.append("")
    lines.append("This step does not modify annotation volumes.")
    if args.apply:
        lines.append("This APPLY step modifies only structures.json acronym/name fields for approved rows.")
    else:
        lines.append("This DRY-RUN step does not modify structures.json.")
    lines.append("")
    lines.append("Counts:")
    for k in ["structure_count", "resource_rows", "approved_rows", "approved_changes", "approved_noops", "blocked_rows", "resource_ids_missing_in_structures_count", "structure_ids_missing_in_resource_count", "duplicate_final_acronyms_count"]:
        lines.append(f"- {k}: {counts.get(k)}")
    lines.append("")
    if errors:
        lines.append("Errors:")
        for e in errors:
            lines.append(f"- {e}")
        lines.append("")
    if warnings:
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
    lines.append("Outputs:")
    for value in report["outputs"].values():
        lines.append(f"- {value}")
    if args.apply:
        lines.append("")
        lines.append("Next step:")
        lines.append("- Re-run the V33.3b validator and then test the atlas in ABBA.")
    else:
        lines.append("")
        lines.append("Next step:")
        lines.append("- Review v33_4b_approved_change_plan.csv. If sane, run the APPLY bat.")

    (report_dir / "v33_4b_apply_approved_label_acronyms_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
