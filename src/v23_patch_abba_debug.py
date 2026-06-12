
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

VISIBILITY_MARKER = "# V17 local BrainGlobe atlas visibility patch"
DEBUG_MARKER = "# V23 ABBA BrainGlobe atlas debug patch"

def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def report_dir() -> Path:
    rd = repo_root() / "reports"
    rd.mkdir(parents=True, exist_ok=True)
    return rd

def visibility_block() -> str:
    return f'''    {VISIBILITY_MARKER}
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

'''

def debug_block() -> str:
    return f'''                {DEBUG_MARKER}
                try:
                    bg_atlas = BrainGlobeAtlas(atlas_name)
                    from abba_python.abba_atlas import AbbaAtlas
                    atlas = AbbaAtlas(bg_atlas, ij)
                    atlas.initialize(None, None)
                    Abba.opened_atlases[atlas_name] = atlas
                    ij.object().addObject(atlas, atlas_name)  # store it in java's object service
                except Exception as _v23_exc:
                    import traceback as _v23_traceback
                    import os as _v23_os
                    from pathlib import Path as _v23_Path
                    _v23_log_dir = _v23_Path(_v23_os.environ.get("USERPROFILE", ".")) / "abba_paxinos_debug"
                    _v23_log_dir.mkdir(parents=True, exist_ok=True)
                    _v23_log = _v23_log_dir / "paxinos_watson_rat_40um_abba_error.log"
                    _v23_text = (
                        "ABBA V23 BrainGlobe atlas load failed\\n"
                        "atlas_name=" + str(atlas_name) + "\\n"
                        "exception=" + repr(_v23_exc) + "\\n\\n"
                        + _v23_traceback.format_exc()
                    )
                    _v23_log.write_text(_v23_text, encoding="utf-8")
                    print(_v23_text)
                    print("ABBA V23: traceback written to " + str(_v23_log))
                    raise
'''

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
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            unique.append(p)
    return unique

def abba_py_for_root(root: Path) -> Path | None:
    for p in [
        root / "Lib" / "site-packages" / "abba_python" / "abba.py",
        root / "lib" / "site-packages" / "abba_python" / "abba.py",
        root / "site-packages" / "abba_python" / "abba.py",
    ]:
        if p.exists():
            return p
    try:
        for p in root.rglob("abba.py"):
            if "abba_python" in [x.lower() for x in p.parts]:
                return p
    except Exception:
        pass
    return None

def python_for_root(root: Path) -> Path | None:
    for p in [root / "python.exe", root / "Scripts" / "python.exe", root / "bin" / "python"]:
        if p.exists():
            return p
    return None

