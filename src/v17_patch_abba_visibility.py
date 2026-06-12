from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
except Exception:
    Console = None
    Table = None

PATCH_MARKER = "# V17 local BrainGlobe atlas visibility patch"
PATCH_BLOCK = f"""
    {PATCH_MARKER}
    try:
        from brainglobe_atlasapi.list_atlases import (
            get_downloaded_atlases as _v17_get_downloaded_atlases,
            get_local_atlas_version as _v17_get_local_atlas_version,
        )
        for _v17_local_atlas in _v17_get_downloaded_atlases():
            if _v17_local_atlas not in available_atlases:
                available_atlases[_v17_local_atlas] = _v17_get_local_atlas_version(_v17_local_atlas)
                print("ABBA V17: registered local BrainGlobe atlas " + str(_v17_local_atlas))
    except Exception as _v17_exc:
        print("ABBA V17: could not merge local BrainGlobe atlases: " + repr(_v17_exc))

"""


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def report_dir() -> Path:
    rd = repo_root_from_script() / "reports"
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def candidate_abba_roots(explicit: str | None = None) -> list[Path]:
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())

    env = os.environ.get("ABBA_PYTHON_ROOT")
    if env:
        candidates.append(Path(env).expanduser().resolve())

    home = Path.home()
    for pattern in ["abba-python-*", "abba_python-*", "ABBA*"]:
        candidates.extend([p.resolve() for p in home.glob(pattern) if p.is_dir()])

    cwd = Path.cwd().resolve()
    candidates.append(cwd)
    candidates.extend(list(cwd.parents))

    seen = set()
    unique = []
    for p in candidates:
        s = str(p).lower()
        if s not in seen:
            seen.add(s)
            unique.append(p)
    return unique


def abba_py_for_root(root: Path) -> Path | None:
    possible = [
        root / "Lib" / "site-packages" / "abba_python" / "abba.py",
        root / "lib" / "site-packages" / "abba_python" / "abba.py",
        root / "site-packages" / "abba_python" / "abba.py",
    ]
    for p in possible:
        if p.exists():
            return p

    try:
        for p in root.rglob("abba.py"):
            if "abba_python" in [part.lower() for part in p.parts]:
                return p
    except Exception:
        pass
    return None


def python_for_root(root: Path) -> Path | None:
    for p in [root / "python.exe", root / "Scripts" / "python.exe", root / "bin" / "python"]:
        if p.exists():
            return p
    return None


