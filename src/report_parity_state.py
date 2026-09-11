#!/usr/bin/env python3
"""Print the recorded Ch03 visual-parity state as a single token.

run_builder.bat used to announce "release eligibility remains false" after every
successful Nissl render, regardless of what had actually been recorded. Once a
reviewer had signed off, the console contradicted the report it had just
written. The batch has no JSON reader, so it asks here instead.

Output is exactly one of:

    PASSED   visual parity recorded as passed and the atlas is release eligible
    FAILED   visual parity recorded as failed
    PENDING  no decision applies to this reconstruction, or no report exists

Exit status is always 0: an absent or unreadable report is a legitimate PENDING,
not an error, and must never fail a build.
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
