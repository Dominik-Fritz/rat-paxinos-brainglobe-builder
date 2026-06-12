from __future__ import annotations
import importlib.metadata
import json
import platform
import sys
from datetime import datetime

from utils_paths import REPORTS_DIR, ensure_project_dirs

PACKAGES = [
    "numpy",
    "pandas",
    "nibabel",
    "rich",
    "tqdm",
    "scikit-image",
    "tifffile",
    "brainglobe-atlasapi",
    "brainglobe-utils",
    "packaging",
]

def get_version(pkg: str) -> str:
    try:
        return importlib.metadata.version(pkg)
    except Exception as exc:
        return f"NOT FOUND ({exc!r})"

def main() -> int:
    ensure_project_dirs()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "packages": {pkg: get_version(pkg) for pkg in PACKAGES},
        "source_dataset": {
            "name": "BlueBrainHeadModels",
            "version": "v1",
            "published": "2024-04-04",
            "doi": "10.5281/zenodo.10926947",
        },
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "environment_versions.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["Environment versions", "=" * 72]
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Python: {report['python']}")
    lines.append(f"Executable: {report['python_executable']}")
    lines.append(f"Platform: {report['platform']}")
    lines.append("")
    lines.append("Packages:")
    for k, v in report["packages"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("Source dataset:")
    for k, v in report["source_dataset"].items():
        lines.append(f"- {k}: {v}")
    (REPORTS_DIR / "environment_versions.txt").write_text("\\n".join(lines), encoding="utf-8")
    print(f"Wrote: {REPORTS_DIR / 'environment_versions.txt'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
