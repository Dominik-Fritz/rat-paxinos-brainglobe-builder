from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import os
import re
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from utils_paths import (
    ATLAS_NAME,
    OUTPUT_DIR,
    REPORTS_DIR,
    official_candidate_folder,
)

console = Console()


def safe_read_text(path: Path, max_chars: int = 250000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars]
    except Exception as exc:
        return f"<<READ ERROR: {exc!r}>>"


def module_file(module_name: str) -> Path | None:
    try:
        module = importlib.import_module(module_name)
        return Path(inspect.getfile(module)).resolve()
    except Exception:
        return None


def inspect_brainglobe_source() -> dict[str, Any]:
    modules = [
        "brainglobe_atlasapi",
        "brainglobe_atlasapi.bg_atlas",
        "brainglobe_atlasapi.config",
        "brainglobe_atlasapi.utils",
    ]
    result: dict[str, Any] = {
        "modules": {},
        "valid_atlas_logic": {},
        "download_logic_snippets": {},
        "cache_terms": {},
    }

    for mod in modules:
        path = module_file(mod)
        result["modules"][mod] = str(path) if path else None
        if not path:
            continue
        text = safe_read_text(path)
        terms = {}
        for term in [
            "last_versions",
            "last_versions.conf",
            "valid atlas",
            "valid_atlas",
            "remote_url",
            "download",
            "tar.gz",
            "DEFAULT_PATH",
            "config",
            "atlas_name",
            "BrainGlobeAtlas",
        ]:
            terms[term] = text.find(term)
        result["cache_terms"][mod] = terms

        snippets = []
        for pattern in [
            "valid atlas",
            "last_versions",
            "check_gin_status",
            "BrainGlobeAtlas",
            "atlas_name",
        ]:
            idx = text.find(pattern)
            if idx >= 0:
                start = max(0, idx - 1200)
                end = min(len(text), idx + 2200)
                snippets.append({"pattern": pattern, "snippet": text[start:end]})
        result["download_logic_snippets"][mod] = snippets

    # Try AST extraction for BrainGlobeAtlas.__init__
    try:
        bg_path = module_file("brainglobe_atlasapi.bg_atlas")
        if bg_path:
            source = safe_read_text(bg_path, max_chars=500000)
            tree = ast.parse(source)
            classes = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "BrainGlobeAtlas":
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                            classes.append({
                                "class": "BrainGlobeAtlas",
                                "method": "__init__",
                                "lineno": item.lineno,
                                "end_lineno": getattr(item, "end_lineno", None),
                            })
            result["valid_atlas_logic"]["BrainGlobeAtlas_init_ast"] = classes
    except Exception as exc:
        result["valid_atlas_logic"]["ast_error"] = repr(exc)

    return result


def find_user_brainglobe_files() -> dict[str, Any]:
    home = Path.home()
    roots = [
        home / ".brainglobe",
        home / ".config" / "brainglobe",
        home / ".cache" / "brainglobe",
        home / "brainglobe_workingdir",
        home / "brainglobe",
    ]

    result: dict[str, Any] = {"roots": {}, "interesting_files": []}
    interesting_patterns = [
        "last_versions",
        "versions",
        "atlas",
        "config",
        ".conf",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    ]

    for root in roots:
        root_info = {"exists": root.exists(), "files": []}
        if root.exists():
            try:
                count = 0
                for path in root.rglob("*"):
                    if count > 300:
                        root_info["files"].append("<<TRUNCATED AFTER 300 ITEMS>>")
                        break
                    if path.is_file():
                        rel = str(path.relative_to(root))
                        size = path.stat().st_size
                        root_info["files"].append({"relative": rel, "size": size})
                        low = path.name.lower()
                        if any(pat in low for pat in interesting_patterns):
                            preview = safe_read_text(path, max_chars=20000) if size < 2_000_000 else "<<too large for preview>>"
                            result["interesting_files"].append({
                                "path": str(path),
                                "size": size,
                                "preview": preview,
                            })
                        count += 1
            except Exception as exc:
                root_info["error"] = repr(exc)
        result["roots"][str(root)] = root_info
    return result


def parse_last_versions_from_text(text: str) -> dict[str, str]:
    pairs = {}
    # Try common config-like formats:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for sep in ["=", ":", ","]:
            if sep in stripped:
                left, right = stripped.split(sep, 1)
                left = left.strip().strip('"').strip("'")
                right = right.strip().strip('"').strip("'")
                if left and right and re.search(r"[A-Za-z]", left):
                    pairs[left] = right
                break
    return pairs


