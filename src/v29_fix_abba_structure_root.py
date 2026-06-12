from __future__ import annotations

import argparse
import csv
import json
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
    if name == "provisional":
        return provisional_folder()
    if name == "official":
        return official_candidate_folder()
    raise ValueError(name)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def is_root_like(s: dict[str, Any]) -> bool:
    try:
        sid = int(s.get("id"))
    except Exception:
        return False
    name = str(s.get("name", "")).strip().lower()
    acronym = str(s.get("acronym", "")).strip().lower()
    path = s.get("structure_id_path", [])
    return (
        sid in {OLD_ROOT_ID, NEW_ROOT_ID}
        and (
            name == "root"
            or acronym == "root"
            or path == [sid]
            or path == [OLD_ROOT_ID]
            or path == [NEW_ROOT_ID]
        )
    )


def fix_path(path: Any) -> list[int]:
    if not isinstance(path, list):
        return [NEW_ROOT_ID]
    out: list[int] = []
    for x in path:
        xi = int(x)
        if xi == OLD_ROOT_ID:
            xi = NEW_ROOT_ID
        out.append(xi)
    if not out or out[0] != NEW_ROOT_ID:
        out = [NEW_ROOT_ID] + [x for x in out if x != NEW_ROOT_ID]
    # Avoid duplicate root at front.
    while len(out) > 1 and out[1] == NEW_ROOT_ID:
        out.pop(1)
    return out


def rgb(s: dict[str, Any]) -> tuple[int, int, int]:
    val = s.get("rgb_triplet", [255, 255, 255])
    if isinstance(val, list) and len(val) >= 3:
        return int(val[0]), int(val[1]), int(val[2])
    return 255, 255, 255


def write_csv(folder: Path, structures: list[dict[str, Any]]) -> Path:
    path = folder / "structures.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "acronym",
                "name",
                "red",
                "green",
                "blue",
                "structure_id_path",
            ],
        )
        w.writeheader()
        for s in structures:
            r, g, b = rgb(s)
            w.writerow(
                {
                    "id": int(s["id"]),
                    "acronym": str(s.get("acronym", "")),
                    "name": str(s.get("name", "")),
                    "red": r,
                    "green": g,
                    "blue": b,
                    "structure_id_path": "/".join(str(x) for x in s.get("structure_id_path", [])),
                }
            )
    return path


