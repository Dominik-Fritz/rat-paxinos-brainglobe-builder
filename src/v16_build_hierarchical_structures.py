from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from utils_paths import OUTPUT_DIR, REPORTS_DIR, official_candidate_folder

console = Console()

ROOT_ID = 997

# Artificial container IDs. Keep them high and outside Paxinos label range.
CONTAINERS = [
    (998000, "Brain major divisions", "major_divisions", ROOT_ID, [240, 240, 240]),
    (998100, "Forebrain", "forebrain", 998000, [220, 230, 255]),
    (998110, "Cerebral cortex", "cortex", 998100, [220, 255, 220]),
    (998120, "Olfactory system", "olfactory_system", 998100, [230, 255, 230]),
    (998130, "Hippocampal formation", "hippocampal_formation", 998100, [210, 245, 210]),
    (998140, "Amygdaloid complex", "amygdaloid_complex", 998100, [255, 220, 220]),
    (998150, "Basal ganglia", "basal_ganglia", 998100, [240, 220, 255]),
    (998160, "Septal region", "septal_region", 998100, [235, 235, 255]),
    (998170, "Thalamus", "thalamus", 998100, [255, 245, 220]),
    (998180, "Hypothalamus", "hypothalamus", 998100, [255, 235, 210]),
    (998190, "Pallium / subpallium unsorted", "pallium_subpallium_unsorted", 998100, [230, 230, 245]),
    (998200, "Midbrain", "midbrain", 998000, [255, 230, 180]),
    (998210, "Superior / inferior colliculus", "collicular_complex", 998200, [255, 225, 165]),
    (998220, "Periaqueductal gray / tectum / tegmentum", "midbrain_tegmentum", 998200, [255, 215, 155]),
    (998300, "Hindbrain", "hindbrain", 998000, [220, 240, 255]),
    (998310, "Pons", "pons", 998300, [215, 235, 255]),
    (998320, "Medulla", "medulla", 998300, [205, 225, 255]),
    (998400, "Cerebellum", "cerebellum", 998000, [210, 255, 255]),
    (998500, "Ventricular system", "ventricular_system", ROOT_ID, [180, 220, 255]),
    (998600, "White matter / fiber tracts", "white_matter_fiber_tracts", ROOT_ID, [245, 245, 245]),
    (998700, "Cranial nerves / peripheral roots", "cranial_nerves_roots", ROOT_ID, [255, 240, 245]),
    (998800, "Unclassified / review needed", "review_needed", ROOT_ID, [255, 255, 180]),
]

CONTAINER_BY_ACRONYM = {ac: cid for cid, name, ac, parent, rgb in CONTAINERS}
CONTAINER_BY_ID = {cid: (name, ac, parent, rgb) for cid, name, ac, parent, rgb in CONTAINERS}


def structures_path_for_stage(stage: str) -> Path:
    if stage == "draft":
        return OUTPUT_DIR / "structures_draft_flat.json"
    if stage == "official":
        return official_candidate_folder() / "structures.json"
    raise ValueError(stage)


