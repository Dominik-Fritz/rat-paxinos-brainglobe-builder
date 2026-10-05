#!/usr/bin/env python3
"""Print the recorded Ch03 visual-parity state: PASSED, FAILED or PENDING.

run_builder.bat reads this token because it cannot parse JSON. The exit code is
always 0; a missing report simply means PENDING.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parity_state(root: Path) -> str:
    report = root / "reports" / "ch03_nissl" / "ch03_nissl_report.json"
    try:
        reconstruction = json.loads(report.read_text(encoding="utf-8")).get(
            "abba_reconstruction", {}
        )
    except (OSError, ValueError):
        return "PENDING"
    status = reconstruction.get("visual_parity_status")
    if status == "passed" and reconstruction.get("release_eligible") is True:
        return "PASSED"
    if status == "failed":
        return "FAILED"
    return "PENDING"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)
    print(parity_state(Path(args.root).resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
