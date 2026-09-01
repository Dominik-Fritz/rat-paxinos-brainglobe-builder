from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import release_data_manager as manager


class OptionalDownloadTests(unittest.TestCase):
    def test_optional_download_failure_is_warning_not_build_failure(self):
        def inspected(raw_dir, key, spec):
            required = bool(spec.get("required"))
            return {
                "key": key, "filename": spec["filename"], "path": str(raw_dir / spec["filename"]),
                "required": required, "recommended": bool(spec.get("recommended")),
                "exists": required, "size_mb": 1.0 if required else None,
                "md5_expected": spec.get("md5"), "md5_actual": spec.get("md5") if required else None,
                "md5_ok": True if required else None, "size_plausible": True if required else None,
                "status": "ok" if required else "missing", "url": "https://example.invalid/file",
            }
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(manager, "inspect_file", side_effect=inspected), \
                mock.patch.object(manager, "download_file", return_value=(False, "offline")), \
                mock.patch.object(manager, "write_reports") as write:
            result = manager.run(Path(temporary), "ensure-minimal", include_optional=True)
        self.assertEqual(result, 0)
        report = write.call_args.args[1]
        self.assertTrue(report["passed"])
        self.assertEqual(report["errors"], [])
        self.assertRegex(report["warnings"][0], "optional; build continues")
