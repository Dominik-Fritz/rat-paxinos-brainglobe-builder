from __future__ import annotations

"""Draft BrainGlobe structures.json builder.

This file is intentionally conservative. The first project phase should validate
the source data and label table before creating a final atlas package.

BrainGlobe needs a hierarchy. The BlueBrain/Paxinos label table mostly provides
ID-to-name mappings, not a fully explicit parent-child ontology. This script
therefore creates a flat draft tree under a synthetic root. That is useful for
testing, but it is not yet the final anatomical hierarchy.
"""

import json
from pathlib import Path

from parse_labels import parse_cortex_labels, parse_itksnap_labels
from utils_paths import OUTPUT_DIR, RAW_DIR


ROOT_ID = 997
ROOT_NAME = "Paxinos-Watson Rat Brain"


def safe_acronym(name: str, label_id: int) -> str:
    cleaned = (
        name.replace(",", "")
        .replace("(", "")
        .replace(")", "")
        .replace("/", "_")
        .replace(" ", "_")
        .replace("-", "_")
    )
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:40] or f"label_{label_id}"


def main() -> int:
    label_path = RAW_DIR / "Paxinos_Watson_Labels.txt"
    cortex_path = RAW_DIR / "Paxinos_Watson_Labels_Cortex.txt"

    if not label_path.exists():
        raise FileNotFoundError(f"Missing label file: {label_path}")

    labels = parse_itksnap_labels(label_path)
    cortex = parse_cortex_labels(cortex_path) if cortex_path.exists() else []
    cortex_by_id = {entry.id: entry for entry in cortex}

    children = []
    for entry in labels:
        if entry.id == 0:
            continue
        cortex_entry = cortex_by_id.get(entry.id)
        acronym = cortex_entry.acronym if cortex_entry and cortex_entry.acronym != "---" else safe_acronym(entry.name, entry.id)

        children.append(
            {
                "id": int(entry.id),
                "name": entry.name,
                "acronym": acronym,
                "rgb_triplet": [
                    int(entry.r if entry.r is not None else 128),
                    int(entry.g if entry.g is not None else 128),
                    int(entry.b if entry.b is not None else 128),
                ],
                "structure_id_path": [ROOT_ID, int(entry.id)],
            }
        )

    structures = [
        {
            "id": ROOT_ID,
            "name": ROOT_NAME,
            "acronym": "PW_RAT",
            "rgb_triplet": [255, 255, 255],
            "structure_id_path": [ROOT_ID],
        },
        *children,
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "structures_draft_flat.json"
    out.write_text(json.dumps(structures, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote draft structures file: {out}")
    print("Warning: this is a flat draft tree, not the final anatomical hierarchy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
