
from __future__ import annotations
import argparse, csv, json
from datetime import datetime
from pathlib import Path
from typing import Any
from rich.console import Console
from rich.table import Table
from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()
ROOT_ID = 997
OLD_ROOT_ID = 997000

def folder_for(target: str) -> Path:
    return provisional_folder() if target == "provisional" else official_candidate_folder()

def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def save(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

def rgb(s):
    val = s.get("rgb_triplet", [255, 255, 255])
    if not isinstance(val, list) or len(val) < 3:
        val = [255, 255, 255]
    return [max(0, min(255, int(val[0]))), max(0, min(255, int(val[1]))), max(0, min(255, int(val[2])))]

def hexrgb(vals):
    return "".join(f"{x:02X}" for x in vals[:3])

def clean_path(path, sid: int):
    if not isinstance(path, list) or not path:
        return [ROOT_ID] if sid == ROOT_ID else [ROOT_ID, sid]
    out = [ROOT_ID if int(x) == OLD_ROOT_ID else int(x) for x in path]
    if out[0] != ROOT_ID:
        out = [ROOT_ID] + [x for x in out if x != ROOT_ID]
    if sid == ROOT_ID:
        return [ROOT_ID]
    if out[-1] != sid:
        out.append(sid)
    return [out[0]] + [x for x in out[1:] if x != ROOT_ID]

def parent_id(path, sid: int):
    if sid == ROOT_ID:
        return None
    return int(path[-2]) if len(path) >= 2 else ROOT_ID

def write_csv(folder: Path, structures):
    path = folder / "structures.csv"
    fields = ["id","atlas_id","ontology_id","acronym","name","color_hex_triplet","red","green","blue","graph_order","st_level","hemisphere_id","parent_structure_id","structure_id_path"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in structures:
            rv = rgb(s)
            w.writerow({
                "id": int(s["id"]),
                "atlas_id": int(s.get("atlas_id", 1)),
                "ontology_id": int(s.get("ontology_id", 1)),
                "acronym": str(s.get("acronym", "")),
                "name": str(s.get("name", "")),
                "color_hex_triplet": str(s.get("color_hex_triplet", hexrgb(rv))),
                "red": rv[0], "green": rv[1], "blue": rv[2],
                "graph_order": int(s.get("graph_order", 0)),
                "st_level": int(s.get("st_level", 1)),
                "hemisphere_id": int(s.get("hemisphere_id", 3)),
                "parent_structure_id": "" if s.get("parent_structure_id") is None else int(s.get("parent_structure_id")),
                "structure_id_path": "/".join(str(x) for x in s.get("structure_id_path", [])),
            })
    return path

def enrich(target: str):
    folder = folder_for(target)
    sp = folder / "structures.json"
    mp = folder / "metadata.json"
    structures = load(sp)
    meta = load(mp)
    changed = 0

    def skey(s):
        sid = int(s["id"])
        p = clean_path(s.get("structure_id_path", []), sid)
        return (0 if sid == ROOT_ID else len(p), sid)

    out = []
    for order, s in enumerate(sorted(structures, key=skey)):
        before = json.dumps(s, sort_keys=True)
        sid = int(s["id"])
        rv = rgb(s)
        path = clean_path(s.get("structure_id_path", []), sid)
        s["id"] = sid
        s["atlas_id"] = int(s.get("atlas_id", 1))
        s["ontology_id"] = int(s.get("ontology_id", 1))
        s["acronym"] = str(s.get("acronym", "root" if sid == ROOT_ID else f"ID{sid}"))
        s["name"] = str(s.get("name", "root" if sid == ROOT_ID else f"Region {sid}"))
        s["rgb_triplet"] = rv
        s["color_hex_triplet"] = str(s.get("color_hex_triplet", hexrgb(rv)))
        s["graph_order"] = 0 if sid == ROOT_ID else int(s.get("graph_order", order))
        s["st_level"] = 1 if sid == ROOT_ID else max(2, len(path))
        s["hemisphere_id"] = int(s.get("hemisphere_id", 3))
        s["parent_structure_id"] = parent_id(path, sid)
        s["structure_id_path"] = path
        if sid == ROOT_ID:
            s["name"] = "root"; s["acronym"] = "root"; s["structure_id_path"] = [ROOT_ID]; s["parent_structure_id"] = None
        after = json.dumps(s, sort_keys=True)
        if before != after:
            changed += 1
        out.append(s)

    ids = [int(s["id"]) for s in out]
    root_count = ids.count(ROOT_ID)
    idset = set(ids)
    missing_parents = sorted({int(s["parent_structure_id"]) for s in out if s.get("parent_structure_id") is not None and int(s["parent_structure_id"]) not in idset})
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    all_paths = all(isinstance(s.get("structure_id_path"), list) and s["structure_id_path"] and int(s["structure_id_path"][0]) == ROOT_ID for s in out)

    meta["root_id"] = ROOT_ID
    meta["structures_format_note"] = "V30 added Allen/ABBA Java-helper compatible structure fields."
    meta["files"] = meta.get("files", {})
    meta["files"]["structures"] = "structures.json"
    meta["files"]["structures_csv"] = "structures.csv"

    save(sp, out)
    save(mp, meta)
    cp = write_csv(folder, out)

    required = {"id","atlas_id","ontology_id","acronym","name","rgb_triplet","color_hex_triplet","graph_order","st_level","hemisphere_id","parent_structure_id","structure_id_path"}
    missing_keys = sorted({k for s in out for k in required if k not in s})
    passed = root_count == 1 and not missing_parents and not dupes and all_paths and not missing_keys and cp.exists()
    return {
        "folder": str(folder), "structure_count": len(out), "changed_count": changed,
        "root_count": root_count, "all_paths_start_997": all_paths,
        "missing_parents": missing_parents, "duplicate_ids": dupes,
        "missing_required_keys": missing_keys, "csv_path": str(cp),
        "metadata_root_id": meta.get("root_id"), "passed": passed
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["provisional","official"], required=True)
    args = ap.parse_args()
    res = enrich(args.target)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "target": args.target, "result": res, "passed": res["passed"]}
    suffix = "_" + args.target
    (REPORTS_DIR / f"v30_abba_java_structures_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    txt = "\n".join(["V30 ABBA Java structures compatibility report", "="*72, f"Generated: {report['generated_at']}", f"Target: {args.target}", f"PASSED: {report['passed']}", ""] + [f"- {k}: {v}" for k,v in res.items()])
    (REPORTS_DIR / f"v30_abba_java_structures_report{suffix}.txt").write_text(txt, encoding="utf-8")
    (REPORTS_DIR / "v30_abba_java_structures_report.txt").write_text(txt, encoding="utf-8")
    t = Table(title=f"V30 ABBA Java structures ({args.target})")
    t.add_column("Check"); t.add_column("Value")
    t.add_row("Passed", str(report["passed"]))
    t.add_row("root count", str(res["root_count"]))
    t.add_row("paths start 997", str(res["all_paths_start_997"]))
    t.add_row("missing parents", str(len(res["missing_parents"])))
    t.add_row("duplicate IDs", str(len(res["duplicate_ids"])))
    t.add_row("missing keys", str(len(res["missing_required_keys"])))
    console.print(t)
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
