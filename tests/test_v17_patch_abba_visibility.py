from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from src import v17_patch_abba_visibility as v17


class PatchOutcomeTests(unittest.TestCase):
    """A foreign ABBA copy must not decide this build's status."""

    @staticmethod
    def _write(text: str) -> Path:
        folder = Path(tempfile.mkdtemp())
        target = folder / "abba.py"
        target.write_text(text, encoding="utf-8")
        return target

    def test_file_without_the_patched_function_is_not_an_error(self):
        # A source checkout of a different ABBA layout, e.g. one belonging to an
        # unrelated project that discovery happens to find.
        result = v17.patch_abba_py(self._write("def something_else():\n    pass\n"),
                                   dry_run=True)
        self.assertTrue(result["not_applicable"])
        self.assertIsNone(result["error"])
        self.assertFalse(result["patched"])

    def test_file_without_the_anchor_is_not_an_error(self):
        result = v17.patch_abba_py(
            self._write("def add_brainglobe_atlases(ij):\n    return None\n"), dry_run=True)
        self.assertTrue(result["not_applicable"])
        self.assertIsNone(result["error"])

    def test_missing_file_is_still_an_error(self):
        result = v17.patch_abba_py(Path(tempfile.mkdtemp()) / "absent.py", dry_run=True)
        self.assertEqual(result["error"], "abba.py not found")
        self.assertFalse(result["not_applicable"])


class PassCriterionTests(unittest.TestCase):
    OK = {"patched": True, "already_patched": False, "error": None}
    KNOWN = {"patched": True, "already_patched": True, "error": None}
    SKIP = {"patched": False, "not_applicable": "different ABBA layout", "error": None}
    BROKEN = {"patched": False, "already_patched": False, "error": "abba.py not found"}

    def test_skipped_installations_do_not_fail_the_run(self):
        verdict = v17.evaluate_patch_results([self.OK, self.KNOWN, self.SKIP])
        self.assertTrue(verdict["passed"])
        self.assertEqual(len(verdict["applicable"]), 2)
        self.assertEqual(len(verdict["skipped"]), 1)

    def test_a_real_error_still_fails_the_run(self):
        self.assertFalse(v17.evaluate_patch_results([self.OK, self.BROKEN])["passed"])

    def test_nothing_applicable_fails_because_abba_changed(self):
        # If no discovered file carries the anchor any more, the patch has gone
        # stale and that must be noticed rather than silently skipped.
        self.assertFalse(v17.evaluate_patch_results([self.SKIP, self.SKIP])["passed"])

    def test_no_installations_at_all_fails(self):
        self.assertFalse(v17.evaluate_patch_results([])["passed"])


if __name__ == "__main__":
    unittest.main()
