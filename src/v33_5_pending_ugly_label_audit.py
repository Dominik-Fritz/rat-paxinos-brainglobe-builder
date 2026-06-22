#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V33.5 Pending / Ugly Label Audit
================================

Read-only audit for the rat Paxinos BrainGlobe atlas label/ontology cleanup.

This script DOES NOT modify:
- annotation.nii.gz
- annotation.tiff
- structures.json
- metadata.json
- resources/label_curation/*.csv

It inspects the currently installed atlas and the curation resource, then writes
prioritized CSV reports for remaining ugly/pending/problematic labels.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import datetime as _dt
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "V33.5 Pending / Ugly Label Audit"
ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
REPORT_REL = Path("reports") / "v33_5_pending_ugly_label_audit"

IMPORTANT_KEYWORDS = [
    # user/project critical or commonly used neuroscience targets
    "amyg", "basolateral", "central amyg", "lateral amyg", "bed nucleus",
    "sept", "septal", "septofimbrial", "medial sept", "lateral sept",
    "hippoc", "dentate", "ca1", "ca2", "ca3", "subiculum",
    "accumbens", "acb", "striat", "caudate", "putamen",
    "prelimbic", "infralimbic", "cingulate", "insular", "cortex",
    "hypothalam", "paraventricular", "perifornical", "lateral hypothalam",
    "thalam", "habenula", "geniculate",
    "periaqueductal", "pag", "substantia nigra", "ventral tegmental", "vta",
    "raphe", "locus coeruleus", "periaqueductal gray",
]

GENERIC_ACRONYM_RISK = {
    "SN",  # could be septofimbrial nucleus, substantia nigra, etc.
    "PN", "PH", "LA", "LP", "LH", "I", "IC", "IMD", "Sub", "CC", "CB", "Sep",
    "P", "A", "M", "L", "V", "D",
}

PLACEHOLDER_PATTERNS = [
    "-------", "unknown", "unresolved", "placeholder", "generated", "not available",
    "not_available", "raw broad", "local", "between_", "_and_",
]

SUSPICIOUS_PARENT_CHILD_PATTERNS = [
    ("white_matter", ["nucleus", "cortex", "amyg", "sept", "hypothalam", "thalam"]),
    ("fiber", ["nucleus", "cortex", "amyg", "sept", "hypothalam", "thalam"]),
    ("root", []),
]

ID_SUFFIX_RE = re.compile(r"_[0-9]+$")


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def as_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x)


def norm(s: Any) -> str:
    return as_str(s).strip()


def norm_lower(s: Any) -> str:
    return norm(s).lower()


def parse_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    candidates = [start] + list(start.parents)
    for c in candidates:
        if (c / RESOURCE_REL).exists() or (c / "run_builder.bat").exists() or (c / "src").exists():
            return c
    return start


def find_atlas_candidates(project_root: Path) -> List[Dict[str, Any]]:
    home = Path.home()
    candidates = [
        project_root / "data" / "output" / "brainglobe_official_candidate" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_official_candidate" / f"{ATLAS_NAME}_v1.0",
        project_root / "data" / "output" / "brainglobe_provisional" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_provisional" / f"{ATLAS_NAME}_v1.0",
        project_root / "data" / "output" / "brainglobe_local_cache" / ATLAS_NAME,
        project_root / "data" / "output" / "brainglobe_local_cache" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / f"{ATLAS_NAME}_v1.0",
        home / ".brainglobe" / ATLAS_NAME,
    ]
    out: List[Dict[str, Any]] = []
    seen = set()
    for atlas_dir in candidates:
        p = atlas_dir.resolve()
        if p in seen:
            continue
        seen.add(p)
        structures_json = p / "structures.json"
        annotation_nii = p / "annotation.nii.gz"
        annotation_tiff = p / "annotation.tiff"
        metadata_json = p / "metadata.json"
        out.append({
            "atlas_dir": str(p),
            "exists": p.exists(),
            "structures_json": structures_json.exists(),
            "annotation_nii": annotation_nii.exists(),
            "annotation_tiff": annotation_tiff.exists(),
            "metadata_json": metadata_json.exists(),
            "usable": p.exists() and structures_json.exists(),
        })
    return out


def select_atlas_dir(candidates: List[Dict[str, Any]]) -> Optional[Path]:
    for c in candidates:
        if c.get("usable"):
            return Path(c["atlas_dir"])
    return None


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_structures(path: Path) -> List[Dict[str, Any]]:
    data = read_json(path)
    if isinstance(data, dict):
        # Some exports wrap structures in a key.
        for key in ("structures", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        # Otherwise values may be the structures.
        vals = [v for v in data.values() if isinstance(v, dict) and "id" in v]
        if vals:
            return vals
    if not isinstance(data, list):
        raise ValueError(f"Unsupported structures.json format: {path}")
    return data


def get_resource_id(row: Dict[str, str]) -> Optional[int]:
    for key in ("label_id", "id", "structure_id", "atlas_id"):
        if key in row:
            parsed = parse_int(row.get(key))
            if parsed is not None:
                return parsed
    return None


def get_first(row: Dict[str, str], keys: Iterable[str]) -> str:
    for k in keys:
        if k in row and norm(row[k]):
            return norm(row[k])
    return ""


def path_for_structure(s: Dict[str, Any], by_id: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    ids = s.get("structure_id_path")
    if not isinstance(ids, list) or not ids:
        # Fallback through parent ids.
        chain = []
        seen = set()
        current = s
        while current:
            sid = parse_int(current.get("id"))
            if sid is None or sid in seen:
                break
            seen.add(sid)
            chain.append(sid)
            parent_id = parse_int(current.get("parent_structure_id"))
            current = by_id.get(parent_id) if parent_id is not None else None
        ids = list(reversed(chain))
    names = []
    acronyms = []
    for sid in ids:
        sid_i = parse_int(sid)
        st = by_id.get(sid_i) if sid_i is not None else None
        if st:
            names.append(norm(st.get("name")) or str(sid_i))
            acronyms.append(norm(st.get("acronym")) or str(sid_i))
        else:
            names.append(str(sid))
            acronyms.append(str(sid))
    return " > ".join(names), " > ".join(acronyms)


def try_voxel_counts(annotation_nii: Path, annotation_tiff: Path) -> Tuple[Dict[int, int], Dict[str, Any]]:
    """Try to compute label voxel counts. This is optional and may be skipped."""
    info: Dict[str, Any] = {
        "attempted": False,
        "source": None,
        "available": False,
        "error": None,
    }
    counts: Dict[int, int] = {}
    try:
        import numpy as np  # type: ignore
    except Exception as e:
        info["error"] = f"numpy unavailable: {e}"
        return counts, info

    # Prefer NIfTI if nibabel is installed; it is usually memory-safe enough for this atlas.
    if annotation_nii.exists():
        info["attempted"] = True
        info["source"] = str(annotation_nii)
        try:
            import nibabel as nib  # type: ignore
            img = nib.load(str(annotation_nii))
            data = np.asanyarray(img.dataobj)
            values, cnts = np.unique(data, return_counts=True)
            for v, c in zip(values, cnts):
                vi = int(v)
                if vi != 0:
                    counts[vi] = int(c)
            info["available"] = True
            info["nonzero_label_count"] = len(counts)
            return counts, info
        except Exception as e:
            info["error"] = f"nibabel/nifti count failed: {e}"

    if annotation_tiff.exists():
        info["attempted"] = True
        info["source"] = str(annotation_tiff)
        try:
            import tifffile  # type: ignore
            data = tifffile.imread(str(annotation_tiff))
            values, cnts = np.unique(data, return_counts=True)
            for v, c in zip(values, cnts):
                vi = int(v)
                if vi != 0:
                    counts[vi] = int(c)
            info["available"] = True
            info["nonzero_label_count"] = len(counts)
            return counts, info
        except Exception as e:
            info["error"] = f"tifffile count failed: {e}"
    return counts, info


def contains_any(text: str, needles: Iterable[str]) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


def is_placeholder_name(name: str) -> bool:
    t = name.lower()
    return any(p in t for p in PLACEHOLDER_PATTERNS)


def is_numeric_suffix(acronym: str) -> bool:
    return bool(ID_SUFFIX_RE.search(acronym.strip()))


def suspicious_parent_path(current_name: str, parent_path_names: str, parent_path_acronyms: str) -> bool:
    p = f"{parent_path_names} {parent_path_acronyms}".lower()
    child = current_name.lower()
    for parent_marker, child_markers in SUSPICIOUS_PARENT_CHILD_PATTERNS:
        if parent_marker in p and child_markers:
            if any(cm in child for cm in child_markers):
                return True
    return False


def classify_priority(flags: List[str], status: str, current_name: str, proposed_name: str, voxel_count: Optional[int]) -> str:
    text = f"{current_name} {proposed_name}".lower()
    important = contains_any(text, IMPORTANT_KEYWORDS)
    if important and any(f in flags for f in ["current_acronym_numeric_suffix", "placeholder_name", "generic_acronym_risk", "name_mismatch_pending", "suspicious_parent_path"]):
        return "P0_project_critical_or_visible"
    if any(f in flags for f in ["current_acronym_numeric_suffix", "placeholder_name", "proposed_acronym_numeric_suffix"]):
        return "P1_ugly_label_visible_cleanup"
    if any(f in flags for f in ["suspicious_parent_path", "generic_acronym_risk", "name_mismatch_pending"]):
        return "P2_semantic_review"
    if status in {"pending_review", "pending", "review"}:
        return "P3_pending_resource_review"
    if status in {"do_not_apply", "rejected", "defer"}:
        return "P4_do_not_apply_documented"
    return "P5_background"


def audit(project_root: Path) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    resource_csv = project_root / RESOURCE_REL
    report_dir = project_root / REPORT_REL
    report_dir.mkdir(parents=True, exist_ok=True)

    if not resource_csv.exists():
        errors.append(f"Missing resource CSV: {resource_csv}")

    candidates = find_atlas_candidates(project_root)
    selected_atlas_dir = select_atlas_dir(candidates)
    if not selected_atlas_dir:
        errors.append("No usable atlas dir found with structures.json.")

    if errors:
        report = {
            "version": VERSION,
            "generated_at": now_iso(),
            "project_root": str(project_root),
            "does_modify_annotation_volumes": False,
            "does_modify_structures_json": False,
            "errors": errors,
            "warnings": warnings,
            "candidate_atlas_dirs": candidates,
            "passed": False,
        }
        write_json(report_dir / "v33_5_pending_ugly_label_audit_report.json", report)
        write_summary(report_dir / "v33_5_pending_ugly_label_audit_summary.txt", report)
        return report

    assert selected_atlas_dir is not None
    structures_path = selected_atlas_dir / "structures.json"
    structures = load_structures(structures_path)
    by_id: Dict[int, Dict[str, Any]] = {}
    for s in structures:
        sid = parse_int(s.get("id"))
        if sid is not None:
            by_id[sid] = s

    resource_rows = read_csv_dicts(resource_csv)
    resource_by_id: Dict[int, Dict[str, str]] = {}
    duplicate_resource_ids: List[int] = []
    for row in resource_rows:
        rid = get_resource_id(row)
        if rid is None:
            continue
        if rid in resource_by_id:
            duplicate_resource_ids.append(rid)
        resource_by_id[rid] = row

    voxel_counts, voxel_info = try_voxel_counts(selected_atlas_dir / "annotation.nii.gz", selected_atlas_dir / "annotation.tiff")
    if not voxel_info.get("available"):
        warnings.append("Voxel counts unavailable; priority still computed from label/resource/path flags.")

    queue_rows: List[Dict[str, Any]] = []
    visible_problem_rows: List[Dict[str, Any]] = []
    name_quality_rows: List[Dict[str, Any]] = []
    parent_suspicion_rows: List[Dict[str, Any]] = []
    manual_template_rows: List[Dict[str, Any]] = []

    status_counter: Counter[str] = Counter()
    priority_counter: Counter[str] = Counter()
    flag_counter: Counter[str] = Counter()
    approved_open_change_count = 0

    for sid, s in sorted(by_id.items(), key=lambda kv: kv[0]):
        r = resource_by_id.get(sid, {})
        current_acronym = norm(s.get("acronym"))
        current_name = norm(s.get("name"))
        current_parent_id = parse_int(s.get("parent_structure_id"))
        parent_path_names, parent_path_acronyms = path_for_structure(s, by_id)
        proposed_acronym = get_first(r, ["proposed_acronym", "suggested_acronym", "acronym", "final_acronym"])
        proposed_name = get_first(r, ["proposed_name", "suggested_name", "name", "final_name", "paxinos_name"])
        status = norm_lower(get_first(r, ["review_status", "status", "curation_status"])) or "missing_resource"
        basis = get_first(r, ["acronym_basis", "basis", "source_basis"])
        basis_detail = get_first(r, ["basis_detail", "note", "notes", "manual_note"])
        confidence = norm_lower(get_first(r, ["confidence", "confidence_level"]))
        voxel_count = voxel_counts.get(sid)

        status_counter[status] += 1
        flags: List[str] = []
        if not r:
            flags.append("missing_resource_row")
        if is_numeric_suffix(current_acronym):
            flags.append("current_acronym_numeric_suffix")
        if proposed_acronym and is_numeric_suffix(proposed_acronym):
            flags.append("proposed_acronym_numeric_suffix")
        if is_placeholder_name(current_name) or (proposed_name and is_placeholder_name(proposed_name)):
            flags.append("placeholder_name")
        if current_acronym in GENERIC_ACRONYM_RISK or proposed_acronym in GENERIC_ACRONYM_RISK:
            flags.append("generic_acronym_risk")
        if status in {"pending_review", "pending", "review"} and proposed_name and proposed_name.lower() != current_name.lower():
            flags.append("name_mismatch_pending")
        if status in {"approved"} and ((proposed_acronym and proposed_acronym != current_acronym) or (proposed_name and proposed_name.lower() != current_name.lower())):
            flags.append("approved_still_not_applied")
            approved_open_change_count += 1
        if "rule_generated" in basis.lower() or "generated" in basis.lower():
            flags.append("rule_generated_basis")
        if "placeholder" in basis.lower():
            flags.append("placeholder_basis")
        if "builder_generated" in basis.lower():
            flags.append("builder_internal_basis")
        if "review" in basis_detail.lower() or "conflict" in basis_detail.lower() or "overlap" in basis_detail.lower():
            flags.append("basis_detail_review_conflict_overlap")
        if suspicious_parent_path(current_name, parent_path_names, parent_path_acronyms):
            flags.append("suspicious_parent_path")
        if contains_any(f"{current_name} {proposed_name} {current_acronym} {proposed_acronym}", IMPORTANT_KEYWORDS):
            flags.append("important_region_keyword")

        priority = classify_priority(flags, status, current_name, proposed_name, voxel_count)
        priority_counter[priority] += 1
        for flag in flags:
            flag_counter[flag] += 1

        needs_review = priority != "P5_background" or status != "approved"
        row = {
            "priority": priority,
            "label_id": sid,
            "voxel_count": voxel_count if voxel_count is not None else "",
            "current_acronym": current_acronym,
            "current_name": current_name,
            "current_parent_id": current_parent_id if current_parent_id is not None else "",
            "parent_path_acronyms": parent_path_acronyms,
            "parent_path_names": parent_path_names,
            "resource_review_status": status,
            "proposed_acronym": proposed_acronym,
            "proposed_name": proposed_name,
            "confidence": confidence,
            "acronym_basis": basis,
            "basis_detail": basis_detail,
            "flags": ";".join(flags),
            "recommended_action": recommend_action(priority, flags, status),
            "manual_decision": "",
            "manual_final_acronym": "",
            "manual_final_name": "",
            "manual_note": "",
        }

        if needs_review:
            queue_rows.append(row)
        if priority in {"P0_project_critical_or_visible", "P1_ugly_label_visible_cleanup"}:
            visible_problem_rows.append(row)
        if any(f in flags for f in ["placeholder_name", "name_mismatch_pending", "approved_still_not_applied"]):
            name_quality_rows.append(row)
        if "suspicious_parent_path" in flags:
            parent_suspicion_rows.append(row)

    # Sort with priority and voxel count desc where available.
    def sort_key(r: Dict[str, Any]) -> Tuple[str, int, int]:
        vc = r.get("voxel_count")
        try:
            vc_i = int(vc)
        except Exception:
            vc_i = -1
        return (str(r.get("priority", "")), -vc_i, int(r.get("label_id", 0)))

    queue_rows.sort(key=sort_key)
    visible_problem_rows.sort(key=sort_key)
    name_quality_rows.sort(key=sort_key)
    parent_suspicion_rows.sort(key=sort_key)

    # Manual review template: start with P0/P1 plus important P2 rows. Keep it manageable.
    manual_template_rows = [r.copy() for r in queue_rows if str(r.get("priority", "")).startswith(("P0", "P1"))]
    if len(manual_template_rows) < 100:
        for r in queue_rows:
            if r in manual_template_rows:
                continue
            if "important_region_keyword" in str(r.get("flags", "")) or str(r.get("priority", "")).startswith("P2"):
                manual_template_rows.append(r.copy())
            if len(manual_template_rows) >= 100:
                break

    fields = [
        "priority", "label_id", "voxel_count", "current_acronym", "current_name",
        "current_parent_id", "parent_path_acronyms", "parent_path_names",
        "resource_review_status", "proposed_acronym", "proposed_name", "confidence",
        "acronym_basis", "basis_detail", "flags", "recommended_action",
        "manual_decision", "manual_final_acronym", "manual_final_name", "manual_note",
    ]
    write_csv(report_dir / "v33_5_pending_ugly_label_queue.csv", queue_rows, fields)
    write_csv(report_dir / "v33_5_visible_problem_candidates.csv", visible_problem_rows, fields)
    write_csv(report_dir / "v33_5_name_quality_flags.csv", name_quality_rows, fields)
    write_csv(report_dir / "v33_5_parent_path_suspicion_flags.csv", parent_suspicion_rows, fields)
    write_csv(report_dir / "v33_5_manual_review_template.csv", manual_template_rows, fields)

    status_rows = [{"status": k, "count": v} for k, v in sorted(status_counter.items())]
    priority_rows = [{"priority": k, "count": v} for k, v in sorted(priority_counter.items())]
    flag_rows = [{"flag": k, "count": v} for k, v in flag_counter.most_common()]
    write_csv(report_dir / "v33_5_status_distribution.csv", status_rows, ["status", "count"])
    write_csv(report_dir / "v33_5_priority_distribution.csv", priority_rows, ["priority", "count"])
    write_csv(report_dir / "v33_5_flag_distribution.csv", flag_rows, ["flag", "count"])

    # Directly highlight the SN_739 style example if present.
    sn739 = next((r for r in queue_rows if int(r.get("label_id", -1)) == 739), None)

    report = {
        "version": VERSION,
        "generated_at": now_iso(),
        "project_root": str(project_root),
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "resource_csv": str(resource_csv),
        "candidate_atlas_dirs": candidates,
        "selected_atlas_dir": str(selected_atlas_dir),
        "structures_json": str(structures_path),
        "errors": errors,
        "warnings": warnings,
        "voxel_count_info": voxel_info,
        "counts": {
            "structure_count": len(structures),
            "resource_rows": len(resource_rows),
            "resource_rows_matched_to_structures": len(set(resource_by_id.keys()) & set(by_id.keys())),
            "duplicate_resource_ids_count": len(duplicate_resource_ids),
            "queue_rows": len(queue_rows),
            "visible_problem_candidates": len(visible_problem_rows),
            "name_quality_flags": len(name_quality_rows),
            "parent_path_suspicion_flags": len(parent_suspicion_rows),
            "manual_review_template_rows": len(manual_template_rows),
            "approved_open_change_count": approved_open_change_count,
        },
        "status_distribution": dict(status_counter),
        "priority_distribution": dict(priority_counter),
        "flag_distribution": dict(flag_counter),
        "sn_739_preview": sn739,
        "outputs": {
            "queue": str(report_dir / "v33_5_pending_ugly_label_queue.csv"),
            "visible_problem_candidates": str(report_dir / "v33_5_visible_problem_candidates.csv"),
            "manual_review_template": str(report_dir / "v33_5_manual_review_template.csv"),
            "name_quality_flags": str(report_dir / "v33_5_name_quality_flags.csv"),
            "parent_path_suspicion_flags": str(report_dir / "v33_5_parent_path_suspicion_flags.csv"),
            "status_distribution": str(report_dir / "v33_5_status_distribution.csv"),
            "priority_distribution": str(report_dir / "v33_5_priority_distribution.csv"),
            "flag_distribution": str(report_dir / "v33_5_flag_distribution.csv"),
        },
        "passed": len(errors) == 0,
    }
    write_json(report_dir / "v33_5_pending_ugly_label_audit_report.json", report)
    write_summary(report_dir / "v33_5_pending_ugly_label_audit_summary.txt", report)
    return report


def recommend_action(priority: str, flags: List[str], status: str) -> str:
    if "approved_still_not_applied" in flags:
        return "re-run V33.4B apply/validator before manual review"
    if priority == "P0_project_critical_or_visible":
        return "manual review first; approve only if acronym/name basis is confirmed"
    if priority == "P1_ugly_label_visible_cleanup":
        return "manual batch candidate; replace ugly generated acronym only after confirmation"
    if priority == "P2_semantic_review":
        return "defer until family-specific review"
    if status == "do_not_apply":
        return "keep blocked unless manually justified"
    if status.startswith("pending"):
        return "keep pending for later family batch"
    return "no immediate action"


def write_summary(path: Path, report: Dict[str, Any]) -> None:
    lines = []
    lines.append(VERSION)
    lines.append("=" * 72)
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"Project root: {report.get('project_root')}")
    lines.append(f"Selected atlas dir: {report.get('selected_atlas_dir', 'n/a')}")
    lines.append(f"PASSED: {report.get('passed')}")
    lines.append("")
    lines.append("This is a read-only audit. It does not modify annotation volumes or structures.json.")
    lines.append("")
    if report.get("errors"):
        lines.append("Errors:")
        for e in report["errors"]:
            lines.append(f"- {e}")
        lines.append("")
    if report.get("warnings"):
        lines.append("Warnings:")
        for w in report["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    counts = report.get("counts", {})
    if counts:
        lines.append("Counts:")
        for k, v in counts.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    if report.get("status_distribution"):
        lines.append("Status distribution:")
        for k, v in sorted(report["status_distribution"].items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
    if report.get("priority_distribution"):
        lines.append("Priority distribution:")
        for k, v in sorted(report["priority_distribution"].items()):
            lines.append(f"- {k}: {v}")
        lines.append("")
    if report.get("sn_739_preview"):
        r = report["sn_739_preview"]
        lines.append("SN_739 / label_id 739 preview:")
        lines.append(f"- current: {r.get('current_acronym')} | {r.get('current_name')}")
        lines.append(f"- proposed: {r.get('proposed_acronym')} | {r.get('proposed_name')}")
        lines.append(f"- status: {r.get('resource_review_status')}")
        lines.append(f"- priority: {r.get('priority')}")
        lines.append(f"- flags: {r.get('flags')}")
        lines.append("")
    outputs = report.get("outputs", {})
    if outputs:
        lines.append("Outputs:")
        for k, v in outputs.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    lines.append("Next step:")
    lines.append("- Review v33_5_visible_problem_candidates.csv and v33_5_manual_review_template.csv.")
    lines.append("- Do not apply anything automatically yet; this audit only prepares the next manual cleanup batch.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: List[str]) -> int:
    cwd = Path.cwd()
    project_root = find_project_root(cwd)
    # Optional explicit root argument.
    if len(argv) >= 2:
        project_root = Path(argv[1]).resolve()
    report = audit(project_root)
    summary = Path(project_root) / REPORT_REL / "v33_5_pending_ugly_label_audit_summary.txt"
    print(f"{VERSION}")
    print(f"Project root: {project_root}")
    print(f"PASSED: {report.get('passed')}")
    print(f"Summary: {summary}")
    if report.get("errors"):
        print("Errors:")
        for e in report["errors"]:
            print(f"- {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
