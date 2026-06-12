from __future__ import annotations
import argparse, json
from datetime import datetime
import nibabel as nib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from utils_paths import OUTPUT_DIR, RAW_DIR, REPORTS_DIR, ensure_project_dirs

console = Console()

CONTAINER_WORDS = ["cortex", "telencepahlon", "telencephalon", "thalamus", "hypothalamus", "tegmentum", "striatum", "amygdala", "hippocampus", "cerebellum", "midbrain", "hindbrain"]
LAYER_WORDS = ["layer", "molecular", "granule", "Purkinje", "oriens", "pyramidal"]
FIBER_WORDS = ["tract", "capsule", "commissure", "fiber", "fibre", "brachium", "lemniscus", "fornix", "cingulum", "white matter"]

def nearest_named(df, label_id, direction):
    if direction == "previous":
        c = df[(df["id"] < label_id) & (~df["is_placeholder"])]
        return None if c.empty else c.iloc[-1]
    c = df[(df["id"] > label_id) & (~df["is_placeholder"])]
    return None if c.empty else c.iloc[0]

def block_context(df, label_id, window=5):
    subset = df[(df["id"] >= label_id - window) & (df["id"] <= label_id + window)].copy()
    return " | ".join(f"{int(r.id)}:{r.name}" for r in subset.itertuples())

def classify_placeholder(prev_name, next_name, voxel_count):
    text = f"{prev_name} {next_name}".lower()
    if any(w.lower() in text for w in FIBER_WORDS):
        return "possible_fiber_or_white_matter"
    if any(w.lower() in text for w in LAYER_WORDS):
        return "possible_layer_group"
    if voxel_count > 50000 or any(w.lower() in text for w in CONTAINER_WORDS):
        return "possible_container_or_parent"
    return "unresolved_small_or_local"

def get_voxel_counts(atlas_path):
    img = nib.load(str(atlas_path))
    data = np.asanyarray(img.dataobj)
    unique, counts = np.unique(data[np.isfinite(data)], return_counts=True)
    return {int(round(float(k))): int(v) for k, v in zip(unique, counts)}

def safe_name(row, prev_row, next_row, placeholder_class):
    label_id = int(row["id"])
    prev_name = str(prev_row["name"]) if prev_row is not None else "none"
    next_name = str(next_row["name"]) if next_row is not None else "none"
    return f"{placeholder_class}_{label_id}_between_{prev_name[:30]}__and__{next_name[:30]}"

