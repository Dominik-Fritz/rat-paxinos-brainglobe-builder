from __future__ import annotations

"""Placeholder for BrainGlobe atlas generation.

This script is intentionally not active yet. Do not build the atlas before:
1. Input geometry is validated.
2. Reference volume selection is confirmed.
3. Paxinos labels are parsed.
4. structures.json hierarchy strategy is selected.
5. Orientation and left-right/anterior-posterior sanity checks are done.

Yes, this is boring. Boring prevents the amygdala from moving into the thalamus.
"""


def main() -> int:
    print("Atlas generation is not enabled in this scaffold.")
    print("Run run_builder.bat and review reports/input_inspection_report.txt first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
