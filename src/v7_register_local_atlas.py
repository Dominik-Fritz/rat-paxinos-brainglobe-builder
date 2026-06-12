from __future__ import annotations
import argparse
import inspect
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from utils_paths import REPORTS_DIR, official_candidate_folder, project_local_cache_folder, ATLAS_NAME, OUTPUT_DIR

console = Console()

def discover_brainglobe_paths():
    result = {"env": {}, "module": {}, "candidate_paths": []}
    for key in ["BRAINGLOBE_ATLAS_DIR", "BRAINGLOBE_DIR", "BRAINGLOBE_CONFIG_DIR"]:
        result["env"][key] = os.environ.get(key)

    try:
        import brainglobe_atlasapi
        result["module"]["brainglobe_atlasapi_file"] = str(Path(inspect.getfile(brainglobe_atlasapi)).resolve())
        result["module"]["brainglobe_atlasapi_version"] = getattr(brainglobe_atlasapi, "__version__", "unknown")
    except Exception as exc:
        result["module"]["brainglobe_atlasapi_error"] = repr(exc)

    home = Path.home()
    candidates = [
        home / ".brainglobe",
        home / ".brainglobe" / "atlases",
        home / ".cache" / "brainglobe",
        home / ".cache" / "brainglobe" / "atlases",
        home / "brainglobe",
        OUTPUT_DIR / "brainglobe_local_cache",
    ]
    result["candidate_paths"] = [str(p) for p in candidates]
    return result

def copy_candidate_to_project_cache():
    src = official_candidate_folder()
    dst = project_local_cache_folder()
    if not src.exists():
        raise FileNotFoundError(f"Missing official candidate folder: {src}")
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return dst

def write_project_cache_index(cache_root: Path):
    index = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "atlas_folder": str(cache_root),
        "note": "Project-local test registration only. Not a confirmed global BrainGlobe cache registration.",
        "files": {
            "reference": str(cache_root / "reference.nii.gz"),
            "annotation": str(cache_root / "annotation.nii.gz"),
            "structures": str(cache_root / "structures.json"),
            "metadata": str(cache_root / "metadata.json"),
        },
    }
    index_path = cache_root.parent / "local_atlas_index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return index_path, index

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register-local", action="store_true", help="Create project-local BrainGlobe-like cache copy.")
    args = parser.parse_args()

    paths = discover_brainglobe_paths()
    install_result = {"attempted": False}

    if args.register_local:
        dst = copy_candidate_to_project_cache()
        index_path, index = write_project_cache_index(dst)
        install_result = {
            "attempted": True,
            "project_cache_folder": str(dst),
            "index_path": str(index_path),
            "index": index,
        }

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "brainglobe_path_discovery": paths,
        "project_local_registration": install_result,
        "passed": True,
        "note": "V7 registration probe completed. This does not mutate the global BrainGlobe installation unless future code explicitly implements it.",
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v7_registration_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["V7 local registration report", "=" * 72, f"Generated: {report['generated_at']}", f"Atlas: {ATLAS_NAME}", "", "BrainGlobe path discovery:"]
    for k, v in paths.get("env", {}).items():
        lines.append(f"- env {k}: {v}")
    for k, v in paths.get("module", {}).items():
        lines.append(f"- module {k}: {v}")
    lines.append("")
    lines.append("Candidate cache paths:")
    for p in paths.get("candidate_paths", []):
        lines.append(f"- {p}")
    lines.append("")
    lines.append("Project-local registration:")
    for k, v in install_result.items():
        if k != "index":
            lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Interpretation:")
    if install_result.get("attempted"):
        lines.append("- Project-local cache copy was created.")
        lines.append("- This is safe and reversible.")
        lines.append("- It is not yet a true BrainGlobe global cache registration.")
    else:
        lines.append("- Registration was not requested. Use --register-local to create the project-local cache copy.")
    (REPORTS_DIR / "v7_registration_report.txt").write_text("\\n".join(lines), encoding="utf-8")

    table = Table(title="V7 local registration")
    table.add_column("Check"); table.add_column("Value")
    table.add_row("Passed", "True")
    table.add_row("Registration attempted", str(install_result.get("attempted")))
    table.add_row("Project cache folder", str(install_result.get("project_cache_folder")))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v7_registration_report.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