def interactive_review(df_context):
    console.print("[yellow]Interactive placeholder review enabled.[/yellow]")
    custom = {}
    for _, row in df_context.iterrows():
        console.print(f"\\n[bold]ID {int(row['id'])}[/bold] | voxels {int(row['voxel_count'])} | class {row['placeholder_class']}")
        console.print(f"Prev: {row['previous_named_id']} - {row['previous_named_name']}")
        console.print(f"Next: {row['next_named_id']} - {row['next_named_name']}")
        console.print(f"Safe: {row['safe_placeholder_name']}")
        answer = input("Custom name / ENTER / q: ").strip()
        if answer.lower() == "q":
            break
        if answer:
            custom[int(row["id"])] = answer
    return custom

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    ensure_project_dirs()

    atlas = RAW_DIR / "Paxinos_Watson_Atlas.nii.gz"
    full_csv = OUTPUT_DIR / "paxinos_labels_full.csv"
    used_placeholder_csv = OUTPUT_DIR / "paxinos_used_placeholders.csv"
    if not full_csv.exists() or not used_placeholder_csv.exists():
        raise FileNotFoundError("Run analyze_paxinos_labels.py first.")

    df = pd.read_csv(full_csv).sort_values("id")
    used_ph = pd.read_csv(used_placeholder_csv).sort_values("id")
    voxel_counts = get_voxel_counts(atlas)
    rows = []
    for _, row in used_ph.iterrows():
        label_id = int(row["id"])
        prev_row = nearest_named(df, label_id, "previous")
        next_row = nearest_named(df, label_id, "next")
        prev_name = prev_row["name"] if prev_row is not None else ""
        next_name = next_row["name"] if next_row is not None else ""
        voxel_count = voxel_counts.get(label_id, 0)
        placeholder_class = classify_placeholder(prev_name, next_name, voxel_count)
        safe = safe_name(row, prev_row, next_row, placeholder_class)
        rows.append({"id": label_id, "original_name": row["name"], "voxel_count": voxel_count, "previous_named_id": int(prev_row["id"]) if prev_row is not None else "", "previous_named_name": prev_name, "next_named_id": int(next_row["id"]) if next_row is not None else "", "next_named_name": next_name, "placeholder_class": placeholder_class, "safe_placeholder_name": safe, "final_name": safe, "manual_override": False, "context_window": block_context(df, label_id), "handling": "kept_as_unresolved_placeholder"})

    context = pd.DataFrame(rows).sort_values(["voxel_count", "id"], ascending=[False, True])
    if args.interactive and not context.empty:
        custom = interactive_review(context)
        for label_id, name in custom.items():
            context.loc[context["id"] == label_id, "final_name"] = name
            context.loc[context["id"] == label_id, "manual_override"] = True
            context.loc[context["id"] == label_id, "handling"] = "manual_name_override"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True); REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    context.to_csv(OUTPUT_DIR / "paxinos_used_placeholders_context.csv", index=False, encoding="utf-8")

    class_summary = context.groupby("placeholder_class")["id"].count().to_dict() if not context.empty else {}

    df2 = df.copy()
    mapping = dict(zip(context["id"], context["final_name"]))
    class_mapping = dict(zip(context["id"], context["placeholder_class"]))
    df2["safe_name"] = df2.apply(lambda r: mapping.get(int(r["id"]), r["name"]), axis=1)
    df2["placeholder_class"] = df2.apply(lambda r: class_mapping.get(int(r["id"]), ""), axis=1)
    df2["placeholder_handling"] = df2.apply(lambda r: "safe_unresolved_or_manual" if int(r["id"]) in mapping else "normal", axis=1)
    df2.to_csv(OUTPUT_DIR / "paxinos_labels_full_safe.csv", index=False, encoding="utf-8")

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "used_placeholder_count": int(context.shape[0]), "total_placeholder_voxels": int(context["voxel_count"].sum()) if not context.empty else 0, "manual_override_count": int(context["manual_override"].sum()) if not context.empty else 0, "placeholder_class_summary": class_summary, "largest_placeholders": context.head(20).to_dict(orient="records")}
    (REPORTS_DIR / "placeholder_context_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["Placeholder context report", "="*72, f"Generated: {report['generated_at']}", f"Used placeholder count: {report['used_placeholder_count']}", f"Total placeholder voxels: {report['total_placeholder_voxels']}", f"Manual override count: {report['manual_override_count']}", "", "Class summary:"]
    for k, v in class_summary.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "Largest placeholders:"]
    for r in report["largest_placeholders"]:
        lines.append(f"- ID {r['id']} | voxels {r['voxel_count']} | class {r['placeholder_class']} | prev {r['previous_named_id']} {r['previous_named_name']} | next {r['next_named_id']} {r['next_named_name']} | final {r['final_name']}")
    (REPORTS_DIR / "placeholder_context_report.txt").write_text("\\n".join(lines), encoding="utf-8")

    table = Table(title="Placeholder context"); table.add_column("Metric"); table.add_column("Value")
    table.add_row("Used placeholders", str(report["used_placeholder_count"]))
    table.add_row("Total placeholder voxels", str(report["total_placeholder_voxels"]))
    table.add_row("Manual overrides", str(report["manual_override_count"]))
    table.add_row("Classes", str(class_summary))
    console.print(table); console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'placeholder_context_report.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
