from __future__ import annotations
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src import console_progress as cp

ROOT = Path(__file__).resolve().parents[1]


def collapse(lines: list[str], total: int | None = 588) -> list[str]:
    """Feed lines through the collapser and return what reached the console."""
    stream = io.StringIO()
    collapser = cp.Collapser("step", stream, interactive=False, total=total)
    for line in lines:
        collapser.line(line)
    collapser.finish()
    return [text for text in stream.getvalue().splitlines() if text]


class CollapseTests(unittest.TestCase):
    """The per-slice chatter is collapsed; everything else stays readable."""

    def test_per_source_noise_becomes_one_progress_line(self):
        out = collapse([f"Reading first plane of (source {i})" for i in range(588)])
        self.assertEqual(len(out), 1)
        self.assertIn("588/588", out[0])

    def test_a_logger_prefix_does_not_defeat_the_pattern(self):
        # The jars print through several paths; anchoring the patterns to the
        # start of the line would put the spam straight back on the console.
        out = collapse([f"[INFO] ch.epfl.biop: Action registered in observer: Slice {i}"
                        for i in range(900)])
        self.assertEqual(len(out), 1)
        self.assertIn("900", out[0])

    def test_unrecognised_lines_pass_through(self):
        out = collapse(["Opening ABBA state", "Reading first plane of (source 0)",
                        "State loaded: 588 slices"])
        self.assertIn("Opening ABBA state", out)
        self.assertIn("State loaded: 588 slices", out)

    def test_a_failure_matching_a_noise_pattern_is_still_shown(self):
        # "Action [" is deliberately generous, so the signal-word guard decides.
        line = "Action [RegisterSliceAction on Slice 17] failed: no landmarks"
        self.assertIn(line, collapse([line]))

    def test_no_denominator_means_no_percentage(self):
        out = collapse(["Reading first plane of (x)"] * 10, total=None)
        self.assertEqual(len(out), 1)
        self.assertIn("10 steps", out[0])
        self.assertNotIn("#", out[0])

    def test_switching_noise_kind_closes_the_previous_line(self):
        out = collapse(["Action registered in observer: Slice 1",
                        "Reading first plane of (source 1)"])
        self.assertEqual(len(out), 2)


class DenominatorTests(unittest.TestCase):
    def test_denominator_comes_from_the_pinned_manifest(self):
        # The same file that pins the planes states their count, so the bar
        # cannot drift from what is exported.
        self.assertEqual(cp.plane_total(), 588)


class ExitCodeTests(unittest.TestCase):
    """The batch branches on ERRORLEVEL, so a cosmetic filter must be transparent."""

    @staticmethod
    def _run(exit_code: int) -> int:
        child = Path(tempfile.mkdtemp()) / "child.py"
        child.write_text(
            "import sys\n"
            "print('Reading first plane of (source 0)')\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "src" / "console_progress.py"),
             "--label", "step", "--log", str(child.parent / "out.log"),
             "--", sys.executable, str(child)],
            capture_output=True, text=True)
        return completed.returncode

    def test_success_is_reported_as_success(self):
        self.assertEqual(self._run(0), 0)

    def test_child_failure_code_is_propagated(self):
        self.assertEqual(self._run(3), 3)


class LogTests(unittest.TestCase):
    def test_every_line_is_logged_even_when_collapsed(self):
        folder = Path(tempfile.mkdtemp())
        child = folder / "child.py"
        child.write_text(
            "for i in range(50):\n"
            "    print(f'Reading first plane of (source {i})')\n",
            encoding="utf-8")
        log = folder / "out.log"
        subprocess.run(
            [sys.executable, str(ROOT / "src" / "console_progress.py"),
             "--label", "step", "--log", str(log), "--", sys.executable, str(child)],
            capture_output=True, text=True, check=True)
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 50)


if __name__ == "__main__":
    unittest.main()
