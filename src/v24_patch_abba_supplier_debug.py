from __future__ import annotations
import argparse, json, os, shutil, subprocess
from datetime import datetime
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
except Exception:
    Console = None
    Table = None

VISIBILITY_MARKER = "# V17 local BrainGlobe atlas visibility patch"
SUPPLIER_MARKER = "# V24 AtlasSupplier.get supplier-level debug patch"

def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def repo_root():
    return Path(__file__).resolve().parents[1]

def reports_dir():
    p = repo_root() / "reports"
    p.mkdir(exist_ok=True)
    return p

def visibility_block():
    lines = [
        f"    {VISIBILITY_MARKER}",
        "    try:",
        "        from brainglobe_atlasapi.list_atlases import (",
        "            get_downloaded_atlases as _v17_get_downloaded_atlases,",
        "            get_local_atlas_version as _v17_get_local_atlas_version,",
        "        )",
        "        for _v17_local_atlas in _v17_get_downloaded_atlases():",
        "            if _v17_local_atlas not in available_atlases:",
        "                available_atlases[_v17_local_atlas] = _v17_get_local_atlas_version(_v17_local_atlas)",
        "                print('ABBA V17: registered local BrainGlobe atlas ' + str(_v17_local_atlas))",
        "    except Exception as _v17_exc:",
        "        print('ABBA V17: could not merge local BrainGlobe atlases: ' + repr(_v17_exc))",
        "",
    ]
    return "\n".join(lines) + "\n"

def roots(explicit=None):
    out = []
    if explicit:
        out.append(Path(explicit).expanduser().resolve())
    if os.environ.get("ABBA_PYTHON_ROOT"):
        out.append(Path(os.environ["ABBA_PYTHON_ROOT"]).expanduser().resolve())
    home = Path.home()
    for pat in ["abba-python-*", "abba_python-*", "ABBA*"]:
        out += [p.resolve() for p in home.glob(pat) if p.is_dir()]
    cwd = Path.cwd().resolve()
    out.append(cwd)
    out += list(cwd.parents)
    seen, final = set(), []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            final.append(p)
    return final