def infer_registry_format(user_files: dict[str, Any]) -> dict[str, Any]:
    inference = {
        "last_versions_candidates": [],
        "atlas_name_occurrences": [],
        "likely_registry_files": [],
        "parsed_version_maps": [],
    }

    for item in user_files.get("interesting_files", []):
        path = item["path"]
        preview = item.get("preview", "")
        low = path.lower()
        if "last_versions" in low or "versions" in low:
            inference["last_versions_candidates"].append(path)
            parsed = parse_last_versions_from_text(preview)
            if parsed:
                inference["parsed_version_maps"].append({"path": path, "entries_sample": dict(list(parsed.items())[:50]), "entry_count": len(parsed)})
        if "atlas" in low or "config" in low:
            inference["likely_registry_files"].append(path)
        if ATLAS_NAME in preview:
            inference["atlas_name_occurrences"].append(path)

    return inference


def optional_reference_atlas_probe(reference_atlas: str | None) -> dict[str, Any]:
    result = {
        "requested": bool(reference_atlas),
        "reference_atlas": reference_atlas,
        "success": False,
        "exception": None,
        "traceback": None,
        "object_summary": {},
        "derived_paths": {},
    }
    if not reference_atlas:
        return result

    try:
        from brainglobe_atlasapi import BrainGlobeAtlas
        atlas = BrainGlobeAtlas(reference_atlas)
        result["success"] = True
        result["object_summary"] = {
            "class": atlas.__class__.__name__,
            "repr": repr(atlas),
        }
        for attr in [
            "atlas_name",
            "name",
            "root_dir",
            "atlas_dir",
            "local_full_name",
            "resolution",
            "orientation",
            "metadata",
        ]:
            try:
                value = getattr(atlas, attr)
                result["object_summary"][attr] = str(value)
            except Exception:
                pass

        for maybe_path in ["root_dir", "atlas_dir"]:
            try:
                p = Path(getattr(atlas, maybe_path))
                if p.exists():
                    result["derived_paths"][maybe_path] = {
                        "path": str(p),
                        "children": [str(c.name) for c in p.iterdir()][:100],
                    }
            except Exception:
                pass
    except Exception as exc:
        result["exception"] = repr(exc)
        result["traceback"] = traceback.format_exc()
    return result


