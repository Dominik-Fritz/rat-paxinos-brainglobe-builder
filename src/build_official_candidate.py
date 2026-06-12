from __future__ import annotations
import json
import shutil
import tarfile
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, provisional_folder, ATLAS_NAME

console = Console()

def copy_required(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    for name in ["reference.nii.gz", "annotation.nii.gz", "structures.json", "metadata.json", "README.md"]:
        source = src / name
        if not source.exists():
            raise FileNotFoundError(f"Missing required provisional file: {source}")
        shutil.copy2(source, dst / name)

def write_candidate_manifest(dst: Path):
    metadata = json.loads((dst / "metadata.json").read_text(encoding="utf-8"))
    manifest = {
        "atlas_name": ATLAS_NAME,
        "candidate_format": "brain-globe-style-local-candidate",
        "status": "experimental",
        "generated_from": "data/output/brainglobe_provisional/paxinos_watson_rat_40um",
        "files": {
            "reference": "reference.nii.gz",
            "annotation": "annotation.nii.gz",
            "structures": "structures.json",
            "metadata": "metadata.json",
            "README": "README.md",
        },
        "metadata": metadata,
        "note": "This folder is a candidate layout for later official BrainGlobe cache/import adaptation. It is not guaranteed to match every BrainGlobe release cache format.",
    }
    (dst / "candidate_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest

def create_tarball(folder: Path):
    tar_path = folder.parent / f"{folder.name}.tar.gz"
    if tar_path.exists():
        tar_path.unlink()
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(folder, arcname=folder.name)
    return tar_path

def main() -> int:
    src = provisional_folder()
    dst = official_candidate_folder()
    if not src.exists():
        raise FileNotFoundError(f"Missing provisional folder: {src}")

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    copy_required(src, dst)
    manifest = write_candidate_manifest(dst)
    tar_path = create_tarball(dst)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_folder": str(src),
        "candidate_folder": str(dst),
        "tarball": str(tar_path),
        "manifest": manifest,
        "status": "candidate_built",
        "next_step": "Run V6 compatibility test and optional local cache copy.",
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v6_official_candidate_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["V6 official candidate package report", "=" * 72, f"Generated: {report['generated_at']}", f"Source: {src}", f"Candidate: {dst}", f"Tarball: {tar_path}", "", "Status: candidate_built", "", "Important:", "- This is a BrainGlobe-style candidate layout.", "- It may still need exact cache-format adaptation for a specific BrainGlobe/ABBA release."]
    (REPORTS_DIR / "v6_official_candidate_report.txt").write_text("\\n".join(lines), encoding="utf-8")

    table = Table(title="V6 official candidate")
    table.add_column("Metric"); table.add_column("Value")
    table.add_row("Candidate folder", str(dst))
    table.add_row("Tarball", str(tar_path))
    table.add_row("Status", "candidate_built")
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v6_official_candidate_report.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
