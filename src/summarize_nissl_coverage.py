#!/usr/bin/env python3
"""Create a compact, shareable summary from the large Ch03 coverage report."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def margins(plane: dict) -> dict | None:
    label = plane.get("label_bbox_si_lr")
    signal = plane.get("nissl_signal_bbox_si_lr")
    if not label or not signal:
        return None
    return {
        "si_min_inset": int(signal[0][0] - label[0][0]),
        "si_max_inset": int(label[1][0] - signal[1][0]),
        "lr_min_inset": int(signal[0][1] - label[0][1]),
        "lr_max_inset": int(label[1][1] - signal[1][1]),
    }


def summarize(report: dict, worst_count: int = 12) -> str:
    edge = report.get("ch03_import", {}).get("edge_coverage", {})
    planes = edge.get("planes", [])
    ranked = sorted(planes, key=lambda item: float(item.get("coverage_fraction", 1.0)))
    lines = [
        "Nissl edge coverage - compact summary",
        "=" * 72,
        f"Definition: {edge.get('definition', 'not recorded')}",
        f"Planes: {edge.get('plane_count', len(planes))}",
        f"Coverage min: {edge.get('coverage_fraction_min')}",
        f"Coverage median: {edge.get('coverage_fraction_median')}",
        f"Coverage max: {edge.get('coverage_fraction_max')}",
        f"Pixels modified by diagnostic: {edge.get('pixels_modified')}",
        "",
        f"Worst {min(worst_count, len(ranked))} planes",
        "-" * 72,
    ]
    for plane in ranked[:worst_count]:
        inset = margins(plane)
        lines.append(
            f"AP {plane.get('ap')}: coverage={plane.get('coverage_fraction')} "
            f"label={plane.get('label_pixels')} covered={plane.get('label_pixels_with_nissl_signal')} "
            f"insets(SImin,SImax,LRmin,LRmax)={inset}"
        )
    lines.extend([
        "",
        "Interpretation",
        "-" * 72,
        "Positive inset: visible non-zero Nissl bounding box ends inside label bounds.",
        "Zero inset: bounding-box edge coincides.",
        "Negative inset: Nissl signal extends beyond label bounds.",
        "Coverage uses value > 0 and does not model ABBA contrast/window visibility.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default=None)
    parser.add_argument("--worst", type=int, default=12)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    report_path = Path(args.report).resolve() if args.report else root / "reports" / "ch03_nissl" / "ch03_nissl_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"Ch03 report not found: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    text = summarize(report, max(1, args.worst))
    destination = root / "reports" / "ch03_nissl" / "NISSL_EDGE_COVERAGE_SUMMARY.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Summary: {destination}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