def abba_py(root):
    for p in [
        root / "Lib/site-packages/abba_python/abba.py",
        root / "lib/site-packages/abba_python/abba.py",
        root / "site-packages/abba_python/abba.py",
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

def pyexe(root):
    for p in [root / "python.exe", root / "Scripts/python.exe", root / "bin/python"]:
        if p.exists():
            return p
    return None

def discover(explicit=None):
    items = []
    for r in roots(explicit):
        ap = abba_py(r)
        if ap:
            items.append({"root": str(r), "abba_py": str(ap), "python": str(pyexe(r)) if pyexe(r) else None})
    seen, out = set(), []
    for item in items:
        key = item["abba_py"].lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

def patch_visibility(text):
    if VISIBILITY_MARKER in text:
        return text, True, "already_patched"
    anchor = "    AtlasChooserCommand = jimport('ch.epfl.biop.atlas.scijava.AtlasChooserCommand')"
    if anchor in text:
        return text.replace(anchor, visibility_block() + anchor, 1), True, "patched"
    return text, False, "visibility_anchor_not_found"

def patch_supplier(text):
    if SUPPLIER_MARKER in text:
        return text, True, "already_patched"
    needle = "        def get(self):\n"
    idx = text.find(needle)
    if idx < 0:
        return text, False, "def_get_not_found"
    body_start = idx + len(needle)
    next_def = text.find("\n        def ", body_start)
    next_class = text.find("\nclass ", body_start)
    ends = [x for x in [next_def, next_class] if x >= 0]
    body_end = min(ends) if ends else len(text)
    body = text[body_start:body_end]
    if "Abba.opened_atlases" not in body or "atlas_name" not in body:
        return text, False, "unexpected_get_body"
    inner = []
    for line in body.splitlines():
        if line.startswith("            "):
            inner.append("                " + line[12:])
        elif not line.strip():
            inner.append("")
        else:
            inner.append("                " + line.lstrip())
    wrapper_lines = [
        f"            {SUPPLIER_MARKER}",
        "            import traceback as _v24_traceback",
        "            import os as _v24_os",
        "            from pathlib import Path as _v24_Path",
        "            _v24_log_dir = _v24_Path(_v24_os.environ.get('USERPROFILE', '.')) / 'abba_paxinos_debug'",
        "            _v24_log_dir.mkdir(parents=True, exist_ok=True)",
        "            _v24_log = _v24_log_dir / (str(atlas_name) + '_supplier_get_error.log')",
        "            _v24_steps = ['entered AtlasSupplier.get', 'atlas_name=' + str(atlas_name), 'ij=' + repr(ij)]",
        "            try:",
    ]
    wrapper_lines.extend(inner)
    wrapper_lines.extend([
        "                _v24_steps.append('returning atlas=' + repr(atlas))",
        "                _v24_log.write_text('\\n'.join(_v24_steps), encoding='utf-8')",
        "                return atlas",
        "            except Exception as _v24_exc:",
        "                _v24_steps.append('exception=' + repr(_v24_exc))",
        "                _v24_steps.append('')",
        "                _v24_steps.append(_v24_traceback.format_exc())",
        "                _v24_log.write_text('\\n'.join(_v24_steps), encoding='utf-8')",
        "                print('ABBA V24: supplier traceback written to ' + str(_v24_log))",
        "                print('\\n'.join(_v24_steps))",
        "                raise",
        "",
    ])
    wrapper = "\n".join(wrapper_lines)
    return text[:body_start] + wrapper + text[body_end:], True, "patched"

def patch_file(path, dry=False):
    path = Path(path)
    res = {"abba_py": str(path), "exists": path.exists(), "patched": False, "backup": None, "visibility_status": None, "supplier_status": None, "error": None}
    if not path.exists():
        res["error"] = "not_found"
        return res
    text = path.read_text(encoding="utf-8", errors="replace")
    t2, okv, sv = patch_visibility(text)
    t3, oks, ss = patch_supplier(t2)
    res["visibility_status"] = sv
    res["supplier_status"] = ss
    if not okv:
        res["error"] = sv
        return res
    if not oks:
        res["error"] = ss
        return res
    changed = t3 != text
    if dry:
        res.update({"patched": True, "dry_run": True, "would_change": changed})
        return res
    if changed:
        backup = path.with_name("abba.py.backup_v24_" + stamp())
        shutil.copy2(path, backup)
        path.write_text(t3, encoding="utf-8")
        res["backup"] = str(backup)
    res.update({"patched": True, "changed": changed})
    return res

def probe(python):
    if not python:
        return {"attempted": False, "error": "no python executable"}
    cmd = [python, "-c", "from brainglobe_atlasapi.list_atlases import get_downloaded_atlases; from brainglobe_atlasapi import config; print('BRAINGLOBE_DIR='+str(config.get_brainglobe_dir())); print('DOWNLOADED='+repr(get_downloaded_atlases()))"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {"attempted": True, "returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except Exception as e:
        return {"attempted": True, "error": repr(e)}

def write_report(report):
    rd = reports_dir()
    (rd / "v24_abba_supplier_debug_patch_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["V24 ABBA supplier debug patch report", "=" * 72, f"Generated: {report['generated_at']}", f"Passed: {report['passed']}", "", "Patch results:"]
    for r in report["patch_results"]:
        lines.append(json.dumps(r, indent=2, ensure_ascii=False))
    lines += ["", "Python probes:"]
    for p in report["python_probes"]:
        lines.append(json.dumps(p, indent=2, ensure_ascii=False))
    lines += ["", "After patching: restart ABBA/Fiji and try paxinos_watson_rat_40um.", r"If it fails, read: C:\Users\<USER>\abba_paxinos_debug\paxinos_watson_rat_40um_supplier_get_error.log"]
    txt = "\n".join(lines)
    (rd / "v24_abba_supplier_debug_patch_report.txt").write_text(txt, encoding="utf-8")
    (rd / "v24_final_status.txt").write_text("V24 FINAL STATUS\n" + "=" * 72 + f"\nPASSED: {report['passed']}\n", encoding="utf-8")

def print_table(report):
    if Console and Table:
        c = Console(); t = Table(title="V24 ABBA supplier debug patch")
        t.add_column("Check"); t.add_column("Value")
        t.add_row("Installations found", str(len(report["installations"])))
        t.add_row("Patch attempts", str(len(report["patch_results"])))
        t.add_row("Passed", str(report["passed"]))
        for r in report["patch_results"]:
            t.add_row("visibility", str(r.get("visibility_status")))
            t.add_row("supplier", str(r.get("supplier_status")))
            t.add_row("error", str(r.get("error")))
        c.print(t)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--abba-root", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    installs = discover(a.abba_root)
    selected = installs if a.all else installs[:1]
    results, probes = [], []
    for inst in selected:
        results.append(patch_file(inst["abba_py"], a.dry_run))
        probes.append({"root": inst["root"], "probe": probe(inst.get("python"))})
    passed = bool(selected) and all(r.get("patched") and not r.get("error") for r in results)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), "installations": installs, "selected_installations": selected, "patch_results": results, "python_probes": probes, "passed": passed}
    write_report(report)
    print_table(report)
    return 0 if passed else 1

if __name__ == "__main__":
    raise SystemExit(main())
