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
    parser.add_argument("--status", choices=("success", "warnings", "failed"), required=True)
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
    nissl_requested = args.nissl.upper() == "YES"
    ch03_report_path = reports / "ch03_nissl" / "ch03_nissl_report.json"
    ch03_report = (
        json.loads(ch03_report_path.read_text(encoding="utf-8"))
        if nissl_requested and ch03_report_path.is_file() else {}
    )
    import_report = ch03_report.get("ch03_import", {})
    reconstruction_report = ch03_report.get("abba_reconstruction", {})
    if reconstruction_report:
        reconstruction = reconstruction_report.get("reconstruction", {})
        # Normalize the new ABBA reconstruction report to the established
        # summary fields while retaining compatibility with v0.3.0 reports.
        import_report = {
            "stack_order": reconstruction_report.get(
                "stack_order", reconstruction.get("source_direction")
            ),
            "mapped_plane_count": reconstruction.get("mapped_plane_count"),
            "target_sequence_offset": reconstruction_report.get(
                "target_sequence_offset", reconstruction.get("target_sequence_offset")
            ),
            "anterior_edge_policy": reconstruction.get("anterior_edge_policy"),
            "duplicated_anterior_target_ap": reconstruction.get("duplicated_anterior_target_ap"),
            "unused_target_sequence_positions": reconstruction.get("unused_target_sequence_positions"),
        }
    lines = [
        "Rat Paxinos/Watson Atlas Builder - Build Summary",
        "=" * 72,
        f"Status: {'SUCCESS WITH WARNINGS' if args.status == 'warnings' else args.status.upper()}",
        f"Started: {args.started}",
        f"Finished: {datetime.now().isoformat(timespec='seconds')}",
        f"Last stage: {args.stage}",
        "",
        "Build result",
        "-" * 72,
        f"Atlas: {atlas if atlas else 'not located'}",
        f"Paxinos annotation: {'present' if atlas and (atlas / 'annotation.tiff').is_file() else 'not confirmed'}",
        f"Registered Nissl channel: {'present' if nissl_requested and nissl_installed else 'disabled for this build' if not nissl_requested else 'not present'}",
        f"Nissl AP order: {import_report.get('stack_order', 'not recorded')}",
        f"Mapped Nissl planes: {import_report.get('mapped_plane_count', 'not recorded')}",
        f"Target sequence offset: {import_report.get('target_sequence_offset', 'not recorded')}",
        f"Anterior edge policy: {import_report.get('anterior_edge_policy', 'not recorded')}",
        f"Duplicated anterior target AP: {import_report.get('duplicated_anterior_target_ap', 'not recorded')}",
        f"Unused target positions: {import_report.get('unused_target_sequence_positions', 'not recorded')}",
        f"Additional references: {', '.join(refs) if refs else 'none'}",
        f"ABBA visibility patch requested: {args.abba_patch}",
        "",
        "Interpretation",
        "-" * 72,
        "The Paxinos annotation remains authoritative for region assignment.",
        "The registered Waxholm Nissl channel is a non-authoritative visual aid.",
        ("The Nissl channel was reconstructed from the immutable, versioned ABBA state and the "
         "pinned Waxholm source; no pre-rendered v0.3.0 stack was used." if nissl_installed else
         "No reconstructed Nissl channel was installed."),
        "",
        f"Detailed reports: {reports}",
    ]
    destination = reports / "BUILD_SUMMARY.txt"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
