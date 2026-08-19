#!/usr/bin/env python3
"""Write a concise, human-readable completion report for run_builder.bat."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from brainglobe_atlasapi import config


def locate_installed_atlas() -> Path | None:
    home = Path(config.get_brainglobe_dir())
    for name in ("paxinos_watson_rat_40um_v1.0", "paxinos_watson_rat_40um"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--status", choices=("success", "failed"), required=True)
    parser.add_argument("--stage", default="Completed")
    parser.add_argument("--started", default="unknown")
    parser.add_argument("--nissl", default="YES")
    parser.add_argument("--abba-patch", default="YES")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    atlas = locate_installed_atlas()
    metadata: dict = {}
    if atlas and (atlas / "metadata.json").is_file():
        metadata = json.loads((atlas / "metadata.json").read_text(encoding="utf-8"))
    refs = metadata.get("additional_references", [])
    if isinstance(refs, str):
        refs = [refs]
    nissl_installed = bool(atlas and (atlas / "waxholm_anatomy_reference.tiff").is_file())
    ch03_report_path = reports / "ch03_nissl" / "ch03_nissl_report.json"
    ch03_report = (
        json.loads(ch03_report_path.read_text(encoding="utf-8")) if ch03_report_path.is_file() else {}
    )
    import_report = ch03_report.get("ch03_import_imagej_stack", {})
    lines = [
        "Rat Paxinos/Watson Atlas Builder - Build Summary",
        "=" * 72,
        f"Status: {args.status.upper()}",
        f"Started: {args.started}",
        f"Finished: {datetime.now().isoformat(timespec='seconds')}",
        f"Last stage: {args.stage}",
        "",
        "Build result",
        "-" * 72,
        f"Atlas: {atlas if atlas else 'not located'}",
        f"Paxinos annotation: {'present' if atlas and (atlas / 'annotation.tiff').is_file() else 'not confirmed'}",
        f"Registered Nissl channel: {'present' if nissl_installed else 'not present'}",
        f"Nissl AP order: {import_report.get('stack_order', 'not recorded')}",
        f"Mapped Nissl planes: {import_report.get('mapped_plane_count', 'not recorded')}",
        f"Excluded terminal AP plane: {import_report.get('excluded_terminal_fixed_ap', 'not recorded')}",
        f"Excluded sequence endpoint: {import_report.get('excluded_sequence_endpoint', 'not recorded')}",
        f"Additional references: {', '.join(refs) if refs else 'none'}",
        f"ABBA visibility patch requested: {args.abba_patch}",
        "",
        "Interpretation",
        "-" * 72,
        "The Paxinos annotation remains authoritative for region assignment.",
        "The registered Waxholm Nissl channel is a non-authoritative visual aid.",
        "",
        f"Detailed reports: {reports}",
    ]
    destination = reports / "BUILD_SUMMARY.txt"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
