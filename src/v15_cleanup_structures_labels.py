from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.table import Table

from utils_paths import OUTPUT_DIR, REPORTS_DIR, official_candidate_folder

console = Console()
ROOT_ID = 997


def is_bad_text(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    s = str(value).strip()
    return s == "" or s.lower() in {"nan", "none", "null"} or s.strip("- ") == ""


def safe_label_name(label_id: int, row: dict | None = None) -> str:
    if row:
        # Prefer contextual safe placeholder names generated earlier.
        for key in ["final_name", "safe_placeholder_name", "safe_name", "cortex_name", "name"]:
            val = row.get(key)
            if not is_bad_text(val):
                return str(val).strip()
    return f"unresolved_label_{label_id}"


def safe_acronym_from_name(name: str, label_id: int) -> str:
    if name == "root":
        return "root"
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        return f"label_{label_id}"
    base = "".join(w[0].upper() for w in words[:6])
    if not base:
        base = "LBL"
    return f"{base}_{label_id}"[:40]


def load_auxiliary_tables() -> dict[str, dict[int, dict]]:
    tables = {}

    candidates = {
        "full_safe": OUTPUT_DIR / "paxinos_labels_full_safe.csv",
        "placeholder_context": OUTPUT_DIR / "paxinos_used_placeholders_context.csv",
        "used": OUTPUT_DIR / "paxinos_labels_used.csv",
        "full": OUTPUT_DIR / "paxinos_labels_full.csv",
    }

    for name, path in candidates.items():
        mapping: dict[int, dict] = {}
        if path.exists():
            df = pd.read_csv(path)
            if "id" in df.columns:
                for _, r in df.iterrows():
                    try:
                        mapping[int(r["id"])] = r.to_dict()
                    except Exception:
                        pass
        tables[name] = mapping
    return tables


def cleanup_structures(structures_path: Path) -> dict[str, Any]:
    if not structures_path.exists():
        raise FileNotFoundError(f"Missing structures file: {structures_path}")

    structures = json.loads(structures_path.read_text(encoding="utf-8"))
    aux = load_auxiliary_tables()

    changes = []
    used_acronyms = set()
    duplicate_acronyms_fixed = 0
    bad_name_count_before = 0
    bad_acronym_count_before = 0

    # First pass: root and names.
    for s in structures:
        sid = int(s["id"])

        if sid == ROOT_ID:
            old = dict(s)
            s["name"] = "root"
            s["acronym"] = "root"
            s["structure_id_path"] = [ROOT_ID]
            s["rgb_triplet"] = s.get("rgb_triplet", [255, 255, 255])
            used_acronyms.add("root")
            if old != s:
                changes.append({"id": sid, "type": "root_normalized", "before": old, "after": dict(s)})
            continue

        old_name = s.get("name")
        old_acronym = s.get("acronym")

        if is_bad_text(old_name):
            bad_name_count_before += 1
            row = (
                aux["placeholder_context"].get(sid)
                or aux["full_safe"].get(sid)
                or aux["used"].get(sid)
                or aux["full"].get(sid)
            )
            s["name"] = safe_label_name(sid, row)
            changes.append({"id": sid, "type": "name_fixed", "before": old_name, "after": s["name"]})

        if is_bad_text(old_acronym):
            bad_acronym_count_before += 1
            s["acronym"] = safe_acronym_from_name(str(s["name"]), sid)
            changes.append({"id": sid, "type": "acronym_fixed", "before": old_acronym, "after": s["acronym"]})

        # BrainGlobe tree is still flat for now, but paths must be sane.
        target_path = [ROOT_ID, sid]
        if s.get("structure_id_path") != target_path:
            before = s.get("structure_id_path")
            s["structure_id_path"] = target_path
            changes.append({"id": sid, "type": "path_fixed", "before": before, "after": target_path})

        ac = str(s["acronym"])
        if ac in used_acronyms:
            before = ac
            s["acronym"] = f"{ac}_{sid}"[:60]
            duplicate_acronyms_fixed += 1
            changes.append({"id": sid, "type": "duplicate_acronym_fixed", "before": before, "after": s["acronym"]})
        used_acronyms.add(str(s["acronym"]))

    structures = sorted(structures, key=lambda x: (0 if int(x["id"]) == ROOT_ID else 1, int(x["id"])))
    structures_path.write_text(json.dumps(structures, indent=2, ensure_ascii=False), encoding="utf-8")

    bad_names_after = [s for s in structures if is_bad_text(s.get("name"))]
    bad_acronyms_after = [s for s in structures if is_bad_text(s.get("acronym"))]
    duplicate_acronyms_after = len([s["acronym"] for s in structures]) - len(set(s["acronym"] for s in structures))

    return {
        "structures_path": str(structures_path),
        "structure_count": len(structures),
        "bad_name_count_before": bad_name_count_before,
        "bad_acronym_count_before": bad_acronym_count_before,
        "bad_name_count_after": len(bad_names_after),
        "bad_acronym_count_after": len(bad_acronyms_after),
        "duplicate_acronyms_fixed": duplicate_acronyms_fixed,
        "duplicate_acronyms_after": duplicate_acronyms_after,
        "changes_count": len(changes),
        "changes_sample": changes[:200],
        "passed": len(bad_names_after) == 0 and len(bad_acronyms_after) == 0 and duplicate_acronyms_after == 0,
    }


def structures_path_for_stage(stage: str) -> Path:
    if stage == "draft":
        return OUTPUT_DIR / "structures_draft_flat.json"
    if stage == "official":
        return official_candidate_folder() / "structures.json"
    raise ValueError(stage)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["draft", "official"], required=True)
    args = parser.parse_args()

    path = structures_path_for_stage(args.stage)
    result = cleanup_structures(path)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": args.stage,
        "result": result,
        "passed": result["passed"],
    }

    suffix = f"_{args.stage}"
    (REPORTS_DIR / f"v15_label_cleanup_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V15 label/name cleanup report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Stage: {args.stage}",
        f"Structures path: {result['structures_path']}",
        f"PASSED: {report['passed']}",
        "",
        f"Structure count: {result['structure_count']}",
        f"Bad names before: {result['bad_name_count_before']}",
        f"Bad acronyms before: {result['bad_acronym_count_before']}",
        f"Bad names after: {result['bad_name_count_after']}",
        f"Bad acronyms after: {result['bad_acronym_count_after']}",
        f"Duplicate acronyms fixed: {result['duplicate_acronyms_fixed']}",
        f"Duplicate acronyms after: {result['duplicate_acronyms_after']}",
        f"Changes count: {result['changes_count']}",
        "",
        "Changes sample:",
    ]
    for ch in result["changes_sample"][:80]:
        lines.append(f"- {ch}")
    text = "\n".join(lines)

    (REPORTS_DIR / f"v15_label_cleanup_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v15_label_cleanup_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V15 label cleanup ({args.stage})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Bad names before", str(result["bad_name_count_before"]))
    table.add_row("Bad names after", str(result["bad_name_count_after"]))
    table.add_row("Bad acronyms before", str(result["bad_acronym_count_before"]))
    table.add_row("Bad acronyms after", str(result["bad_acronym_count_after"]))
    table.add_row("Duplicate acronyms after", str(result["duplicate_acronyms_after"]))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / f'v15_label_cleanup_report{suffix}.txt'}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
