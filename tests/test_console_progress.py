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


class ResilienceTests(unittest.TestCase):
    """A defect in this cosmetic layer must not be able to fail a build.

    The wrapper's exit code is what run_builder.bat branches on, and the filter
    will first run on machines nobody can debug from here.
    """

    @staticmethod
    def _run_with_broken_collapser(exit_code: int) -> subprocess.CompletedProcess:
        folder = Path(tempfile.mkdtemp())
        child = folder / "child.py"
        child.write_text(
            "import sys\n"
            "for i in range(20):\n"
            "    print(f'Reading first plane of (source {i})')\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8")
        # Break the collapser from outside, the way an unforeseen defect would.
        driver = folder / "driver.py"
        driver.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from src import console_progress as cp\n"
            "def explode(self, text):\n"
            "    raise RuntimeError('boom')\n"
            "cp.Collapser.line = explode\n"
            "raise SystemExit(cp.main(["
            "'--label', 'step', '--log', " + repr(str(folder / "out.log")) + ","
            " '--', sys.executable, " + repr(str(child)) + "]))\n",
            encoding="utf-8")
        return subprocess.run([sys.executable, str(driver)],
                              capture_output=True, text=True)

    def test_a_crash_in_the_filter_keeps_a_passing_build_passing(self):
        completed = self._run_with_broken_collapser(0)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("CONSOLE_FILTER", completed.stderr)

    def test_a_crash_in_the_filter_still_reports_a_real_failure(self):
        self.assertEqual(self._run_with_broken_collapser(4).returncode, 4)

    def test_non_ascii_output_does_not_break_the_run(self):
        # A user profile with an umlaut is ordinary on the machines this has to
        # run on, and the console code page there is not UTF-8.
        folder = Path(tempfile.mkdtemp())
        child = folder / "child.py"
        child.write_text(
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "print('Reading first plane of (C:/Users/M\u00fcller/plane \u00b5m)')\n",
            encoding="utf-8")
        log = folder / "out.log"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "src" / "console_progress.py"),
             "--label", "step", "--log", str(log), "--", sys.executable, str(child)],
            capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("M\u00fcller", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
