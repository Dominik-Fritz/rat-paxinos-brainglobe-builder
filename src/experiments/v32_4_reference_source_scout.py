from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
from typing import Any

KEYWORDS = [
    "reference", "anatom", "nissl", "structure", "full", "label", "border",
    "waxholm", "whs", "sigma", "neurorat", "mri", "template", "atlas", "paxinos",
]
VOLUME_SUFFIXES = (
    ".nii", ".nii.gz", ".tif", ".tiff", ".nrrd", ".mha", ".mhd",
)
MAX_FILES_PER_ROOT = 20000
MAX_TIFF_SHAPE_READ_MB = 2048  # avoid trying to fully inspect monstrous stacks

@dataclass
class CandidateFile:
    path: str
    source_root: str
    kind: str
    size_mb: float
    score: int
    reason: str
    shape: str = ""
    dtype: str = ""
    orientation: str = ""
    voxel_size: str = ""
    read_error: str = ""


def has_suffix(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suf) for suf in VOLUME_SUFFIXES)


def score_file(path: Path) -> tuple[int, list[str]]:
    lname = path.name.lower()
    parts = []
    score = 0
    for kw in KEYWORDS:
        if kw in lname:
            score += 2
            parts.append(kw)
    if lname.endswith((".nii", ".nii.gz")):
        score += 3
        parts.append("nifti")
    if lname.endswith((".tif", ".tiff")):
        score += 2
        parts.append("tiff")
    if any(x in lname for x in ["reference", "nissl", "anatom", "mri", "structure full"]):
        score += 5
        parts.append("possible anatomical reference")
    if any(x in lname for x in ["annotation", "label", "border"]):
        score += 1
        parts.append("label/annotation related")
    return score, sorted(set(parts))


def inspect_nifti(path: Path) -> tuple[str, str, str, str, str]:
    try:
        import nibabel as nib
        img = nib.load(str(path))
        shape = str(tuple(int(x) for x in img.shape[:3]))
        dtype = str(img.get_data_dtype())
        orientation = "".join(nib.aff2axcodes(img.affine))
        voxel = str(tuple(float(x) for x in img.header.get_zooms()[:3]))
        return shape, dtype, orientation, voxel, ""
    except Exception as e:
        return "", "", "", "", f"{type(e).__name__}: {e}"


def inspect_tiff(path: Path) -> tuple[str, str, str]:
    try:
        import tifffile
        with tifffile.TiffFile(str(path)) as tif:
            series = tif.series[0]
            shape = str(tuple(int(x) for x in series.shape))
            dtype = str(series.dtype)
        return shape, dtype, ""
    except Exception as e:
        return "", "", f"{type(e).__name__}: {e}"


def safe_size_mb(path: Path) -> float:
    try:
        return round(path.stat().st_size / (1024 * 1024), 3)
    except OSError:
        return 0.0


def scan_root(root: Path, label: str) -> list[CandidateFile]:
    out: list[CandidateFile] = []
    if not root.exists():
        return out
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # prune obvious junk
        dirnames[:] = [d for d in dirnames if d.lower() not in {".git", ".venv", "__pycache__", "node_modules"}]
        for fn in filenames:
            count += 1
            if count > MAX_FILES_PER_ROOT:
                out.append(CandidateFile(
                    path=f"<scan stopped after {MAX_FILES_PER_ROOT} files>",
                    source_root=str(root),
                    kind="scan_limit",
                    size_mb=0.0,
                    score=0,
                    reason="Too many files; scan stopped to avoid wasting your afternoon.",
                ))
                return out
            p = Path(dirpath) / fn
            if not has_suffix(p):
                continue
            score, reasons = score_file(p)
            if score < 3:
                # keep only likely atlas/reference files
                continue
            size = safe_size_mb(p)
            kind = "nifti" if p.name.lower().endswith((".nii", ".nii.gz")) else "tiff" if p.name.lower().endswith((".tif", ".tiff")) else "volume"
            cf = CandidateFile(
                path=str(p),
                source_root=label,
                kind=kind,
                size_mb=size,
                score=score,
                reason="; ".join(reasons),
            )
            if kind == "nifti":
                cf.shape, cf.dtype, cf.orientation, cf.voxel_size, cf.read_error = inspect_nifti(p)
            elif kind == "tiff":
                cf.shape, cf.dtype, cf.read_error = inspect_tiff(p)
            out.append(cf)
    return out


