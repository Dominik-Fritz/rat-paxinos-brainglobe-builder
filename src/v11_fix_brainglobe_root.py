from __future__ import annotations
import json
from datetime import datetime
from rich.console import Console
from rich.table import Table
from utils_paths import OUTPUT_DIR, REPORTS_DIR, ROOT_ID

console = Console()
STRUCTURES_PATH = OUTPUT_DIR / "structures_draft_flat.json"

def normalize_root(structures: list[dict]) -> tuple[list[dict], dict]:
    changes = {
        "had_lowercase_root_before": any(s.get("acronym") == "root" for s in structures),
        "root_id": ROOT_ID,
        "root_entry_added": False,
        "root_entry_modified": False,
        "children_relinked": 0,
        "duplicate_lowercase_roots_removed": 0,
    }

    filtered = []
    seen_root = False
    for s in structures:
        if s.get("acronym") == "root":
            if int(s.get("id")) == ROOT_ID and not seen_root:
                filtered.append(s)
                seen_root = True
            else:
                changes["duplicate_lowercase_roots_removed"] += 1
        else:
            filtered.append(s)
    structures = filtered

    root = None
    for s in structures:
        if int(s.get("id")) == ROOT_ID:
            root = s
            break

    if root is None:
        root = {
            "id": ROOT_ID,
            "name": "root",
            "acronym": "root",
            "rgb_triplet": [255, 255, 255],
            "structure_id_path": [ROOT_ID],
        }
        structures.insert(0, root)
        changes["root_entry_added"] = True
    else:
        before = dict(root)
        root["name"] = "root"
        root["acronym"] = "root"
        root["rgb_triplet"] = root.get("rgb_triplet", [255, 255, 255])
        root["structure_id_path"] = [ROOT_ID]
        if before != root:
            changes["root_entry_modified"] = True

    for s in structures:
        sid = int(s.get("id"))
        if sid == ROOT_ID:
            continue
        target_path = [ROOT_ID, sid]
        if s.get("structure_id_path") != target_path:
            s["structure_id_path"] = target_path
            changes["children_relinked"] += 1

    structures = sorted(structures, key=lambda s: (0 if int(s.get("id")) == ROOT_ID else 1, int(s.get("id"))))
    changes["has_lowercase_root_after"] = any(s.get("acronym") == "root" for s in structures)
    changes["structure_count"] = len(structures)
    return structures, changes

def main() -> int:
    if not STRUCTURES_PATH.exists():
        raise FileNotFoundError(f"Missing structures file: {STRUCTURES_PATH}")

    structures = json.loads(STRUCTURES_PATH.read_text(encoding="utf-8"))
    normalized, changes = normalize_root(structures)
    STRUCTURES_PATH.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "structures_path": str(STRUCTURES_PATH),
        "changes": changes,
        "passed": bool(changes["has_lowercase_root_after"]),
        "note": "BrainGlobe structure_tree_util expects acronym exactly lower-case root.",
    }
    (REPORTS_DIR / "v11_root_fix_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V11 BrainGlobe root fix report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Structures path: {STRUCTURES_PATH}",
        f"PASSED: {report['passed']}",
        "",
        "Changes:",
    ]
    for k, v in changes.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Interpretation:")
    if report["passed"]:
        lines.append("- structures.json now contains acronym == root.")
        lines.append("- All top-level structures are linked under root.")
    else:
        lines.append("- Root fix failed. BrainGlobe will probably throw KeyError('root') again.")
    (REPORTS_DIR / "v11_root_fix_report.txt").write_text("\n".join(lines), encoding="utf-8")

    table = Table(title="V11 BrainGlobe root fix")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Has root after", str(changes["has_lowercase_root_after"]))
    table.add_row("Root added", str(changes["root_entry_added"]))
    table.add_row("Root modified", str(changes["root_entry_modified"]))
    table.add_row("Children relinked", str(changes["children_relinked"]))
    table.add_row("Structure count", str(changes["structure_count"]))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v11_root_fix_report.txt'}")
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