def fix(folder: Path) -> dict[str, Any]:
    sp = folder / "structures.json"
    mp = folder / "metadata.json"

    structures = load_json(sp)
    metadata = load_json(mp)

    if not isinstance(structures, list):
        raise TypeError("structures.json is not a list")

    ids_before = [int(s["id"]) for s in structures if "id" in s]
    had_old_root = OLD_ROOT_ID in ids_before
    had_new_root = NEW_ROOT_ID in ids_before

    changed = 0
    removed_old_root_duplicates = 0
    normalized: list[dict[str, Any]] = []
    kept_root = False

    for s in structures:
        sid = int(s["id"])

        if sid == OLD_ROOT_ID and (had_new_root or is_root_like(s)):
            # If the standard root already exists, remove old duplicate root.
            # If it is the only root, convert it instead.
            if had_new_root:
                removed_old_root_duplicates += 1
                changed += 1
                continue
            s["id"] = NEW_ROOT_ID
            s["name"] = "root"
            s["acronym"] = "root"
            s["structure_id_path"] = [NEW_ROOT_ID]
            s["rgb_triplet"] = s.get("rgb_triplet", [255, 255, 255])
            kept_root = True
            changed += 1

        elif sid == NEW_ROOT_ID or is_root_like(s):
            s["id"] = NEW_ROOT_ID
            s["name"] = "root"
            s["acronym"] = "root"
            s["structure_id_path"] = [NEW_ROOT_ID]
            s["rgb_triplet"] = s.get("rgb_triplet", [255, 255, 255])
            if not kept_root:
                kept_root = True
                changed += 1
            else:
                # duplicate root-like entry after root was already kept
                removed_old_root_duplicates += 1
                changed += 1
                continue

        else:
            old_path = s.get("structure_id_path", [])
            new_path = fix_path(old_path)
            if new_path != old_path:
                s["structure_id_path"] = new_path
                changed += 1

        normalized.append(s)

    if not any(int(s["id"]) == NEW_ROOT_ID for s in normalized):
        normalized.insert(
            0,
            {
                "id": NEW_ROOT_ID,
                "name": "root",
                "acronym": "root",
                "rgb_triplet": [255, 255, 255],
                "structure_id_path": [NEW_ROOT_ID],
            },
        )
        changed += 1

    # Final duplicate-ID safety: keep first root, reject non-root duplicate ids.
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    duplicate_ids_removed = []
    for s in normalized:
        sid = int(s["id"])
        if sid in seen:
            duplicate_ids_removed.append(sid)
            changed += 1
            continue
        seen.add(sid)
        deduped.append(s)

    structures = sorted(
        deduped,
        key=lambda s: (0 if int(s["id"]) == NEW_ROOT_ID else 1, int(s["id"])),
    )

    # Enforce paths after sorting/dedup.
    for s in structures:
        if int(s["id"]) == NEW_ROOT_ID:
            s["name"] = "root"
            s["acronym"] = "root"
            s["structure_id_path"] = [NEW_ROOT_ID]
        else:
            s["structure_id_path"] = fix_path(s.get("structure_id_path", []))

    metadata["root_id"] = NEW_ROOT_ID
    metadata["structures_format_note"] = (
        "V29 normalized and merged root ids to standard BrainGlobe/Allen convention 997 "
        "for ABBA Java compatibility."
    )
    metadata["files"] = metadata.get("files", {})
    metadata["files"]["structures"] = "structures.json"
    metadata["files"]["structures_csv"] = "structures.csv"

    save_json(sp, structures)
    save_json(mp, metadata)
    cp = write_csv(folder, structures)

    ids_after = [int(s["id"]) for s in structures]
    root_entries = [s for s in structures if int(s["id"]) == NEW_ROOT_ID]
    all_paths_start_997 = all(
        isinstance(s.get("structure_id_path"), list)
        and len(s["structure_id_path"]) >= 1
        and int(s["structure_id_path"][0]) == NEW_ROOT_ID
        for s in structures
    )

    result = {
        "folder": str(folder),
        "structures_path": str(sp),
        "metadata_path": str(mp),
        "csv_path": str(cp),
        "old_root_present_before": had_old_root,
        "new_root_present_before": had_new_root,
        "old_root_present_after": OLD_ROOT_ID in ids_after,
        "new_root_present_after": NEW_ROOT_ID in ids_after,
        "root_count_after": len(root_entries),
        "removed_old_root_duplicates": removed_old_root_duplicates,
        "duplicate_ids_removed": duplicate_ids_removed,
        "paths_start_997": all_paths_start_997,
        "metadata_root_id": metadata.get("root_id"),
        "structure_count": len(structures),
        "changed_count": changed,
        "passed": (
            NEW_ROOT_ID in ids_after
            and OLD_ROOT_ID not in ids_after
            and len(root_entries) == 1
            and all_paths_start_997
            and int(metadata.get("root_id")) == NEW_ROOT_ID
            and cp.exists()
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["provisional", "official"], required=True)
    args = parser.parse_args()

    result = fix(target_folder(args.target))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "result": result,
        "passed": result["passed"],
    }

    suffix = "_" + args.target
    (REPORTS_DIR / f"v29_abba_structure_root_report{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        "V29 ABBA structure/root merge report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Target: {args.target}",
        f"PASSED: {report['passed']}",
        "",
    ]
    for k, v in result.items():
        lines.append(f"- {k}: {v}")

    text = "\n".join(lines)
    (REPORTS_DIR / f"v29_abba_structure_root_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v29_abba_structure_root_report.txt").write_text(text, encoding="utf-8")
    # compatibility
    (REPORTS_DIR / "v25_abba_structure_root_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V29 ABBA root merge ({args.target})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("root 997", str(result["new_root_present_after"]))
    table.add_row("old 997000", str(result["old_root_present_after"]))
    table.add_row("root count", str(result["root_count_after"]))
    table.add_row("paths ok", str(result["paths_start_997"]))
    table.add_row("csv", str(Path(result["csv_path"]).exists()))
    console.print(table)

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
