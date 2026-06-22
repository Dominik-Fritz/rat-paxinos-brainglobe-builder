#!/usr/bin/env python3
"""
V33.3b Label Acronym Resource Validator

Validates the sanitized curated Paxinos/Watson acronym resource before it is used
by the builder. It never modifies atlas files.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATLAS_NAME = "paxinos_watson_rat_40um"
RESOURCE_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms_with_basis.csv"
TXT_REL = Path("resources") / "label_curation" / "Paxinos_Watson_Labels_Acronyms.txt"
REPORT_REL = Path("reports") / "v33_3b_label_acronym_resource_validator"
ALLOWED_STATUS = {"pending_review", "approved", "rejected", "needs_manual_review", "defer", "do_not_apply"}
REQUIRED_COLS = {"label_id", "paxinos_name", "proposed_acronym", "proposed_name", "acronym_basis", "basis_detail", "confidence", "review_status"}
PLACEHOLDER_NAME_RE = re.compile(r"^[-–—_\s]+$")


def norm(s: Any) -> str:
    return str(s or "").strip()


def norm_text(s: Any) -> str:
    return re.sub(r"\s+", " ", norm(s).lower())


def read_csv_dicts(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


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
    checked=[]; selected=None
    for d in candidate_atlas_dirs(root, explicit):
        st = d / "structures.json"
        ok = d.exists() and st.exists()
        checked.append({"atlas_dir": str(d), "exists": d.exists(), "structures_json": st.exists(), "usable": ok})
        if selected is None and ok:
            selected=d
    return selected, checked


def validate(rows: List[Dict[str,str]], structures: List[Dict[str,Any]]) -> Dict[str, Any]:
    problems=[]; warnings=[]; name_mismatches=[]; approved_preview=[]
    structures_by_id={int(s["id"]): s for s in structures if "id" in s}
    structure_ids=set(structures_by_id)
    rows_by_id={}
    for i,row in enumerate(rows, start=2):
        try: sid=int(norm(row.get("label_id")))
        except Exception:
            problems.append({"line": i, "type":"bad_label_id", "detail": row.get("label_id","")}); continue
        if sid in rows_by_id:
            problems.append({"line": i, "label_id": sid, "type":"duplicate_resource_id"})
        rows_by_id[sid]=row
        status=norm_text(row.get("review_status"))
        if status not in ALLOWED_STATUS:
            problems.append({"line": i, "label_id": sid, "type":"bad_review_status", "detail": status})
        acr=norm(row.get("proposed_acronym")); name=norm(row.get("proposed_name"))
        if not acr: problems.append({"line": i, "label_id": sid, "type":"empty_proposed_acronym"})
        if not name: problems.append({"line": i, "label_id": sid, "type":"empty_proposed_name"})
        if status == "approved":
            conf=norm_text(row.get("confidence")); basis=norm_text(row.get("acronym_basis")); detail=norm(row.get("basis_detail"))
            if sid not in structure_ids:
                problems.append({"line": i, "label_id": sid, "type":"approved_id_not_in_structures"})
            if conf in {"", "low"}:
                problems.append({"line": i, "label_id": sid, "type":"approved_low_confidence"})
            if acr.upper().startswith("UNL") or PLACEHOLDER_NAME_RE.match(name):
                problems.append({"line": i, "label_id": sid, "type":"approved_placeholder_like_label"})
            if basis == "rule_generated_from_paxinos_name" and not detail:
                problems.append({"line": i, "label_id": sid, "type":"approved_rule_generated_without_detail"})
    resource_ids=set(rows_by_id)
    ids_missing_in_structures=sorted(resource_ids - structure_ids)
    structures_missing_in_resource=sorted(i for i in structure_ids - resource_ids if i != 997)
    if ids_missing_in_structures:
        problems.append({"type":"resource_ids_missing_in_structures", "count":len(ids_missing_in_structures), "ids":ids_missing_in_structures[:100]})
    if structures_missing_in_resource:
        warnings.append({"type":"structure_ids_missing_in_resource", "count":len(structures_missing_in_resource), "ids":structures_missing_in_resource[:100]})
    acr_counter=Counter(norm(r.get("proposed_acronym")) for r in rows if norm(r.get("proposed_acronym")))
    dup_resource=sorted(a for a,c in acr_counter.items() if c>1)
    if dup_resource:
        problems.append({"type":"duplicate_resource_acronyms", "count":len(dup_resource), "acronyms":dup_resource[:100]})
    final_acrs={sid:norm(st.get("acronym")) for sid,st in structures_by_id.items()}
    for sid,row in rows_by_id.items():
        if norm_text(row.get("review_status"))=="approved" and sid in final_acrs:
            final_acrs[sid]=norm(row.get("proposed_acronym"))
    dup_final=sorted(a for a,c in Counter(a for a in final_acrs.values() if a).items() if c>1)
    if dup_final:
        problems.append({"type":"duplicate_final_acronyms_if_approved_applied", "count":len(dup_final), "acronyms":dup_final[:100]})
    for sid,row in rows_by_id.items():
        st=structures_by_id.get(sid)
        if not st: continue
        current_name=norm_text(st.get("name")); proposed=norm_text(row.get("proposed_name")); paxinos=norm_text(row.get("paxinos_name"))
        status=norm_text(row.get("review_status"))
        if current_name and proposed and current_name != proposed and status in {"pending_review", "approved", "needs_manual_review"}:
            name_mismatches.append({
                "label_id":sid, "current_acronym":st.get("acronym",""), "current_name":st.get("name",""),
                "paxinos_name":row.get("paxinos_name",""), "proposed_acronym":row.get("proposed_acronym",""),
                "proposed_name":row.get("proposed_name",""), "review_status":row.get("review_status",""),
                "confidence":row.get("confidence",""), "acronym_basis":row.get("acronym_basis","")})
        if status=="approved" and st:
            old_acr=norm(st.get("acronym")); old_name=norm(st.get("name")); new_acr=norm(row.get("proposed_acronym")); new_name=norm(row.get("proposed_name"))
            if old_acr!=new_acr or old_name!=new_name:
                approved_preview.append({"label_id":sid,"old_acronym":old_acr,"old_name":old_name,"new_acronym":new_acr,"new_name":new_name,"acronym_basis":row.get("acronym_basis",""),"basis_detail":row.get("basis_detail",""),"confidence":row.get("confidence","")})
    return {
        "problems":problems,
        "warnings":warnings,
        "row_count":len(rows),
        "approved_count":sum(1 for r in rows if norm_text(r.get("review_status"))=="approved"),
        "pending_count":sum(1 for r in rows if norm_text(r.get("review_status"))=="pending_review"),
        "rejected_count":sum(1 for r in rows if norm_text(r.get("review_status")) in {"rejected","do_not_apply","defer"}),
        "resource_ids_missing_in_structures_count":len(ids_missing_in_structures),
        "structure_ids_missing_in_resource_count":len(structures_missing_in_resource),
        "duplicate_resource_acronyms_count":len(dup_resource),
        "duplicate_final_acronyms_count":len(dup_final),
        "name_mismatches_count":len(name_mismatches),
        "approved_change_preview_count":len(approved_preview),
        "by_status":dict(Counter(norm_text(r.get("review_status")) for r in rows)),
        "by_confidence":dict(Counter(norm_text(r.get("confidence")) for r in rows)),
        "by_basis":dict(Counter(norm(r.get("acronym_basis")) for r in rows)),
        "name_mismatches":name_mismatches,
        "approved_preview":approved_preview,
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--atlas-dir", default=None)
    ap.add_argument("--resource", default=None)
    args=ap.parse_args()
    root=Path(args.root).expanduser().resolve()
    resource=Path(args.resource).expanduser().resolve() if args.resource else root/RESOURCE_REL
    report_dir=root/REPORT_REL; report_dir.mkdir(parents=True, exist_ok=True)
    generated_at=datetime.now().isoformat(timespec="seconds")
    selected,checked=find_usable_atlas(root,args.atlas_dir)
    errors=[]
    if not resource.exists(): errors.append(f"Missing resource CSV: {resource}")
    if selected is None: errors.append("No usable atlas dir found. Run builder first or pass --atlas-dir.")
    report={"version":"V33.3b Label Acronym Resource Validator","generated_at":generated_at,"project_root":str(root),"does_modify_atlas":False,"resource_csv":str(resource),"candidate_atlas_dirs":checked,"selected_atlas_dir":str(selected) if selected else None,"errors":errors}
    if errors:
        report["passed"]=False
        (report_dir/"v33_3b_label_acronym_resource_validator_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
        print("FAILED"); return 2
    rows=read_csv_dicts(resource)
    missing=sorted(REQUIRED_COLS-set(rows[0].keys() if rows else []))
    if missing:
        report["validation"]={"problems":[{"type":"missing_required_columns","columns":missing}]}
        report["passed"]=False
    else:
        structures=load_structures(selected/"structures.json")  # type: ignore[operator]
        v=validate(rows, structures)
        write_csv(report_dir/"v33_3b_name_mismatches_current_vs_resource.csv", v["name_mismatches"], ["label_id","current_acronym","current_name","paxinos_name","proposed_acronym","proposed_name","review_status","confidence","acronym_basis"])
        write_csv(report_dir/"v33_3b_approved_change_preview.csv", v["approved_preview"], ["label_id","old_acronym","old_name","new_acronym","new_name","acronym_basis","basis_detail","confidence"])
        report["validation"]={k:v[k] for k in v if k not in {"name_mismatches","approved_preview"}}
        report["passed"]=len(v["problems"])==0
    summary=["V33.3b Label Acronym Resource Validator","========================================================================",f"Generated: {generated_at}",f"Project root: {root}",f"Selected atlas dir: {selected}",f"Resource CSV: {resource}",f"PASSED: {report.get('passed')}",""]
    if "validation" in report:
        v=report["validation"]
        summary += ["Counts:",f"- row_count: {v.get('row_count')}",f"- approved_count: {v.get('approved_count')}",f"- pending_count: {v.get('pending_count')}",f"- rejected/do_not_apply/defer count: {v.get('rejected_count')}",f"- resource_ids_missing_in_structures: {v.get('resource_ids_missing_in_structures_count')}",f"- structure_ids_missing_in_resource: {v.get('structure_ids_missing_in_resource_count')}",f"- duplicate_resource_acronyms: {v.get('duplicate_resource_acronyms_count')}",f"- duplicate_final_acronyms_if_approved_applied: {v.get('duplicate_final_acronyms_count')}",f"- name_mismatches_current_vs_resource: {v.get('name_mismatches_count')}",f"- approved_change_preview_count: {v.get('approved_change_preview_count')}","","Status breakdown:"]
        for k,val in sorted(v.get("by_status",{}).items()): summary.append(f"- {k}: {val}")
        summary += ["","Problems:"]
        probs=v.get("problems",[])
        summary += [f"- {p}" for p in probs[:30]] if probs else ["- none"]
        summary += ["","Warnings:"]
        warns=v.get("warnings",[])
        summary += [f"- {w}" for w in warns[:30]] if warns else ["- none"]
    (report_dir/"v33_3b_label_acronym_resource_validator_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    (report_dir/"v33_3b_label_acronym_resource_validator_summary.txt").write_text("\n".join(summary)+"\n",encoding="utf-8")
    print("PASSED" if report.get("passed") else "FAILED")
    print(f"Reports: {report_dir}")
    return 0 if report.get("passed") else 1

if __name__ == "__main__":
    raise SystemExit(main())
