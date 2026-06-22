from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

VERSION = "V33.6 Manual Cleanup Batch Planner"

PROJECT_ROOT = Path.cwd()
IN_DIR = PROJECT_ROOT / "reports" / "v33_5_pending_ugly_label_audit"
OUT_DIR = PROJECT_ROOT / "reports" / "v33_6_manual_cleanup_batches"

MANUAL_CANDIDATES = IN_DIR / "v33_5_manual_review_template.csv"
QUEUE = IN_DIR / "v33_5_pending_ugly_label_queue.csv"
VISIBLE = IN_DIR / "v33_5_visible_problem_candidates.csv"
NAME_FLAGS = IN_DIR / "v33_5_name_quality_flags.csv"
PARENT_FLAGS = IN_DIR / "v33_5_parent_path_suspicion_flags.csv"


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: List[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        seen = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        fieldnames = seen
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def safe_int(x, default=0):
    try:
        if x is None or str(x).strip() == "":
            return default
        return int(float(str(x).replace(",", ".")))
    except Exception:
        return default


def priority_rank(priority: str) -> int:
    order = {
        "P0_project_critical_or_visible": 0,
        "P1_ugly_label_visible_cleanup": 1,
        "P2_semantic_review": 2,
        "P3_container_metadata": 3,
        "P4_do_not_apply_documented": 4,
        "P5_background": 5,
    }
    return order.get(priority, 99)


def has_flag(row: Dict[str, str], flag: str) -> bool:
    return flag in str(row.get("flags", ""))


def family_group(row: Dict[str, str]) -> str:
    text = " ".join([
        str(row.get("current_name", "")),
        str(row.get("parent_path_names", "")),
        str(row.get("parent_path_acronyms", "")),
        str(row.get("proposed_name", "")),
    ]).lower()
    if "placeholder" in text or "possible_layer" in text or "possible_container" in text or "-------" in text:
        return "placeholder_or_unresolved"
    if "sept" in text:
        return "septal_region"
    if "hippocamp" in text or "subiculum" in text or "dentate" in text or "entorhinal" in text or "alveus" in text or "fimbria" in text:
        return "hippocampal_entorhinal"
    if "hypothalam" in text or "mammill" in text or "preoptic" in text:
        return "hypothalamus_preoptic"
    if "thalam" in text or "geniculate" in text:
        return "thalamus_geniculate"
    if "amyg" in text:
        return "amygdala"
    if "accumbens" in text or "striat" in text or "pallid" in text or "basal ganglia" in text:
        return "basal_ganglia_accumbens"
    if "periaqueductal" in text or "p a g" in text or "midbrain" in text or "tegment" in text or "substantia nigra" in text:
        return "midbrain_pag_tegmentum"
    if "fiber" in text or "commissure" in text or "tract" in text or "radiation" in text or "bundle" in text or "white matter" in text:
        return "fiber_tracts_commissures"
    if "cortex" in text or "cortical" in text:
        return "cortex_other"
    if "cerebell" in text:
        return "cerebellum"
    if "medulla" in text or "pons" in text or "raphe" in text or "reticular" in text:
        return "hindbrain_brainstem"
    if "ventricle" in text or "ventricular" in text:
        return "ventricular_system"
    return "other"


def risk_level(row: Dict[str, str]) -> str:
    status = str(row.get("resource_review_status", ""))
    flags = str(row.get("flags", ""))
    if status == "do_not_apply" or "placeholder" in flags:
        return "do_not_apply_or_placeholder"
    if "suspicious_parent_path" in flags and "generic_acronym_risk" in flags:
        return "high_manual_risk"
    if "suspicious_parent_path" in flags:
        return "parent_path_review_needed"
    if "generic_acronym_risk" in flags:
        return "generic_acronym_review_needed"
    if "proposed_acronym_numeric_suffix" in flags:
        return "proposed_suffix_review_needed"
    if "rule_generated_basis" in flags:
        return "manual_acronym_confirmation_needed"
    return "low_or_unknown"


def recommended_next_step(row: Dict[str, str]) -> str:
    status = str(row.get("resource_review_status", ""))
    flags = str(row.get("flags", ""))
    if status == "do_not_apply" or "placeholder" in flags:
        return "keep out of apply; needs source/manual ontology decision, not display cleanup"
    if "suspicious_parent_path" in flags:
        return "review acronym/name and parent path separately; acronym-only fix may improve display but hierarchy remains suspect"
    if "generic_acronym_risk" in flags:
        return "manual acronym confirmation required before approval"
    if "current_acronym_numeric_suffix" in flags:
        return "candidate for display acronym cleanup after manual confirmation"
    return "manual review"


def enrich(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    out = []
    for r in rows:
        rr = dict(r)
        rr["family_group"] = family_group(r)
        rr["risk_level"] = risk_level(r)
        rr["v33_6_recommended_next_step"] = recommended_next_step(r)
        rr["v33_6_manual_decision"] = ""
        rr["v33_6_manual_final_acronym"] = ""
        rr["v33_6_manual_final_name"] = ""
        rr["v33_6_manual_note"] = ""
        out.append(rr)
    return out


def sort_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(rows, key=lambda r: (priority_rank(str(r.get("priority", ""))), -safe_int(r.get("voxel_count", 0)), safe_int(r.get("label_id", 0))))


def main() -> int:
    errors = []
    warnings = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    source = MANUAL_CANDIDATES if MANUAL_CANDIDATES.exists() else VISIBLE if VISIBLE.exists() else QUEUE
    rows_raw = read_csv(source)
    if not rows_raw:
        errors.append(f"Missing or empty input file. Expected one of: {MANUAL_CANDIDATES}, {VISIBLE}, {QUEUE}")

    rows = sort_rows(enrich(rows_raw)) if rows_raw else []
    pending_rows = [r for r in rows if str(r.get("resource_review_status", "")) == "pending_review"]
    non_placeholder_pending = [r for r in pending_rows if "placeholder" not in str(r.get("flags", ""))]
    visible_top60 = sort_rows([r for r in non_placeholder_pending if str(r.get("priority", "")).startswith("P0") or str(r.get("priority", "")).startswith("P1")])[:60]
    p0_top100 = sort_rows([r for r in rows if str(r.get("priority", "")).startswith("P0")])[:100]
    parent_rows = sort_rows([r for r in rows if "suspicious_parent_path" in str(r.get("flags", ""))])
    do_not_apply_rows = sort_rows([r for r in rows if str(r.get("resource_review_status", "")) == "do_not_apply" or "placeholder" in str(r.get("flags", ""))])
    generic_risk_rows = sort_rows([r for r in rows if "generic_acronym_risk" in str(r.get("flags", ""))])

    # family batches, pending non-placeholder only. Keep manageable top 75 each.
    family_rows: Dict[str, List[Dict[str, object]]] = {}
    for r in non_placeholder_pending:
        family_rows.setdefault(str(r.get("family_group", "other")), []).append(r)

    common_fields = [
        "priority", "family_group", "risk_level", "label_id", "voxel_count",
        "current_acronym", "current_name", "parent_path_acronyms", "parent_path_names",
        "resource_review_status", "proposed_acronym", "proposed_name", "confidence",
        "acronym_basis", "basis_detail", "flags", "v33_6_recommended_next_step",
        "v33_6_manual_decision", "v33_6_manual_final_acronym", "v33_6_manual_final_name", "v33_6_manual_note",
    ]

    outputs = {}
    def save(name: str, out_rows: List[Dict[str, object]]):
        path = OUT_DIR / name
        write_csv(path, out_rows, common_fields)
        outputs[name] = str(path)

    save("v33_6_batch01_visible_top60_review.csv", visible_top60)
    save("v33_6_p0_top100_review.csv", p0_top100)
    save("v33_6_parent_path_suspicion_focus.csv", parent_rows)
    save("v33_6_do_not_apply_or_placeholder_review.csv", do_not_apply_rows)
    save("v33_6_generic_acronym_risk_focus.csv", generic_risk_rows)

    for fam, fam_rows in sorted(family_rows.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        safe_name = re.sub(r"[^A-Za-z0-9_]+", "_", fam).strip("_") or "other"
        save(f"v33_6_family_{safe_name}_review.csv", sort_rows(fam_rows)[:75])

    family_distribution = {fam: len(vals) for fam, vals in sorted(family_rows.items(), key=lambda kv: (-len(kv[1]), kv[0]))}
    risk_distribution: Dict[str, int] = {}
    for r in rows:
        risk_distribution[str(r.get("risk_level", "unknown"))] = risk_distribution.get(str(r.get("risk_level", "unknown")), 0) + 1

    report = {
        "version": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(PROJECT_ROOT),
        "input_source": str(source),
        "does_modify_annotation_volumes": False,
        "does_modify_structures_json": False,
        "does_modify_resources": False,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "input_rows": len(rows),
            "pending_review_rows": len(pending_rows),
            "pending_non_placeholder_rows": len(non_placeholder_pending),
            "visible_top60_rows": len(visible_top60),
            "p0_top100_rows": len(p0_top100),
            "parent_path_suspicion_rows": len(parent_rows),
            "do_not_apply_or_placeholder_rows": len(do_not_apply_rows),
            "generic_acronym_risk_rows": len(generic_risk_rows),
        },
        "family_distribution_pending_non_placeholder": family_distribution,
        "risk_distribution_all_input": risk_distribution,
        "outputs": outputs,
        "passed": len(errors) == 0,
    }

    report_path = OUT_DIR / "v33_6_manual_cleanup_batch_planner_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    summary_path = OUT_DIR / "v33_6_manual_cleanup_batch_planner_summary.txt"
    with summary_path.open("w", encoding="utf-8") as f:
        f.write(f"{VERSION}\n")
        f.write("=" * 72 + "\n")
        f.write(f"Generated: {report['generated_at']}\n")
        f.write(f"Project root: {PROJECT_ROOT}\n")
        f.write(f"Input source: {source}\n")
        f.write(f"PASSED: {report['passed']}\n\n")
        f.write("This is a read-only planner. It does not modify annotation volumes, structures.json, or resource files.\n\n")
        f.write("Counts:\n")
        for k, v in report["counts"].items():
            f.write(f"- {k}: {v}\n")
        f.write("\nTop family groups among pending non-placeholder rows:\n")
        for fam, count in list(family_distribution.items())[:12]:
            f.write(f"- {fam}: {count}\n")
        f.write("\nNext step:\n")
        f.write("- Review v33_6_batch01_visible_top60_review.csv first.\n")
        f.write("- Fill manual decision columns only for rows that are truly confirmed.\n")
        f.write("- Do not apply anything automatically from this planner.\n")

    outputs["report"] = str(report_path)
    outputs["summary"] = str(summary_path)

    print(summary_path.read_text(encoding="utf-8"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
