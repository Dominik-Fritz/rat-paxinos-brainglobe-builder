from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
CACHE_DIR_NAME = f"{ATLAS_NAME}_v1.0"
ROOT_ID = 997
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
REPORT_REL = Path("reports") / "apply_curated_acronym_resource"
CHECK_IDS = [997, 739, 348, 448, 449, 450, 451, 642, 646, 859, 861, 984, 985, 998800, 637, 709]
APPLICABLE_REVIEW_STATUSES = {"approved", "display_only"}


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def norm_status(value: Any) -> str:
    return norm(value).lower()


def parse_int(value: Any) -> Optional[int]:
    try:
        return int(norm(value))
    except Exception:
        return None


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_json_list(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"structures.json is not a JSON list: {path}")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"structures.json entry {i} is not an object: {path}")
    return data


def write_json_list(path: Path, data: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def candidate_targets(root: Path) -> List[Tuple[str, Path, bool]]:
    """Return (target_label, atlas_dir, is_installed_target)."""
    home = Path.home()
    raw = [
        ("official", root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME, False),
        ("official_v1", root / "data" / "output" / "brainglobe_official_candidate" / CACHE_DIR_NAME, False),
        ("provisional", root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME, False),
        ("provisional_v1", root / "data" / "output" / "brainglobe_provisional" / CACHE_DIR_NAME, False),
        ("project_cache", root / "data" / "output" / "brainglobe_local_cache" / ATLAS_NAME, False),
        ("project_cache_v1", root / "data" / "output" / "brainglobe_local_cache" / CACHE_DIR_NAME, False),
        ("installed", home / ".brainglobe" / CACHE_DIR_NAME, True),
        ("installed_nosuffix", home / ".brainglobe" / ATLAS_NAME, True),
    ]
    seen = set()
    out: List[Tuple[str, Path, bool]] = []
    for label, path, installed in raw:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append((label, path, installed))
    return out


def select_targets(root: Path, explicit_atlas_dir: Optional[str], require_installed: bool) -> Tuple[List[Tuple[str, Path, bool]], List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    if explicit_atlas_dir:
        atlas_dir = Path(explicit_atlas_dir).expanduser().resolve()
        targets = [("explicit", atlas_dir, False)]
    else:
        targets = candidate_targets(root)

    checked: List[Dict[str, Any]] = []
    usable: List[Tuple[str, Path, bool]] = []
    installed_usable = False
    for label, atlas_dir, is_installed in targets:
        structures_json = atlas_dir / "structures.json"
        info = {
            "target": label,
            "atlas_dir": str(atlas_dir),
            "is_installed_target": is_installed,
            "atlas_dir_exists": atlas_dir.exists(),
            "structures_json_exists": structures_json.exists(),
            "usable": structures_json.exists(),
        }
        checked.append(info)
        if structures_json.exists():
            usable.append((label, atlas_dir, is_installed))
            installed_usable = installed_usable or is_installed

    if require_installed and not installed_usable:
        errors.append(
            "Installed .brainglobe structures.json target is missing. "
            "Clean builds must not silently skip curated acronym application."
        )
    if not usable:
        errors.append("No structures.json target found. Run the builder first or pass --atlas-dir.")
    return usable, checked, errors


def build_approved_map(rows: List[Dict[str, str]]) -> Tuple[Dict[int, Dict[str, str]], List[Dict[str, Any]], List[str]]:
    approved: Dict[int, Dict[str, str]] = {}
    blocked_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    seen_ids: Dict[int, int] = {}

    for line_no, row in enumerate(rows, start=2):
        sid = parse_int(row.get("label_id"))
        status = norm_status(row.get("review_status"))
        if sid is None:
            blocked_rows.append({"line": line_no, "label_id": row.get("label_id", ""), "reason": "invalid_label_id"})
            continue
        if sid in seen_ids:
            blocked_rows.append({"line": line_no, "label_id": sid, "reason": "duplicate_resource_label_id", "first_seen_line": seen_ids[sid]})
            continue
        seen_ids[sid] = line_no
        if status not in APPLICABLE_REVIEW_STATUSES:
            continue
        approved[sid] = row

    # Root is hard-locked even if the CSV is edited later by an overconfident mammal.
    approved[ROOT_ID] = {
        "label_id": str(ROOT_ID),
        "proposed_acronym": "root",
        "proposed_name": "root",
        "paxinos_name": "root",
        "acronym_basis": "builder_root_lock",
        "basis_detail": "label_id 997 is hard-locked to root/root during curated acronym application.",
        "confidence": "locked",
        "review_status": "approved",
    }

    if blocked_rows:
        warnings.append(f"Resource rows ignored because of invalid/duplicate label IDs: {len(blocked_rows)}")
    return approved, blocked_rows, warnings


def index_structures(structures: List[Dict[str, Any]]) -> Tuple[Dict[int, int], List[str]]:
    errors: List[str] = []
    by_id: Dict[int, int] = {}
    for idx, st in enumerate(structures):
        sid = parse_int(st.get("id"))
        if sid is None:
            errors.append(f"Structure at index {idx} has invalid id: {st.get('id')!r}")
            continue
        if sid in by_id:
            errors.append(f"Duplicate structure id in structures.json: {sid}")
            continue
        by_id[sid] = idx
    return by_id, errors


def proposed_values(sid: int, row: Dict[str, str]) -> Tuple[str, str]:
    if sid == ROOT_ID:
        return "root", "root"
    new_acronym = norm(row.get("proposed_acronym"))
    new_name = norm(row.get("proposed_name")) or norm(row.get("paxinos_name"))
    return new_acronym, new_name


def duplicate_acronyms_after_apply(structures: List[Dict[str, Any]]) -> Dict[str, List[int]]:
    acr_to_ids: Dict[str, List[int]] = defaultdict(list)
    for st in structures:
        acr = norm(st.get("acronym"))
        sid = parse_int(st.get("id"))
        if acr and sid is not None:
            acr_to_ids[acr].append(sid)
    return {acr: ids for acr, ids in acr_to_ids.items() if len(ids) > 1}


def check_only_name_acronym_changed(before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    if len(before) != len(after):
        return ["Structure count changed."]
    for idx, (old, new) in enumerate(zip(before, after)):
        if old.get("id") != new.get("id"):
            errors.append(f"Structure order/id changed at index {idx}: {old.get('id')} -> {new.get('id')}")
            continue
        for key in set(old) | set(new):
            if key in {"name", "acronym"}:
                continue
            if old.get(key) != new.get(key):
                errors.append(f"Non-name/acronym field changed for id {old.get('id')}: {key}")
                if len(errors) >= 20:
                    return errors
    return errors


def plan_for_structures(target_label: str, atlas_dir: Path, structures: List[Dict[str, Any]], approved: Dict[int, Dict[str, str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    by_id, index_errors = index_structures(structures)
    errors.extend(index_errors)
    if errors:
        return structures, [], warnings, errors

    output = json.loads(json.dumps(structures))
    changes: List[Dict[str, Any]] = []

    for sid in sorted(approved):
        row = approved[sid]
        if sid not in by_id:
            # Root and approved rows missing from a target are a real mismatch, not a cute little surprise.
            errors.append(f"Applicable label_id {sid} is missing from {atlas_dir / 'structures.json'}")
            continue
        new_acronym, new_name = proposed_values(sid, row)
        if not new_acronym or not new_name:
            errors.append(f"Applicable row has empty proposed acronym/name for label_id {sid}")
            continue
        idx = by_id[sid]
        st = output[idx]
        old_acronym = norm(st.get("acronym"))
        old_name = norm(st.get("name"))
        if old_acronym == new_acronym and old_name == new_name:
            continue
        st["acronym"] = new_acronym
        st["name"] = new_name
        changes.append({
            "target": target_label,
            "atlas_dir": str(atlas_dir),
            "label_id": sid,
            "old_acronym": old_acronym,
            "old_name": old_name,
            "new_acronym": new_acronym,
            "new_name": new_name,
            "review_status": norm(row.get("review_status")),
            "acronym_basis": row.get("acronym_basis", ""),
            "basis_detail": row.get("basis_detail", ""),
            "confidence": row.get("confidence", ""),
        })

    duplicate_final = duplicate_acronyms_after_apply(output)
    if duplicate_final:
        preview = "; ".join(f"{acr}: {ids}" for acr, ids in list(duplicate_final.items())[:30])
        errors.append(f"Applying approved/display_only rows would leave/create duplicate final acronyms ({len(duplicate_final)}): {preview}")

    preservation_errors = check_only_name_acronym_changed(structures, output)
    errors.extend(preservation_errors)
    return output, changes, warnings, errors


def check_id_snapshot(structures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id, _errors = index_structures(structures)
    rows: List[Dict[str, Any]] = []
    for sid in CHECK_IDS:
        idx = by_id.get(sid)
        if idx is None:
            rows.append({"label_id": sid, "present": False, "acronym": None, "name": None})
        else:
            st = structures[idx]
            rows.append({"label_id": sid, "present": True, "acronym": st.get("acronym"), "name": st.get("name")})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply curated Paxinos/Watson label acronyms to generated and installed BrainGlobe structures.json files.")
    parser.add_argument("--root", default=".", help="Project root. Default: current working directory.")
    parser.add_argument("--resource", default=None, help="Curated acronym CSV. Default: resources/label_curation/Paxinos_Watson_Labels_Acronyms_with_basis.csv")
    parser.add_argument("--atlas-dir", default=None, help="Explicit atlas directory containing structures.json. Mostly for diagnostics.")
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this, dry-run only.")
    parser.add_argument("--require-installed", action="store_true", help="Fail if the installed ~/.brainglobe atlas structures.json cannot be patched.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a timestamped backup next to structures.json before writing.")
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    resource = Path(args.resource).expanduser().resolve() if args.resource else root / RESOURCE_REL
    report_dir = root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {
        "version": "apply_curated_acronym_resource.py",
        "generated_at": iso_now(),
        "project_root": str(root),
        "resource_csv": str(resource),
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "require_installed": bool(args.require_installed),
        "safety": {
            "modifies_only_structures_json_fields": ["name", "acronym"],
            "does_modify_annotation_volumes": False,
            "does_modify_reference_volumes": False,
            "does_modify_hemisphere_volumes": False,
            "does_modify_label_ids": False,
            "root_id_997_locked": True,
        },
        "candidate_targets": [],
        "results": [],
        "warnings": [],
        "errors": [],
        "passed": False,
    }

    errors: List[str] = []
    warnings: List[str] = []
    all_changes: List[Dict[str, Any]] = []
    blocked_resource_rows: List[Dict[str, Any]] = []

    if not resource.exists():
        errors.append(f"Missing curated acronym resource CSV: {resource}")
    targets, checked, target_errors = select_targets(root, args.atlas_dir, args.require_installed)
    report["candidate_targets"] = checked
    errors.extend(target_errors)

    approved: Dict[int, Dict[str, str]] = {}
    if not errors:
        try:
            rows = read_csv_dicts(resource)
            approved, blocked_resource_rows, resource_warnings = build_approved_map(rows)
            warnings.extend(resource_warnings)
            report["resource_rows"] = len(rows)
            report["applicable_rows_including_root_lock"] = len(approved)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Could not read/parse resource CSV: {exc}")

    if not errors:
        for target_label, atlas_dir, is_installed in targets:
            structures_path = atlas_dir / "structures.json"
            result: Dict[str, Any] = {
                "target": target_label,
                "atlas_dir": str(atlas_dir),
                "is_installed_target": is_installed,
                "structures_json": str(structures_path),
                "written": False,
                "backup": None,
                "planned_changes": 0,
                "errors": [],
                "warnings": [],
                "check_ids_after": [],
            }
            try:
                before = read_json_list(structures_path)
                after, changes, local_warnings, local_errors = plan_for_structures(target_label, atlas_dir, before, approved)
                result["planned_changes"] = len(changes)
                result["warnings"] = local_warnings
                result["errors"] = local_errors
                result["check_ids_after"] = check_id_snapshot(after)
                warnings.extend([f"{target_label}: {w}" for w in local_warnings])
                if local_errors:
                    errors.extend([f"{target_label}: {e}" for e in local_errors])
                elif args.apply and changes:
                    if not args.no_backup:
                        backup = structures_path.with_name(f"structures.json.before_curated_acronyms_{stamp()}.bak")
                        shutil.copy2(structures_path, backup)
                        result["backup"] = str(backup)
                    write_json_list(structures_path, after)
                    result["written"] = True
                all_changes.extend(changes)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                result["errors"].append(msg)
                errors.append(f"{target_label}: {msg}")
            report["results"].append(result)

    changes_csv = report_dir / "applied_or_planned_curated_acronym_changes.csv"
    write_csv(changes_csv, all_changes, [
        "target", "atlas_dir", "label_id", "old_acronym", "old_name", "new_acronym", "new_name",
        "review_status", "acronym_basis", "basis_detail", "confidence",
    ])
    if blocked_resource_rows:
        write_csv(report_dir / "blocked_resource_rows.csv", blocked_resource_rows, ["line", "label_id", "reason", "first_seen_line"])

    report["total_planned_changes"] = len(all_changes)
    report["warnings"] = warnings
    report["errors"] = errors
    report["passed"] = not errors
    report_path = report_dir / "apply_curated_acronym_resource_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary_lines = [
        "Apply curated Paxinos/Watson acronym resource",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Mode: {report['mode']}",
        f"Project root: {root}",
        f"Resource CSV: {resource}",
        f"Targets patched/checked: {len(targets)}",
        f"Total planned changes: {len(all_changes)}",
        f"PASSED: {report['passed']}",
        "",
        "Safety:",
        "- only structures.json name/acronym fields are changed",
        "- annotation/reference/hemisphere volumes are not touched",
        "- label IDs and hierarchy fields are not touched",
        "- label_id 997 is hard-locked to root/root",
        "",
        "Check IDs after planned/apply:",
    ]
    for res in report["results"]:
        summary_lines.append(f"[{res['target']}] {res['structures_json']}")
        for row in res.get("check_ids_after", []):
            summary_lines.append(f"  {row['label_id']}: {row.get('acronym')} | {row.get('name')}")
    summary_lines += ["", "Errors:"]
    summary_lines.extend([f"- {e}" for e in errors] or ["- none"])
    summary_lines += ["", "Warnings:"]
    summary_lines.extend([f"- {w}" for w in warnings] or ["- none"])
    (report_dir / "apply_curated_acronym_resource_summary.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    print("Apply curated Paxinos/Watson acronym resource")
    print("=" * 72)
    print(f"Mode: {report['mode']}")
    print(f"Targets checked: {len(targets)}")
    print(f"Total planned changes: {len(all_changes)}")
    print(f"PASSED: {report['passed']}")
    print(f"Report: {report_path}")
    if errors:
        print("Errors:")
        for e in errors:
            print(f"- {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
