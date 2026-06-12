"""
V32.3 ontology/acronym audit for the local Paxinos-Watson rat atlas.

This script is read-only. It checks the current official candidate for:
- annotation IDs present in voxels but missing from structures.json
- structures.json IDs that do not occur in annotation.tiff
- root/path/parent consistency
- duplicated acronyms/names
- auto-generated or suspicious acronyms
- unresolved placeholder labels
- likely raw-label availability under data/raw

It writes CSV/TXT/JSON reports to:
    reports/v32_3_ontology_audit

It does not modify atlas files. The output is intentionally conservative:
flags are review tasks, not automatic corrections. Because apparently letting code
invent neuroanatomical ontology is how one summons ancient demons.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import tifffile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATLAS_NAME = "paxinos_watson_rat_40um"
OFFICIAL_CANDIDATE = PROJECT_ROOT / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME
REPORT_DIR = PROJECT_ROOT / "reports" / "v32_3_ontology_audit"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
ROOT_ID = 997
CONTAINER_ID_MIN = 998000

SUSPICIOUS_NAME_PATTERNS = [
    re.compile(r"unresolved", re.I),
    re.compile(r"placeholder", re.I),
    re.compile(r"review needed", re.I),
    re.compile(r"small_or_local", re.I),
    re.compile(r"between_", re.I),
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    ensure_dir(path.parent)
    if fieldnames is None:
        keys = []
        seen = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    keys.append(key)
                    seen.add(key)
        fieldnames = keys or ["empty"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def acronym_base(acronym: str) -> str:
    return re.sub(r"_\d+$", "", acronym or "")


def is_container(s: Dict[str, Any]) -> bool:
    sid = int(s.get("id", -1))
    return sid == ROOT_ID or sid >= CONTAINER_ID_MIN


def load_structures() -> List[Dict[str, Any]]:
    path = OFFICIAL_CANDIDATE / "structures.json"
    if not path.exists():
        raise FileNotFoundError(f"structures.json not found: {path}")
    data = read_json(path)
    if not isinstance(data, list):
        raise TypeError("structures.json must contain a list")
    return data


def load_annotation_ids() -> Tuple[List[int], Dict[str, Any]]:
    path = OFFICIAL_CANDIDATE / "annotation.tiff"
    if not path.exists():
        raise FileNotFoundError(f"annotation.tiff not found: {path}")
    arr = tifffile.imread(path)
    unique, counts = np.unique(arr, return_counts=True)
    ids = [int(x) for x in unique.tolist()]
    stats = {
        "path": str(path),
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "unique_count": len(ids),
        "min": int(np.min(unique)),
        "max": int(np.max(unique)),
        "nonzero_fraction": float(np.count_nonzero(arr) / arr.size),
        "voxel_counts": {str(int(i)): int(c) for i, c in zip(unique.tolist(), counts.tolist())},
    }
    return ids, stats


def find_raw_label_files() -> List[Path]:
    patterns = [
        "*Paxinos*Labels*.txt",
        "*paxinos*labels*.txt",
        "*Labels_Cortex*.txt",
        "*labels*cortex*.txt",
    ]
    out: List[Path] = []
    if not RAW_DIR.exists():
        return out
    for pattern in patterns:
        for p in sorted(RAW_DIR.rglob(pattern)):
            if p.is_file() and p not in out:
                out.append(p)
    return out


def inspect_raw_label_files(paths: List[Path]) -> List[Dict[str, Any]]:
    rows = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = [line for line in text.splitlines() if line.strip()]
            id_like = 0
            for line in lines:
                if re.search(r"(^|\D)\d{1,6}(\D|$)", line):
                    id_like += 1
            rows.append({
                "path": str(p),
                "line_count": len(lines),
                "id_like_line_count": id_like,
                "first_nonempty_lines": " | ".join(lines[:5])[:1000],
            })
        except Exception as exc:
            rows.append({"path": str(p), "error": repr(exc)})
    return rows


def main() -> int:
    ensure_dir(REPORT_DIR)
    structures = load_structures()
    annotation_ids, annotation_stats = load_annotation_ids()
    annotation_set = set(annotation_ids)
    structure_by_id: Dict[int, Dict[str, Any]] = {int(s.get("id")): s for s in structures}
    structure_ids = set(structure_by_id.keys())

    ids_missing_structures = sorted([i for i in annotation_set if i != 0 and i not in structure_ids])
    structures_without_voxels = []
    for sid, s in sorted(structure_by_id.items()):
        if sid == 0:
            continue
        if sid not in annotation_set and not is_container(s):
            structures_without_voxels.append({
                "id": sid,
                "acronym": s.get("acronym"),
                "name": s.get("name"),
                "parent_structure_id": s.get("parent_structure_id"),
                "structure_id_path": ">".join(map(str, s.get("structure_id_path") or [])),
                "reason": "structure exists but ID does not occur in annotation.tiff",
            })

    # Parent/path/root integrity.
    integrity_rows = []
    for sid, s in sorted(structure_by_id.items()):
        path = s.get("structure_id_path") or []
        parent = s.get("parent_structure_id")
        problems = []
        if sid != ROOT_ID and (not path or path[0] != ROOT_ID):
            problems.append("path_does_not_start_with_root_997")
        if not path or path[-1] != sid:
            problems.append("path_last_id_is_not_structure_id")
        if sid == ROOT_ID:
            if parent is not None:
                problems.append("root_parent_should_be_null")
        else:
            if parent not in structure_by_id:
                problems.append("parent_missing")
            elif len(path) >= 2 and path[-2] != parent:
                problems.append("path_parent_does_not_match_parent_structure_id")
        st_level = s.get("st_level")
        if isinstance(st_level, int) and path and st_level != len(path):
            problems.append("st_level_does_not_match_path_length")
        if problems:
            integrity_rows.append({
                "id": sid,
                "acronym": s.get("acronym"),
                "name": s.get("name"),
                "parent_structure_id": parent,
                "structure_id_path": ">".join(map(str, path)),
                "problems": ";".join(problems),
            })

    # Duplicate names/acronyms.
    acronym_counts = Counter(str(s.get("acronym", "")) for s in structures)
    base_counts = Counter(acronym_base(str(s.get("acronym", ""))) for s in structures)
    name_counts = Counter(str(s.get("name", "")) for s in structures)

    duplicate_acronyms = []
    for s in structures:
        acr = str(s.get("acronym", ""))
        base = acronym_base(acr)
        if acronym_counts[acr] > 1 or (base and base_counts[base] > 1):
            duplicate_acronyms.append({
                "id": s.get("id"),
                "acronym": acr,
                "acronym_base": base,
                "full_acronym_count": acronym_counts[acr],
                "base_acronym_count": base_counts[base],
                "name": s.get("name"),
                "review_note": "duplicate exact acronym or duplicate base acronym after removing _ID suffix",
            })

    duplicate_names = []
    for s in structures:
        name = str(s.get("name", ""))
        if name_counts[name] > 1:
            duplicate_names.append({
                "id": s.get("id"),
                "acronym": s.get("acronym"),
                "name": name,
                "name_count": name_counts[name],
            })

    # Suspicious autogenerated/placeholder flags.
    suspicious_rows = []
    for s in structures:
        sid = int(s.get("id"))
        acr = str(s.get("acronym", ""))
        name = str(s.get("name", ""))
        reasons = []
        if re.search(r"_\d+$", acr):
            reasons.append("acronym_has_auto_id_suffix")
        if len(acronym_base(acr)) > 10:
            reasons.append("acronym_base_very_long")
        if acronym_base(acr).startswith("USOL"):
            reasons.append("unresolved_small_or_local_acronym")
        for pattern in SUSPICIOUS_NAME_PATTERNS:
            if pattern.search(name):
                reasons.append(f"name_matches_{pattern.pattern}")
        if reasons:
            suspicious_rows.append({
                "id": sid,
                "acronym": acr,
                "acronym_base": acronym_base(acr),
                "name": name,
                "parent_structure_id": s.get("parent_structure_id"),
                "structure_id_path": ">".join(map(str, s.get("structure_id_path") or [])),
                "reasons": ";".join(sorted(set(reasons))),
                "has_voxels": sid in annotation_set,
            })

    raw_label_paths = find_raw_label_files()
    raw_label_rows = inspect_raw_label_files(raw_label_paths)

    manual_review_rows = []
    for row in suspicious_rows[:]:
        manual_review_rows.append({
            "id": row["id"],
            "current_acronym": row["acronym"],
            "current_name": row["name"],
            "current_parent_structure_id": row["parent_structure_id"],
            "flag_reason": row["reasons"],
            "proposed_acronym": "",
            "proposed_name": "",
            "proposed_parent_structure_id": "",
            "decision": "review",
            "notes": "",
        })

    write_csv(REPORT_DIR / "annotation_ids_missing_structures.csv", [
        {"annotation_id": i, "problem": "voxel ID occurs in annotation.tiff but not in structures.json"}
        for i in ids_missing_structures
    ])
    write_csv(REPORT_DIR / "structures_without_voxels.csv", structures_without_voxels)
    write_csv(REPORT_DIR / "parent_path_integrity_flags.csv", integrity_rows)
    write_csv(REPORT_DIR / "duplicate_acronym_flags.csv", duplicate_acronyms)
    write_csv(REPORT_DIR / "duplicate_name_flags.csv", duplicate_names)
    write_csv(REPORT_DIR / "suspicious_autogenerated_label_flags.csv", suspicious_rows)
    write_csv(REPORT_DIR / "raw_label_file_inventory.csv", raw_label_rows)
    write_csv(REPORT_DIR / "manual_ontology_review_template.csv", manual_review_rows)

    summary = {
        "generated_at": now(),
        "candidate": str(OFFICIAL_CANDIDATE),
        "structures_count": len(structures),
        "annotation_unique_count": annotation_stats["unique_count"],
        "annotation_shape": annotation_stats["shape"],
        "annotation_nonzero_fraction": annotation_stats["nonzero_fraction"],
        "root_count": sum(1 for s in structures if int(s.get("id")) == ROOT_ID),
        "ids_missing_structures_count": len(ids_missing_structures),
        "structures_without_voxels_count": len(structures_without_voxels),
        "parent_path_integrity_flag_count": len(integrity_rows),
        "duplicate_acronym_flag_count": len(duplicate_acronyms),
        "duplicate_name_flag_count": len(duplicate_names),
        "suspicious_autogenerated_label_flag_count": len(suspicious_rows),
        "raw_label_file_count": len(raw_label_paths),
        "passed_machine_integrity": len(ids_missing_structures) == 0 and len(integrity_rows) == 0 and sum(1 for s in structures if int(s.get("id")) == ROOT_ID) == 1,
        "note": "Acronym/name flags are review queues, not automatic failures.",
    }
    (REPORT_DIR / "v32_3_ontology_audit_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    txt_lines = [
        "V32.3 Paxinos ontology/acronym audit summary",
        "========================================================================",
        f"Generated: {summary['generated_at']}",
        f"Candidate: {summary['candidate']}",
        f"Machine integrity passed: {summary['passed_machine_integrity']}",
        "",
        f"Structures count: {summary['structures_count']}",
        f"Annotation unique count: {summary['annotation_unique_count']}",
        f"Annotation shape: {summary['annotation_shape']}",
        f"Root count: {summary['root_count']}",
        "",
        "Critical integrity counts:",
        f"- annotation IDs missing structures: {summary['ids_missing_structures_count']}",
        f"- parent/path integrity flags: {summary['parent_path_integrity_flag_count']}",
        f"- structures without voxels: {summary['structures_without_voxels_count']}",
        "",
        "Manual review queues:",
        f"- duplicate acronym/base-acronym flags: {summary['duplicate_acronym_flag_count']}",
        f"- duplicate name flags: {summary['duplicate_name_flag_count']}",
        f"- suspicious autogenerated/placeholder flags: {summary['suspicious_autogenerated_label_flag_count']}",
        f"- raw label files found: {summary['raw_label_file_count']}",
        "",
        "Main files:",
        "- annotation_ids_missing_structures.csv",
        "- structures_without_voxels.csv",
        "- parent_path_integrity_flags.csv",
        "- duplicate_acronym_flags.csv",
        "- duplicate_name_flags.csv",
        "- suspicious_autogenerated_label_flags.csv",
        "- manual_ontology_review_template.csv",
        "",
        "Interpretation:",
        "- Machine integrity should be zero-critical before any serious ABBA use.",
        "- Acronym/name flags need human review against Paxinos labels, not automatic correction.",
        "- The manual template is the place for curated overrides in a later V32.4/V33 step.",
    ]
    (REPORT_DIR / "v32_3_ontology_audit_summary.txt").write_text("\n".join(txt_lines), encoding="utf-8")
    print("Ontology audit written to:", REPORT_DIR)
    print("Machine integrity passed:", summary["passed_machine_integrity"])
    return 0 if summary["passed_machine_integrity"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
