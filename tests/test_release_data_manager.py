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


class LocalCacheTests(unittest.TestCase):
    def test_repository_cache_is_verified_before_network(self):
        repository = Path(__file__).parents[1]
        source = repository / "data/external/bluebrain_headmodels_v1"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "data/external/bluebrain_headmodels_v1"
            cache.mkdir(parents=True)
            for spec in manager.FILES.values():
                (cache / spec["filename"]).write_bytes((source / spec["filename"]).read_bytes())
            with mock.patch.object(manager, "download_file", side_effect=AssertionError("network must not be used")):
                result = manager.run(root, "ensure-minimal", include_optional=True)
            self.assertEqual(result, 0)
            report = (root / "reports/release_data_manager/release_data_manager_report.json").read_text(encoding="utf-8")
            self.assertEqual(report.count('"action": "copied_from_local_cache"'), 3)
            for key, spec in manager.FILES.items():
                self.assertEqual(manager.inspect_file(root / "data/raw/bluebrainheadmodels", key, spec)["status"], "ok")
