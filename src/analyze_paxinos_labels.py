from __future__ import annotations
import json
from datetime import datetime
import nibabel as nib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from parse_labels import entries_by_id, parse_cortex_labels, parse_itksnap_labels
from utils_paths import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, ensure_project_dirs

console = Console()

def load_used_ids(path):
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj)
    unique = np.unique(data[np.isfinite(data)])
    return set(np.round(unique).astype(int).tolist())

def main() -> int:
    ensure_project_dirs()
    atlas = RAW_DIR / "Paxinos_Watson_Atlas.nii.gz"
    labels = RAW_DIR / "Paxinos_Watson_Labels.txt"
    cortex = RAW_DIR / "Paxinos_Watson_Labels_Cortex.txt"
    if not atlas.exists() or not labels.exists():
        raise FileNotFoundError("Missing Paxinos atlas or label table.")

    entries = parse_itksnap_labels(labels)
    cortex_entries = parse_cortex_labels(cortex) if cortex.exists() else []
    cortex_by_id = entries_by_id(cortex_entries)
    used_ids = load_used_ids(atlas)
    defined_ids = {e.id for e in entries}

    rows = []
    for e in entries:
        ce = cortex_by_id.get(e.id)
        rows.append({"id": e.id, "name": e.name, "acronym": ce.acronym if ce and ce.acronym != "---" else "", "cortex_name": ce.name if ce else "", "r": e.r, "g": e.g, "b": e.b, "is_used_in_atlas": e.id in used_ids, "is_placeholder": e.is_placeholder})

    df = pd.DataFrame(rows).sort_values("id")
    df_used = df[df["is_used_in_atlas"] & ~df["is_placeholder"]].copy()
    df_unused = df[~df["is_used_in_atlas"]].copy()
    df_placeholders = df[df["is_placeholder"]].copy()
    df_used_placeholders = df[df["is_placeholder"] & df["is_used_in_atlas"]].copy()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True); REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_DIR / "paxinos_labels_full.csv", index=False, encoding="utf-8")
    df_used.to_csv(OUTPUT_DIR / "paxinos_labels_used.csv", index=False, encoding="utf-8")
    df_unused.to_csv(OUTPUT_DIR / "paxinos_labels_unused.csv", index=False, encoding="utf-8")
    df_placeholders.to_csv(OUTPUT_DIR / "paxinos_placeholders.csv", index=False, encoding="utf-8")
    df_used_placeholders.to_csv(OUTPUT_DIR / "paxinos_used_placeholders.csv", index=False, encoding="utf-8")

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "used_label_count_including_background": len(used_ids), "used_nonzero_label_count": len([x for x in used_ids if x != 0]), "defined_label_count": len(defined_ids), "placeholder_label_count": int(df_placeholders.shape[0]), "used_placeholder_label_count": int(df_used_placeholders.shape[0]), "missing_labels_for_used_ids": sorted(used_ids - defined_ids), "unused_defined_label_count": int(df_unused.shape[0]), "used_placeholder_ids": df_used_placeholders["id"].astype(int).tolist()}
    (REPORTS_DIR / "paxinos_label_analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (REPORTS_DIR / "paxinos_label_analysis.txt").write_text("\\n".join(["Paxinos label analysis", "="*72] + [f"{k}: {v}" for k, v in report.items()]), encoding="utf-8")

    table = Table(title="Paxinos label analysis")
    table.add_column("Metric"); table.add_column("Value")
    for k in ["used_label_count_including_background","used_nonzero_label_count","defined_label_count","placeholder_label_count","used_placeholder_label_count","unused_defined_label_count"]:
        table.add_row(k, str(report[k]))
    console.print(table); console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'paxinos_label_analysis.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