def discover(explicit: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in candidate_abba_roots(explicit):
        abba_py = abba_py_for_root(r)
        if abba_py:
            out.append({"root": str(r), "abba_py": str(abba_py), "python": str(python_for_root(r)) if python_for_root(r) else None})
    seen = set()
    unique = []
    for item in out:
        k = item["abba_py"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(item)
    return unique

def patch_visibility(text: str) -> tuple[str, bool, str]:
    if VISIBILITY_MARKER in text:
        return text, True, "already_patched"
    anchor = "    AtlasChooserCommand = jimport('ch.epfl.biop.atlas.scijava.AtlasChooserCommand')"
    if anchor in text:
        return text.replace(anchor, visibility_block() + anchor, 1), True, "patched"
    return text, False, "visibility_anchor_not_found"

def patch_debug(text: str) -> tuple[str, bool, str]:
    if DEBUG_MARKER in text:
        return text, True, "already_patched"
    start = text.find("                bg_atlas = BrainGlobeAtlas(atlas_name)")
    if start < 0:
        return text, False, "debug_start_anchor_not_found"
    end_marker = "                ij.object().addObject(atlas, atlas_name)  # store it in java's object service"
    end = text.find(end_marker, start)
    if end < 0:
        return text, False, "debug_end_anchor_not_found"
    end = text.find("\n", end)
    if end < 0:
        end = len(text)
    else:
        end += 1
    return text[:start] + debug_block() + text[end:], True, "patched"

def patch_file(abba_py: Path, dry_run: bool = False) -> dict[str, Any]:
    result = {"abba_py": str(abba_py), "exists": abba_py.exists(), "patched": False, "backup": None, "visibility_status": None, "debug_status": None, "error": None}
    if not abba_py.exists():
        result["error"] = "abba.py not found"
        return result
    text = abba_py.read_text(encoding="utf-8", errors="replace")
    text2, okv, sv = patch_visibility(text)
    text3, okd, sd = patch_debug(text2)
    result["visibility_status"] = sv
    result["debug_status"] = sd
    if not okv:
        result["error"] = sv
        return result
    if not okd:
        result["error"] = sd
        return result
    changed = text3 != text
    if dry_run:
        result["patched"] = True
        result["dry_run"] = True
        result["would_change"] = changed
        return result
    if changed:
        backup = abba_py.with_name(f"abba.py.backup_v23_{now_stamp()}")
        shutil.copy2(abba_py, backup)
        abba_py.write_text(text3, encoding="utf-8")
        result["backup"] = str(backup)
    result["patched"] = True
    result["changed"] = changed
    return result

def run_probe(python_exe: str | None) -> dict[str, Any]:
    if not python_exe:
        return {"attempted": False, "error": "no python executable found"}
    cmd = [python_exe, "-c", "from brainglobe_atlasapi.list_atlases import get_downloaded_atlases; from brainglobe_atlasapi import config; print('BRAINGLOBE_DIR=' + str(config.get_brainglobe_dir())); print('DOWNLOADED=' + repr(get_downloaded_atlases()))"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {"attempted": True, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except Exception as exc:
        return {"attempted": True, "error": repr(exc)}

def write_reports(report: dict[str, Any]) -> None:
    rd = report_dir()
    (rd / "v23_abba_debug_patch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["V23 ABBA debug patch report", "=" * 72, f"Generated: {report['generated_at']}", f"Passed: {report['passed']}", "", "Patch results:"]
    for pr in report.get("patch_results", []):
        lines.append(json.dumps(pr, indent=2, ensure_ascii=False))
    lines += ["", "Python probes:"]
    for probe in report.get("python_probes", []):
        lines.append(json.dumps(probe, indent=2, ensure_ascii=False))
    lines += ["", "After patching:", "- Restart ABBA/Fiji completely.", "- Try opening paxinos_watson_rat_40um.", "- If it fails, read:", r"  C:\Users\<USER>\abba_paxinos_debug\paxinos_watson_rat_40um_abba_error.log"]
    txt = "\n".join(lines)
    (rd / "v23_abba_debug_patch_report.txt").write_text(txt, encoding="utf-8")
    (rd / "v17_abba_visibility_patch_report.txt").write_text(txt, encoding="utf-8")
    final = ["V23 FINAL STATUS", "=" * 72, f"PASSED: {report['passed']}", ""]
    final.append("ABBA visibility/debug patch applied or already present." if report["passed"] else "Patch failed. Review reports/v23_abba_debug_patch_report.txt")
    (rd / "v23_final_status.txt").write_text("\n".join(final), encoding="utf-8")

def print_table(report: dict[str, Any]) -> None:
    if Console is None or Table is None:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    console = Console()
    table = Table(title="V23 ABBA debug patch")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Installations found", str(len(report.get("installations", []))))
    table.add_row("Patch attempts", str(len(report.get("patch_results", []))))
    table.add_row("Passed", str(report["passed"]))
    for pr in report.get("patch_results", []):
        table.add_row("visibility", str(pr.get("visibility_status")))
        table.add_row("debug", str(pr.get("debug_status")))
        table.add_row("error", str(pr.get("error")))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {report_dir() / 'v23_final_status.txt'}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--abba-root", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    installations = discover(args.abba_root)
    selected = installations if args.all else installations[:1]
    patch_results = []
    probes = []
    for inst in selected:
        patch_results.append(patch_file(Path(inst["abba_py"]), dry_run=args.dry_run))
        probes.append({"root": inst["root"], "probe": run_probe(inst.get("python"))})
    passed = bool(selected) and all(pr.get("patched") and not pr.get("error") for pr in patch_results)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "explicit_abba_root": args.abba_root, "dry_run": args.dry_run, "installations": installations, "selected_installations": selected, "patch_results": patch_results, "python_probes": probes, "passed": passed}
    write_reports(report)
    print_table(report)
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