def discover_abba_installations(explicit: str | None = None) -> list[dict[str, Any]]:
    installs = []
    for root in candidate_abba_roots(explicit):
        abba_py = abba_py_for_root(root)
        if abba_py:
            installs.append({
                "root": str(root),
                "abba_py": str(abba_py),
                "python": str(python_for_root(root)) if python_for_root(root) else None,
            })

    seen = set()
    unique = []
    for item in installs:
        key = item["abba_py"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def patch_abba_py(abba_py: Path, dry_run: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "abba_py": str(abba_py),
        "exists": abba_py.exists(),
        "already_patched": False,
        "patched": False,
        "backup": None,
        "error": None,
        "insert_anchor": None,
    }

    if not abba_py.exists():
        result["error"] = "abba.py not found"
        return result

    text = abba_py.read_text(encoding="utf-8", errors="replace")

    if PATCH_MARKER in text:
        result["already_patched"] = True
        result["patched"] = True
        return result

    if "def add_brainglobe_atlases(ij):" not in text:
        result["error"] = "Could not find def add_brainglobe_atlases(ij)"
        return result

    anchor = "    AtlasChooserCommand = jimport('ch.epfl.biop.atlas.scijava.AtlasChooserCommand')"
    if anchor in text:
        result["insert_anchor"] = anchor
        new_text = text.replace(anchor, PATCH_BLOCK + anchor, 1)
    else:
        lines = text.splitlines(keepends=True)
        idx = None
        for i, line in enumerate(lines):
            if "AtlasChooserCommand" in line and "jimport" in line:
                idx = i
                break
        if idx is None:
            result["error"] = "Could not find AtlasChooserCommand anchor"
            return result
        result["insert_anchor"] = f"line_{idx+1}"
        lines.insert(idx, PATCH_BLOCK)
        new_text = "".join(lines)

    if dry_run:
        result["dry_run_would_patch"] = True
        return result

    backup = abba_py.with_name(f"abba.py.backup_v17_{now_stamp()}")
    shutil.copy2(abba_py, backup)
    abba_py.write_text(new_text, encoding="utf-8")

    result["backup"] = str(backup)
    result["patched"] = True
    return result


def run_abba_python_probe(python_exe: str | None) -> dict[str, Any]:
    if not python_exe:
        return {"attempted": False, "error": "No ABBA python executable found"}

    cmd = [
        python_exe,
        "-c",
        (
            "from brainglobe_atlasapi.list_atlases import get_downloaded_atlases; "
            "from brainglobe_atlasapi import config; "
            "print('BRAINGLOBE_DIR=' + str(config.get_brainglobe_dir())); "
            "print('DOWNLOADED=' + repr(get_downloaded_atlases()))"
        ),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {
            "attempted": True,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except Exception as exc:
        return {"attempted": True, "error": repr(exc)}


def write_reports(report: dict[str, Any]) -> None:
    rd = report_dir()
    (rd / "v17_abba_visibility_patch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "V17 ABBA visibility patch report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Passed: {report['passed']}",
        f"Explicit ABBA root: {report.get('explicit_abba_root')}",
        f"Dry run: {report.get('dry_run')}",
        "",
        "Discovered installations:",
    ]
    for inst in report.get("installations", []):
        lines.append(f"- root: {inst.get('root')}")
        lines.append(f"  abba.py: {inst.get('abba_py')}")
        lines.append(f"  python: {inst.get('python')}")
    lines.append("")
    lines.append("Patch results:")
    for pr in report.get("patch_results", []):
        lines.append(json.dumps(pr, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("Python probes:")
    for probe in report.get("python_probes", []):
        lines.append(json.dumps(probe, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("What this patch does:")
    lines.append("- ABBA's add_brainglobe_atlases() registers atlases from get_all_atlases_lastversions().")
    lines.append("- V17 additionally merges get_downloaded_atlases() into that list.")
    lines.append("- This makes locally installed BrainGlobe atlases visible in ABBA's atlas dropdown.")
    lines.append("")
    lines.append("After patching:")
    lines.append("- Close ABBA/Fiji completely.")
    lines.append("- Restart ABBA.")
    lines.append("- Open Atlas.")
    lines.append("- Look for paxinos_watson_rat_40um, not paxinos_watson_rat_40um_v1.0.")

    (rd / "v17_abba_visibility_patch_report.txt").write_text("\n".join(lines), encoding="utf-8")

    final = [
        "V17 FINAL STATUS",
        "=" * 72,
        f"PASSED: {report['passed']}",
        "",
    ]
    if report["passed"]:
        final.append("ABBA visibility patch applied or was already present.")
        final.append("Restart ABBA and check whether paxinos_watson_rat_40um appears in Open Atlas.")
    else:
        final.append("ABBA visibility patch was not applied.")
        final.append("Review reports/v17_abba_visibility_patch_report.txt.")
    (rd / "v17_final_status.txt").write_text("\n".join(final), encoding="utf-8")


def print_table(report: dict[str, Any]) -> None:
    if Console is None or Table is None:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    console = Console()
    table = Table(title="V17 ABBA visibility patch")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Installations found", str(len(report.get("installations", []))))
    table.add_row("Patch attempts", str(len(report.get("patch_results", []))))
    table.add_row("Passed", str(report["passed"]))
    for pr in report.get("patch_results", []):
        table.add_row("patched", str(pr.get("patched")))
        table.add_row("already patched", str(pr.get("already_patched")))
        table.add_row("error", str(pr.get("error")))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {report_dir() / 'v17_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {report_dir() / 'v17_abba_visibility_patch_report.txt'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--abba-root", default=None, help="Path to ABBA Python root, e.g. C:\\Users\\49152\\abba-python-0.11.0")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", help="Patch all discovered ABBA installations. Default: patch first discovered installation.")
    args = parser.parse_args()

    installations = discover_abba_installations(args.abba_root)
    selected = installations if args.all else installations[:1]

    patch_results = []
    python_probes = []

    for inst in selected:
        patch_results.append(patch_abba_py(Path(inst["abba_py"]), dry_run=args.dry_run))
        python_probes.append({"root": inst["root"], "probe": run_abba_python_probe(inst.get("python"))})

    passed = bool(selected) and all((p.get("patched") or p.get("already_patched")) and not p.get("error") for p in patch_results)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "explicit_abba_root": args.abba_root,
        "dry_run": args.dry_run,
        "installations": installations,
        "selected_installations": selected,
        "patch_results": patch_results,
        "python_probes": python_probes,
        "passed": passed,
    }

    write_reports(report)
    print_table(report)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
