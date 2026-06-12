from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from datetime import datetime

PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

REQUIRED = [
    ("nibabel", "nibabel"),
    ("numpy", "numpy"),
    ("matplotlib", "matplotlib"),
    ("tifffile", "tifffile"),
    ("pandas", "pandas"),
]

def module_exists(module: str) -> bool:
    return importlib.util.find_spec(module) is not None

def main() -> int:
    lines = [
        "V36 ABBA Python environment repair report",
        "=" * 72,
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Python executable: {sys.executable}",
        f"Python version: {sys.version}",
        "",
        "Required modules:",
    ]

    missing = []
    for module, package in REQUIRED:
        ok = module_exists(module)
        lines.append(f"- {module}: {ok}")
        if not ok:
            missing.append(package)

    if missing:
        lines.append("")
        lines.append("Installing missing packages:")
        lines.append("- " + " ".join(missing))
        cmd = [sys.executable, "-m", "pip", "install", *missing]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        lines.append("")
        lines.append("pip stdout:")
        lines.append(proc.stdout)
        lines.append("")
        lines.append("pip stderr:")
        lines.append(proc.stderr)
        if proc.returncode != 0:
            lines.append(f"FAILED: pip returned {proc.returncode}")
            (REPORTS / "v36_env_repair_report.txt").write_text("\n".join(lines), encoding="utf-8")
            print("\n".join(lines))
            return proc.returncode

    lines.append("")
    lines.append("Post-check:")
    failed = []
    for module, package in REQUIRED:
        ok = module_exists(module)
        lines.append(f"- {module}: {ok}")
        if not ok:
            failed.append(module)

    passed = not failed
    lines.append("")
    lines.append(f"PASSED: {passed}")

    (REPORTS / "v36_env_repair_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