def scan_brainglobe_cache(bg_root: Path) -> list[dict[str, Any]]:
    rows = []
    if not bg_root.exists():
        return rows
    for child in sorted(bg_root.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        meta_path = child / "metadata.json"
        row: dict[str, Any] = {
            "folder": str(child),
            "name": child.name,
            "metadata_exists": meta_path.exists(),
            "atlas_name": "",
            "title": "",
            "species": "",
            "resolution": "",
            "orientation": "",
            "shape": "",
            "files_keys": "",
            "reference_tiff": str(child / "reference.tiff") if (child / "reference.tiff").exists() else "",
            "annotation_tiff": str(child / "annotation.tiff") if (child / "annotation.tiff").exists() else "",
        }
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                row.update({
                    "atlas_name": meta.get("name", ""),
                    "title": meta.get("title", ""),
                    "species": meta.get("species", ""),
                    "resolution": str(meta.get("resolution", "")),
                    "orientation": meta.get("orientation", ""),
                    "shape": str(meta.get("shape", "")),
                    "files_keys": ",".join(sorted((meta.get("files") or {}).keys())),
                })
            except Exception as e:
                row["metadata_error"] = f"{type(e).__name__}: {e}"
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if not fieldnames:
        keys: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        fieldnames = keys or ["empty"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=str(Path.cwd()))
    ap.add_argument("--extra-root", action="append", default=[], help="Additional folder to scan")
    args = ap.parse_args()

    project_root = Path(args.project_root).resolve()
    report_dir = project_root / "reports" / "v32_4_reference_source_scout"
    report_dir.mkdir(parents=True, exist_ok=True)

    roots: list[tuple[Path, str]] = [
        (project_root / "data" / "raw", "project_data_raw"),
        (project_root / "data" / "output", "project_data_output"),
        (Path.home() / ".brainglobe", "user_brainglobe_cache"),
    ]
    for extra in args.extra_root:
        roots.append((Path(extra), f"extra:{extra}"))

    candidates: list[CandidateFile] = []
    for root, label in roots:
        candidates.extend(scan_root(root, label))
    candidates.sort(key=lambda x: (x.score, x.size_mb), reverse=True)

    bg_rows = scan_brainglobe_cache(Path.home() / ".brainglobe")

    candidate_dicts = [asdict(c) for c in candidates]
    write_csv(report_dir / "candidate_reference_sources.csv", candidate_dicts)
    write_csv(report_dir / "local_brainglobe_atlases.csv", bg_rows)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "report_dir": str(report_dir),
        "scanned_roots": [{"path": str(p), "label": label, "exists": p.exists()} for p, label in roots],
        "candidate_file_count": len([c for c in candidates if c.kind != "scan_limit"]),
        "local_brainglobe_atlas_count": len(bg_rows),
        "top_candidates": candidate_dicts[:25],
        "notes": [
            "This scout does not require ABBA and does not modify atlas files.",
            "Files are ranked by filename keywords and basic shape/readability, not by anatomical correctness.",
            "The next step is visual/registration QC for promising reference candidates only.",
        ],
    }
    (report_dir / "v32_4_reference_source_scout.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = []
    lines.append("V32.4 Reference Source Scout")
    lines.append("=" * 72)
    lines.append(f"Generated: {summary['generated_at']}")
    lines.append(f"Project root: {project_root}")
    lines.append(f"Report dir: {report_dir}")
    lines.append("")
    lines.append("Scanned roots:")
    for r in summary["scanned_roots"]:
        lines.append(f"- {r['label']}: {r['path']} exists={r['exists']}")
    lines.append("")
    lines.append(f"Candidate volume files: {summary['candidate_file_count']}")
    lines.append(f"Local BrainGlobe atlas folders: {summary['local_brainglobe_atlas_count']}")
    lines.append("")
    lines.append("Top candidate reference/source files:")
    for c in candidates[:20]:
        lines.append(f"- score={c.score:02d} size={c.size_mb} MB kind={c.kind} shape={c.shape} orient={c.orientation} :: {c.path}")
        if c.reason:
            lines.append(f"  reason: {c.reason}")
        if c.read_error:
            lines.append(f"  read_error: {c.read_error}")
    lines.append("")
    lines.append("Local BrainGlobe atlases:")
    for row in bg_rows[:50]:
        lines.append(f"- {row.get('name','')} atlas_name={row.get('atlas_name','')} shape={row.get('shape','')} orientation={row.get('orientation','')} files={row.get('files_keys','')}")
    lines.append("")
    lines.append("Main output files:")
    lines.append("- candidate_reference_sources.csv")
    lines.append("- local_brainglobe_atlases.csv")
    lines.append("- v32_4_reference_source_scout.json")
    (report_dir / "v32_4_reference_source_scout_summary.txt").write_text("\n".join(lines), encoding="utf-8")

    print("Wrote:")
    print(report_dir / "v32_4_reference_source_scout_summary.txt")
    print(report_dir / "candidate_reference_sources.csv")
    print(report_dir / "local_brainglobe_atlases.csv")
    print(report_dir / "v32_4_reference_source_scout.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
