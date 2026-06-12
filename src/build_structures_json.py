from __future__ import annotations
import json, re
from datetime import datetime
import pandas as pd
from utils_paths import OUTPUT_DIR, REPORTS_DIR, ROOT_ID

ROOT_NAME = "root"
ROOT_ACRONYM = "root"

def safe_acronym(name: str, label_id: int, provided: str | None = None) -> str:
    if provided and str(provided).strip():
        ac = str(provided).strip()
        if ac.lower() == "root":
            return "root"
        return ac
    words = re.findall(r"[A-Za-z0-9]+", name)
    if not words:
        return f"label_{label_id}"
    return ("".join(w[0].upper() for w in words[:6]) + f"_{label_id}")[:40]

def main() -> int:
    safe_csv = OUTPUT_DIR / "paxinos_labels_full_safe.csv"
    if not safe_csv.exists():
        raise FileNotFoundError("Missing paxinos_labels_full_safe.csv. Run analyze_placeholder_context.py first.")

    df = pd.read_csv(safe_csv)
    df = df[df["is_used_in_atlas"]].copy()
    df = df[df["id"].astype(int) != 0].copy()
    df = df.sort_values("id")

    structures = [{"id": ROOT_ID, "name": ROOT_NAME, "acronym": ROOT_ACRONYM, "rgb_triplet": [255, 255, 255], "structure_id_path": [ROOT_ID]}]
    for _, row in df.iterrows():
        label_id = int(row["id"])
        name = str(row.get("safe_name", row["name"]))
        structures.append({
            "id": label_id,
            "name": name,
            "acronym": safe_acronym(name, label_id, row.get("acronym", "")),
            "rgb_triplet": [
                int(row["r"]) if not pd.isna(row["r"]) else 128,
                int(row["g"]) if not pd.isna(row["g"]) else 128,
                int(row["b"]) if not pd.isna(row["b"]) else 128,
            ],
            "structure_id_path": [ROOT_ID, label_id],
        })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "structures_draft_flat.json"
    out.write_text(json.dumps(structures, indent=2, ensure_ascii=False), encoding="utf-8")
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "structure_count_including_root": len(structures),
        "root_id": ROOT_ID,
        "root_acronym": ROOT_ACRONYM,
        "root_name": ROOT_NAME,
        "output": str(out),
        "status": "draft_flat_hierarchy_with_brainglobe_root",
        "note": "BrainGlobe requires acronym == root.",
    }
    (REPORTS_DIR / "structures_draft_flat_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (REPORTS_DIR / "structures_draft_flat_report.txt").write_text("\n".join([
        "Draft structures.json report",
        "="*72,
        f"Generated: {report['generated_at']}",
        f"Output: {out}",
        f"Structure count including root: {len(structures)}",
        f"Root acronym: {ROOT_ACRONYM}",
        f"Root name: {ROOT_NAME}",
        "",
        "Important:",
        "- Flat draft tree.",
        "- Used placeholders retained with safe unresolved names.",
        "- BrainGlobe root acronym is exactly lower-case root.",
        "- Not final anatomical ontology.",
    ]), encoding="utf-8")
    print(f"Wrote draft structures file: {out}")
    print("BrainGlobe root acronym set to lower-case root.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
