
from __future__ import annotations
import argparse, csv, json
from datetime import datetime
from pathlib import Path
from typing import Any
from rich.console import Console
from rich.table import Table
from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder

console = Console()
OLD_ROOT_ID = 997000
NEW_ROOT_ID = 997

def target_folder(name: str) -> Path:
    if name == "provisional": return provisional_folder()
    if name == "official": return official_candidate_folder()
    raise ValueError(name)

def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def fix_path(path):
    if not isinstance(path, list):
        return [NEW_ROOT_ID]
    out=[]
    for x in path:
        xi=int(x)
        out.append(NEW_ROOT_ID if xi == OLD_ROOT_ID else xi)
    if not out or out[0] != NEW_ROOT_ID:
        out=[NEW_ROOT_ID]+[x for x in out if x != NEW_ROOT_ID]
    return out

def rgb(s):
    val=s.get("rgb_triplet", [255,255,255])
    if isinstance(val, list) and len(val)>=3:
        return int(val[0]), int(val[1]), int(val[2])
    return 255,255,255

def write_csv(folder: Path, structures: list[dict[str, Any]]) -> Path:
    path=folder/"structures.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=["id","acronym","name","red","green","blue","structure_id_path"])
        w.writeheader()
        for s in structures:
            r,g,b=rgb(s)
            w.writerow({
                "id": int(s["id"]),
                "acronym": str(s.get("acronym","")),
                "name": str(s.get("name","")),
                "red": r, "green": g, "blue": b,
                "structure_id_path": "/".join(str(x) for x in s.get("structure_id_path", []))
            })
    return path

def fix(folder: Path):
    sp=folder/"structures.json"
    mp=folder/"metadata.json"
    structures=load_json(sp)
    metadata=load_json(mp)
    ids_before=[int(s["id"]) for s in structures]
    if OLD_ROOT_ID in ids_before and NEW_ROOT_ID in ids_before:
        raise RuntimeError("Both 997000 and 997 exist; refusing automatic merge.")

    changed=0
    for s in structures:
        sid=int(s["id"])
        if sid == OLD_ROOT_ID or str(s.get("acronym","")).lower()=="root" or str(s.get("name","")).lower()=="root":
            if int(s["id"]) != NEW_ROOT_ID:
                s["id"]=NEW_ROOT_ID; changed += 1
            s["name"]="root"; s["acronym"]="root"; s["structure_id_path"]=[NEW_ROOT_ID]
            s["rgb_triplet"]=s.get("rgb_triplet", [255,255,255])
        else:
            old=s.get("structure_id_path", [])
            new=fix_path(old)
            if new != old:
                s["structure_id_path"]=new; changed += 1

    if not any(int(s["id"]) == NEW_ROOT_ID for s in structures):
        structures.insert(0, {"id": NEW_ROOT_ID, "name":"root", "acronym":"root", "rgb_triplet":[255,255,255], "structure_id_path":[NEW_ROOT_ID]})
    roots=[s for s in structures if int(s["id"]) == NEW_ROOT_ID]
    if len(roots) != 1:
        raise RuntimeError(f"Expected exactly one root 997, found {len(roots)}")

    structures=sorted(structures, key=lambda s: (0 if int(s["id"]) == NEW_ROOT_ID else 1, int(s["id"])))
    metadata["root_id"]=NEW_ROOT_ID
    metadata["structures_format_note"]="V25 normalized root id to BrainGlobe/Allen convention 997 for ABBA Java compatibility."
    metadata["files"]=metadata.get("files", {})
    metadata["files"]["structures"]="structures.json"
    metadata["files"]["structures_csv"]="structures.csv"

    save_json(sp, structures)
    save_json(mp, metadata)
    cp=write_csv(folder, structures)

    ids_after=[int(s["id"]) for s in structures]
    paths_ok=all(isinstance(s.get("structure_id_path"), list) and s["structure_id_path"] and int(s["structure_id_path"][0]) == NEW_ROOT_ID for s in structures)
    return {
        "folder": str(folder), "structures_path": str(sp), "metadata_path": str(mp), "csv_path": str(cp),
        "old_root_present_before": OLD_ROOT_ID in ids_before, "new_root_present_before": NEW_ROOT_ID in ids_before,
        "old_root_present_after": OLD_ROOT_ID in ids_after, "new_root_present_after": NEW_ROOT_ID in ids_after,
        "paths_start_997": paths_ok, "metadata_root_id": metadata.get("root_id"),
        "structure_count": len(structures), "changed_count": changed,
        "passed": (NEW_ROOT_ID in ids_after and OLD_ROOT_ID not in ids_after and paths_ok and int(metadata.get("root_id")) == NEW_ROOT_ID and cp.exists())
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--target", choices=["provisional","official"], required=True)
    args=ap.parse_args()
    result=fix(target_folder(args.target))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report={"generated_at": datetime.now().isoformat(timespec="seconds"), "target": args.target, "result": result, "passed": result["passed"]}
    suf="_"+args.target
    (REPORTS_DIR/f"v25_abba_structure_root_report{suf}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines=["V25 ABBA structure/root compatibility report","="*72,f"Generated: {report['generated_at']}",f"Target: {args.target}",f"PASSED: {report['passed']}"]
    for k,v in result.items(): lines.append(f"{k}: {v}")
    txt="\n".join(lines)
    (REPORTS_DIR/f"v25_abba_structure_root_report{suf}.txt").write_text(txt, encoding="utf-8")
    (REPORTS_DIR/"v25_abba_structure_root_report.txt").write_text(txt, encoding="utf-8")
    t=Table(title=f"V25 ABBA root fix ({args.target})")
    t.add_column("Check"); t.add_column("Value")
    t.add_row("Passed", str(report["passed"]))
    t.add_row("root 997", str(result["new_root_present_after"]))
    t.add_row("old 997000", str(result["old_root_present_after"]))
    t.add_row("paths ok", str(result["paths_start_997"]))
    t.add_row("csv", str(Path(result["csv_path"]).exists()))
    console.print(t)
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