def propose_next_steps(source_probe: dict[str, Any], user_files: dict[str, Any], inference: dict[str, Any], reference_probe: dict[str, Any]) -> list[str]:
    steps = []
    if inference.get("last_versions_candidates"):
        steps.append("Patch or extend the discovered last_versions registry with paxinos_watson_rat_40um.")
        steps.append("Create a versioned cache folder matching the discovered installed-atlas pattern.")
    else:
        steps.append("No clear last_versions registry was found; inspect brainglobe_atlasapi source around BrainGlobeAtlas.__init__ and valid atlas checks.")
    if reference_probe.get("success"):
        steps.append("Clone the folder structure of the loaded reference atlas for the Paxinos candidate.")
    else:
        steps.append("Install or load one small official BrainGlobe atlas, then rerun V9 with --reference-atlas <name> to clone its structure.")
    steps.append("Implement V10 as exact registry injection based on the discovered registry/cache files.")
    return steps


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-probe", action="store_true", help="Run full BrainGlobe registry reverse-engineering diagnostics.")
    parser.add_argument("--reference-atlas", default=None, help="Optional official atlas name to inspect after loading.")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.registry_probe:
        source_probe = inspect_brainglobe_source()
        user_files = find_user_brainglobe_files()
        inference = infer_registry_format(user_files)
        reference_probe = optional_reference_atlas_probe(args.reference_atlas)
    else:
        source_probe = {"skipped": True}
        user_files = {"skipped": True}
        inference = {"skipped": True}
        reference_probe = {"requested": False, "skipped": True}

    candidate_folder = official_candidate_folder()
    candidate_exists = candidate_folder.exists()

    next_steps = propose_next_steps(source_probe, user_files, inference, reference_probe) if args.registry_probe else [
        "Run menu option 6 or 7 to execute V9 registry diagnostics."
    ]

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "atlas_name": ATLAS_NAME,
        "registry_probe_requested": args.registry_probe,
        "candidate_folder": str(candidate_folder),
        "candidate_exists": candidate_exists,
        "source_probe": source_probe,
        "user_brainglobe_files": user_files,
        "registry_inference": inference,
        "reference_atlas_probe": reference_probe,
        "next_steps": next_steps,
        "passed": bool(args.registry_probe and candidate_exists),
    }

    (REPORTS_DIR / "v9_registry_probe_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Separate source report for easier human reading
    source_lines = [
        "V9 BrainGlobe source probe",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        "",
        "Module paths:",
    ]
    if isinstance(source_probe, dict):
        for mod, path in source_probe.get("modules", {}).items():
            source_lines.append(f"- {mod}: {path}")
        source_lines.append("")
        source_lines.append("Important source snippets:")
        for mod, snippets in source_probe.get("download_logic_snippets", {}).items():
            source_lines.append(f"[{mod}]")
            for snip in snippets[:5]:
                source_lines.append(f"--- pattern: {snip.get('pattern')} ---")
                source_lines.append(str(snip.get("snippet", ""))[:2500])
                source_lines.append("")
    (REPORTS_DIR / "v9_brainglobe_source_probe.txt").write_text("\n".join(source_lines), encoding="utf-8")

    lines = [
        "V9 BrainGlobe registry probe report",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"Atlas: {ATLAS_NAME}",
        f"Registry probe requested: {args.registry_probe}",
        f"Candidate folder exists: {candidate_exists}",
        "",
        "Discovered registry/cache files:",
    ]
    if isinstance(inference, dict) and not inference.get("skipped"):
        lines.append("last_versions candidates:")
        for p in inference.get("last_versions_candidates", []):
            lines.append(f"- {p}")
        lines.append("")
        lines.append("likely registry files:")
        for p in inference.get("likely_registry_files", [])[:100]:
            lines.append(f"- {p}")
        lines.append("")
        lines.append("parsed version maps:")
        for m in inference.get("parsed_version_maps", []):
            lines.append(f"- {m.get('path')} entries={m.get('entry_count')}")
            sample = m.get("entries_sample", {})
            for k, v in list(sample.items())[:15]:
                lines.append(f"  {k}: {v}")
    else:
        lines.append("- skipped")

    lines.append("")
    lines.append("Reference atlas probe:")
    for k, v in reference_probe.items():
        if k != "traceback":
            lines.append(f"- {k}: {v}")
    if reference_probe.get("traceback"):
        lines.append("")
        lines.append("Reference traceback:")
        lines.append(reference_probe["traceback"])

    lines.append("")
    lines.append("Next steps:")
    for step in next_steps:
        lines.append(f"- {step}")

    (REPORTS_DIR / "v9_registry_probe_report.txt").write_text("\n".join(lines), encoding="utf-8")

    final_lines = [
        "V9 FINAL STATUS",
        "=" * 72,
        f"PASSED: {report['passed']}",
        f"Registry probe requested: {args.registry_probe}",
        f"Candidate folder exists: {candidate_exists}",
        "",
        "Result:",
    ]
    if args.registry_probe:
        final_lines.append("- Registry/source diagnostics completed.")
        if inference.get("last_versions_candidates"):
            final_lines.append("- Found candidate version/registry files. V10 should patch/extend these carefully.")
        else:
            final_lines.append("- No obvious last_versions registry was found in user files. Use source probe to implement exact logic.")
        if reference_probe.get("success"):
            final_lines.append("- Reference atlas loaded successfully. Its folder structure can be cloned.")
        elif args.reference_atlas:
            final_lines.append("- Reference atlas probe failed. See report.")
        else:
            final_lines.append("- No reference atlas was requested.")
    else:
        final_lines.append("- V9 was skipped. Run menu option 6 or 7.")

    (REPORTS_DIR / "v9_final_status.txt").write_text("\n".join(final_lines), encoding="utf-8")

    table = Table(title="V9 BrainGlobe registry probe")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Requested", str(args.registry_probe))
    table.add_row("Candidate exists", str(candidate_exists))
    if isinstance(inference, dict) and not inference.get("skipped"):
        table.add_row("last_versions candidates", str(len(inference.get("last_versions_candidates", []))))
        table.add_row("registry files", str(len(inference.get("likely_registry_files", []))))
    table.add_row("Reference probe success", str(reference_probe.get("success")))
    table.add_row("Passed", str(report["passed"]))
    console.print(table)
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v9_final_status.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v9_registry_probe_report.txt'}")
    console.print(f"[green]Wrote:[/green] {REPORTS_DIR / 'v9_brainglobe_source_probe.txt'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