def clean_text(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def match_parent(structure: dict[str, Any]) -> tuple[int, str, str]:
    name = str(structure.get("name", ""))
    ac = str(structure.get("acronym", ""))
    text = clean_text(name, ac)

    # Ventricle / ventricular
    if re.search(r"\bventricle\b|\bventricular\b|\b3v\b|\b4v\b|\blv\b|aqueduct", text):
        return CONTAINER_BY_ACRONYM["ventricular_system"], "ventricle_keyword", "high"

    # White matter and fiber tracts
    if re.search(r"corpus callosum|commissure|capsule|fornix|fimbria|fiber|fibre|tract|stria|lemniscus|peduncle|radiation|fasciculus|bundle|white matter|wm\b|optic tract|root", text):
        return CONTAINER_BY_ACRONYM["white_matter_fiber_tracts"], "white_matter_keyword", "medium"

    # Cranial nerves / roots
    if re.search(r"cranial nerve|nerve|oculomotor|trochlear|trigeminal|abducens|facial|vestibulocochlear|glossopharyngeal|vagus|hypoglossal|optic nerve", text):
        return CONTAINER_BY_ACRONYM["cranial_nerves_roots"], "cranial_nerve_keyword", "medium"

    # Cortex
    if re.search(r"\bcortex\b|cortical|isocortex|allocortex|cingulate|retrosplenial|motor cortex|somatosensory|visual cortex|auditory|insular|orbital|prelimbic|infralimbic|piriform|ectorhinal|perirhinal|temporal association|parietal|frontal|occipital|cortical layer|\blayer\b|au1|aud|auv|cg1|cg2|m1|m2|s1|s2|v1|v2", text):
        return CONTAINER_BY_ACRONYM["cortex"], "cortex_keyword", "medium"

    # Olfactory
    if re.search(r"olfactory|piriform|anterior olfactory|aon|olfactory tubercle|taenia tecta", text):
        return CONTAINER_BY_ACRONYM["olfactory_system"], "olfactory_keyword", "medium"

    # Hippocampus
    if re.search(r"hippocamp|dentate|subiculum|ca1|ca2|ca3|cornu ammonis|fimbria hippocampus|presubiculum|parasubiculum|entorhinal", text):
        return CONTAINER_BY_ACRONYM["hippocampal_formation"], "hippocampal_keyword", "medium"

    # Amygdala
    if re.search(r"amygd|amygdala|basolateral|central amyg|medial amyg|cortical amyg|intercalated", text):
        return CONTAINER_BY_ACRONYM["amygdaloid_complex"], "amygdala_keyword", "medium"

    # Basal ganglia
    if re.search(r"striatum|caudate|putamen|accumbens|globus pallidus|pallidum|substantia innominata|basal ganglia|nucleus accumbens|cp\b|cpu|gp\b|nac", text):
        return CONTAINER_BY_ACRONYM["basal_ganglia"], "basal_ganglia_keyword", "medium"

    # Septal
    if re.search(r"\bsept|septal|lateral septum|medial septum|triangular septal", text):
        return CONTAINER_BY_ACRONYM["septal_region"], "septal_keyword", "medium"

    # Thalamus
    if re.search(r"thalam|geniculate|habenula|mediodorsal|ventrobasal|ventral poster|lateral posterior|reticular thalamic|parafascicular|reuniens|intralaminar", text):
        return CONTAINER_BY_ACRONYM["thalamus"], "thalamus_keyword", "medium"

    # Hypothalamus
    if re.search(r"hypothalam|preoptic|suprachiasmatic|paraventricular|arcuate|mammillary|tuberomammillary|lateral hypothalamic|anterior hypothalamic|ventromedial hypothalamic|dorsomedial hypothalamic", text):
        return CONTAINER_BY_ACRONYM["hypothalamus"], "hypothalamus_keyword", "medium"

    # Cerebellum
    if re.search(r"cerebell|vermis|flocculus|nodulus|lobule|fastigial|interposed|dentate nucleus|purkinje|granule cell layer|molecular layer", text):
        return CONTAINER_BY_ACRONYM["cerebellum"], "cerebellum_keyword", "medium"

    # Midbrain
    if re.search(r"colliculus|tectum|tegment|periaqueductal|substantia nigra|red nucleus|ventral tegmental|interpeduncular|pretectal|edinger|mesenceph|midbrain|oculomotor nucleus", text):
        if re.search(r"colliculus|collicular", text):
            return CONTAINER_BY_ACRONYM["collicular_complex"], "colliculus_keyword", "medium"
        return CONTAINER_BY_ACRONYM["midbrain_tegmentum"], "midbrain_keyword", "medium"

    # Pons / medulla / hindbrain
    if re.search(r"pons|pontine|locus coeruleus|parabrachial|raphe pontis|trapezoid", text):
        return CONTAINER_BY_ACRONYM["pons"], "pons_keyword", "medium"
    if re.search(r"medulla|medullary|solitary|nucleus ambiguus|inferior olive|olive|cuneate|gracile|hypoglossal|vagal|area postrema|raphe magnus", text):
        return CONTAINER_BY_ACRONYM["medulla"], "medulla_keyword", "medium"

    # Broad fallback: if names sound like forebrain but not specific
    if re.search(r"pallium|subpallium|telencephalon|diencephalon|forebrain", text):
        return CONTAINER_BY_ACRONYM["pallium_subpallium_unsorted"], "broad_forebrain_keyword", "low"

    return CONTAINER_BY_ACRONYM["review_needed"], "no_rule_match", "review"


def build_path(parent_id: int) -> list[int]:
    path = [parent_id]
    current = parent_id
    while current != ROOT_ID:
        name, ac, parent, rgb = CONTAINER_BY_ID[current]
        current = parent
        path.append(current)
    return list(reversed(path))


def make_container_structures() -> list[dict[str, Any]]:
    out = []
    for cid, name, ac, parent, rgb in CONTAINERS:
        out.append({
            "id": cid,
            "name": name,
            "acronym": ac,
            "rgb_triplet": rgb,
            "structure_id_path": build_path(cid),
        })
    return out


def apply_hierarchy(path: Path) -> dict[str, Any]:
    structures = json.loads(path.read_text(encoding="utf-8"))

    # Remove old V16 artificial containers if rerunning.
    structures = [s for s in structures if int(s.get("id")) not in CONTAINER_BY_ID]

    root_entries = [s for s in structures if int(s.get("id")) == ROOT_ID]
    if not root_entries:
        structures.insert(0, {"id": ROOT_ID, "name": "root", "acronym": "root", "rgb_triplet": [255, 255, 255], "structure_id_path": [ROOT_ID]})
    else:
        root = root_entries[0]
        root["name"] = "root"
        root["acronym"] = "root"
        root["structure_id_path"] = [ROOT_ID]
        root["rgb_triplet"] = root.get("rgb_triplet", [255, 255, 255])

    assignments = []
    counts = Counter()
    confidence_counts = Counter()

    real_structures = [s for s in structures if int(s.get("id")) != ROOT_ID]
    containers = make_container_structures()

    for s in real_structures:
        sid = int(s["id"])
        parent_id, reason, confidence = match_parent(s)
        s["structure_id_path"] = build_path(parent_id) + [sid]
        assignments.append({
            "id": sid,
            "name": s.get("name"),
            "acronym": s.get("acronym"),
            "parent_id": parent_id,
            "parent_acronym": CONTAINER_BY_ID[parent_id][1],
            "parent_name": CONTAINER_BY_ID[parent_id][0],
            "reason": reason,
            "confidence": confidence,
        })
        counts[CONTAINER_BY_ID[parent_id][1]] += 1
        confidence_counts[confidence] += 1

    new_structures = []
    # root first
    new_structures.extend([s for s in structures if int(s.get("id")) == ROOT_ID][:1])
    new_structures.extend(containers)
    new_structures.extend(sorted(real_structures, key=lambda x: int(x["id"])))

    path.write_text(json.dumps(new_structures, indent=2, ensure_ascii=False), encoding="utf-8")

    review = [a for a in assignments if a["parent_acronym"] == "review_needed"]

    return {
        "structures_path": str(path),
        "structure_count_before_without_v16_containers": len(structures),
        "container_count_added": len(containers),
        "structure_count_after": len(new_structures),
        "assigned_real_structures": len(assignments),
        "assignment_counts_by_parent": dict(counts),
        "confidence_counts": dict(confidence_counts),
        "review_needed_count": len(review),
        "review_needed_sample": review[:100],
        "assignments_sample": assignments[:200],
        "passed": len(new_structures) == len(real_structures) + len(containers) + 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["draft", "official"], required=True)
    args = parser.parse_args()

    path = structures_path_for_stage(args.stage)
    result = apply_hierarchy(path)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stage": args.stage,
        "result": result,
        "passed": result["passed"],
        "warning": "Rule-based hierarchy is a first-pass anatomical grouping, not a curated Paxinos ontology.",
    }

    suffix = f"_{args.stage}"
    (REPORTS_DIR / f"v16_hierarchy_report{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V16 hierarchy builder report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Stage: {args.stage}",
        f"Structures path: {result['structures_path']}",
        f"PASSED: {report['passed']}",
        "",
        "Important warning:",
        "- This is a rule-based first-pass hierarchy, not a manually curated Paxinos ontology.",
        "",
        f"Container count added: {result['container_count_added']}",
        f"Structure count after: {result['structure_count_after']}",
        f"Assigned real structures: {result['assigned_real_structures']}",
        f"Review needed count: {result['review_needed_count']}",
        "",
        "Assignment counts by parent:",
    ]
    for k, v in sorted(result["assignment_counts_by_parent"].items(), key=lambda x: x[0]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Confidence counts:")
    for k, v in sorted(result["confidence_counts"].items(), key=lambda x: x[0]):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Review-needed sample:")
    for item in result["review_needed_sample"][:60]:
        lines.append(f"- {item}")
    text = "\n".join(lines)

    (REPORTS_DIR / f"v16_hierarchy_report{suffix}.txt").write_text(text, encoding="utf-8")
    (REPORTS_DIR / "v16_hierarchy_report.txt").write_text(text, encoding="utf-8")

    table = Table(title=f"V16 hierarchy builder ({args.stage})")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Passed", str(report["passed"]))
    table.add_row("Containers added", str(result["container_count_added"]))
    table.add_row("Assigned structures", str(result["assigned_real_structures"]))
    table.add_row("Review needed", str(result["review_needed_count"]))
    table.add_row("Total structures", str(result["structure_count_after"]))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / f'v16_hierarchy_report{suffix}.txt'}")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
