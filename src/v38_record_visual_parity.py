#!/usr/bin/env python3
"""Record the human visual-parity decision for the installed Ch03 channel.

Visual validation cannot be automated, so no build step sets it. This entry
point exists to write that decision down deliberately, bind it to the exact
reconstruction it was made for, and push it into the atlas metadata without
re-rendering.

The decision is stored in resources/optional_ch03/visual_parity_approval.json
naming the reconstruction's content hash. A later build whose voxels differ
produces a different hash, so the approval stops applying and the status falls
back to pending rather than being inherited silently.

Typical use, after inspecting the channel under the Paxinos contours in ABBA:

    .venv\\Scripts\\python.exe src\\v38_record_visual_parity.py passed \\
        --reviewer "Dominik Fritz" --notes "AP 10-597 checked, contours symmetric"

`--status failed` is equally recordable; the install path then refuses to keep
the channel, which is the intended outcome of a failed review.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ch03_nissl_pipeline as pipeline
import native_abba_renderer as renderer


def _active_content_sha256() -> str:
    if not pipeline.ACTIVE_PATH.is_file():
        raise SystemExit(
            f"ERROR: no rendered channel at {pipeline.ACTIVE_PATH}. Run a build first."
        )
    return renderer.content_sha256_of(np.asarray(tifffile.imread(pipeline.ACTIVE_PATH)))


def _reported_content_sha256() -> str | None:
    if not pipeline.REPORT_JSON.is_file():
        return None
    report = json.loads(pipeline.REPORT_JSON.read_text(encoding="utf-8"))
    return report.get("abba_reconstruction", {}).get("output_content_sha256")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("status", choices=("passed", "failed"),
                        help="the outcome of your visual inspection in ABBA")
    parser.add_argument("--reviewer", required=True, help="who performed the inspection")
    parser.add_argument("--notes", default="", help="what was checked, in your words")
    parser.add_argument("--expect-content-sha256", default=None,
                        help="refuse unless the rendered channel has this content hash")
    parser.add_argument("--apply", action="store_true",
                        help="also write the decision into the installed atlas metadata")
    args = parser.parse_args(argv)

    # Hash the file that is actually on disk. Trusting the report's own figure
    # would let a stale report approve a channel it does not describe.
    observed = _active_content_sha256()
    reported = _reported_content_sha256()
    if reported and reported != observed:
        raise SystemExit(
            "ERROR: the rendered channel does not match the last report.\n"
            f"  on disk : {observed}\n  report  : {reported}\n"
            "Re-run the build before recording a decision."
        )
    if args.expect_content_sha256 and args.expect_content_sha256 != observed:
        raise SystemExit(
            f"ERROR: expected {args.expect_content_sha256}, rendered channel is {observed}"
        )

    record = {
        "schema_version": 1,
        "visual_parity_status": args.status,
        "output_content_sha256": observed,
        "reviewer": args.reviewer,
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "notes": args.notes,
        "definition": (
            "Human visual validation of the registered Ch03 channel against the Paxinos "
            "contours in ABBA. Applies only to the reconstruction whose content hash is "
            "named above; any build producing different voxels reverts to pending."
        ),
    }
    renderer.VISUAL_PARITY_APPROVAL.parent.mkdir(parents=True, exist_ok=True)
    renderer.VISUAL_PARITY_APPROVAL.write_text(json.dumps(record, indent=2) + "\n",
                                               encoding="utf-8")
    print(f"Recorded visual_parity_status={args.status} for {observed[:16]}...")
    print(f"  {renderer.VISUAL_PARITY_APPROVAL}")

    resolved = renderer.resolve_visual_parity(observed)
    print(f"  resolves to: {resolved['visual_parity_status']}, "
          f"release_eligible={resolved['release_eligible']}")

    if not args.apply:
        print("\nAtlas metadata unchanged. Re-run with --apply to install the decision,")
        print("or it takes effect on the next build.")
        return 0

    if not pipeline.REPORT_JSON.is_file():
        raise SystemExit("ERROR: no Ch03 report to install from; run a build first.")
    report = json.loads(pipeline.REPORT_JSON.read_text(encoding="utf-8"))
    reconstruction = report.get("abba_reconstruction")
    if not reconstruction:
        raise SystemExit("ERROR: the Ch03 report has no abba_reconstruction block.")
    reconstruction.update(resolved)
    # Reuse the transactional install rather than re-rendering: the voxels are
    # unchanged and verified identical above, only the recorded decision moves.
    installed = pipeline.install_channel(reconstruction)
    pipeline.write_report({"abba_reconstruction": reconstruction})
    print(f"Applied to {len(installed)} atlas target(s).")
    for entry in installed:
        print(f"  {entry['atlas']}: visual_parity_status={entry['visual_parity_status']}, "
              f"release_eligible={entry['release_eligible']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
