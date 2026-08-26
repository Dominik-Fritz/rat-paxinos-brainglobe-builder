from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import shutil
import zipfile

import numpy as np
import tifffile

from src import ch03_nissl_pipeline as pipeline
from src import nissl_release_asset
from src import storage_preflight


class RegisteredStackTests(unittest.TestCase):
    def test_anterior_to_posterior_preserves_stack_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = {
                "TARGET_SHAPE": pipeline.TARGET_SHAPE,
                "ACTIVE_PATH": pipeline.ACTIVE_PATH,
                "REPORT_DIR": pipeline.REPORT_DIR,
                "REPORT_JSON": pipeline.REPORT_JSON,
                "find_annotation_tiff": pipeline.find_annotation_tiff,
                "orient_annotation": pipeline.orient_annotation,
            }
            try:
                pipeline.TARGET_SHAPE = (589, 2, 3)
                pipeline.ACTIVE_PATH = root / "active.tiff"
                pipeline.REPORT_DIR = root / "reports"
                pipeline.REPORT_JSON = pipeline.REPORT_DIR / "ch03_nissl_report.json"
                fixed_mask = np.ones((589, 2, 3), dtype=bool)
                fixed_mask[0] = False
                fixed_mask[0, 0, 0] = True
                annotation = root / "annotation.tiff"
                tifffile.imwrite(annotation, fixed_mask.astype(np.uint16))
                pipeline.find_annotation_tiff = lambda: annotation
                stack = np.empty((588, 2, 3), dtype=np.uint16)
                for index in range(588):
                    stack[index].fill(index + 1)
                source = root / "registered_slices_ImageJ_stack.tif"
                tifffile.imwrite(source, stack, imagej=True)

                report = pipeline.import_registered_stack(
                    source, "anterior-to-posterior", 1, "duplicate_first_registered_plane"
                )
                self.assertEqual(report["target_sequence_offset"], 1)
                volume = tifffile.imread(pipeline.ACTIVE_PATH)
                self.assertTrue(np.all(volume[0] == 1))
                self.assertTrue(np.all(volume[1] == 1))
                self.assertTrue(np.all(volume[-1] == 588))
                self.assertEqual(report["duplicated_anterior_target_ap"], 0)
            finally:
                for name, value in original.items():
                    setattr(pipeline, name, value)


class ReleaseAssetTests(unittest.TestCase):
    def test_embedded_registration_package_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / nissl_release_asset.MANIFEST_RELATIVE
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "release": "test",
                        "asset_name": "test.zip",
                        "download_url": "",
                        "sha256": "",
                        "expected_registered_stack": "registered_slices_ImageJ_stack.tif",
                    }
                ),
                encoding="utf-8",
            )
            package = root / "resources" / "optional_ch03" / "nissl_registration_0_3_0"
            package.mkdir()
            (package / "final.abba").write_text("{}", encoding="utf-8")
            (package / "registration_manifest.json").write_text("{}", encoding="utf-8")
            (package / "registered_slices_ImageJ_stack.tif").write_bytes(b"test")
            self.assertEqual(nissl_release_asset.resolve(root), package.resolve())
            self.assertTrue((root / "reports/nissl_release_asset/nissl_release_asset_summary.txt").is_file())

    def test_pinned_release_zip_downloads_and_extracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            source_zip = base / "release.zip"
            with zipfile.ZipFile(source_zip, "w") as archive:
                archive.writestr("final.abba", "{}")
                archive.writestr("registration_manifest.json", "{}")
                archive.writestr("registered_slices_ImageJ_stack.tif", "test")
            digest = nissl_release_asset.sha256_file(source_zip)
            manifest_path = root / nissl_release_asset.MANIFEST_RELATIVE
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "release": "test",
                        "asset_name": "test.zip",
                        "download_url": source_zip.as_uri(),
                        "sha256": digest,
                        "expected_registered_stack": "registered_slices_ImageJ_stack.tif",
                    }
                ),
                encoding="utf-8",
            )
            package = nissl_release_asset.resolve(root)
            self.assertTrue((package / "final.abba").is_file())
            self.assertTrue((package / "registered_slices_ImageJ_stack.tif").is_file())

    def test_pin_asset_records_url_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / nissl_release_asset.MANIFEST_RELATIVE
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "release": "test",
                        "asset_name": "old.zip",
                        "download_url": "",
                        "sha256": "",
                        "expected_registered_stack": "registered_slices_ImageJ_stack.tif",
                    }
                ),
                encoding="utf-8",
            )
            asset = root / "new.zip"
            asset.write_bytes(b"release data")
            url = "https://github.com/example/project/releases/download/test/new.zip"
            self.assertEqual(nissl_release_asset.pin_asset(root, asset, url), 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["asset_name"], "new.zip")
            self.assertEqual(manifest["download_url"], url)
            self.assertEqual(manifest["sha256"], nissl_release_asset.sha256_file(asset))


class IncrementalBuilderTests(unittest.TestCase):
    def test_optional_abba_commands_are_direct_and_guarded(self) -> None:
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        section = batch[batch.index("echo [25/30]"):batch.index("\n:detect_python")]
        self.assertNotIn("step_runner.py", section)
        self.assertIn('if /I "%PATCH_ABBA%"=="YES" (', section)
        self.assertIn('ABBA_V17_EXIT=!ERRORLEVEL!', section)
        self.assertIn('ABBA_V44_EXIT=!ERRORLEVEL!', section)
        self.assertIn('FINAL_STATUS=warnings', section)

    def test_noninteractive_guards_all_pauses(self) -> None:
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        pause_lines = [line for line in batch.splitlines() if "pause" in line.lower()]
        self.assertTrue(pause_lines)
        self.assertTrue(all("NON_INTERACTIVE" in line for line in pause_lines))


class StoragePreflightTests(unittest.TestCase):
    def test_same_volume_is_deduplicated_with_all_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = storage_preflight.inspect_locations(
                [("builder_root", root), ("temporary", root / "temp")], warn_gib=0, fail_gib=0
            )
            self.assertEqual(len(report["volumes"]), 1)
            self.assertEqual(report["volumes"][0]["roles"], ["builder_root", "temporary"])

    def test_low_space_warns_but_critical_space_fails(self) -> None:
        usage = shutil._ntuple_diskusage(total=20 * 1024**3, used=16 * 1024**3, free=4 * 1024**3)
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(storage_preflight.shutil, "disk_usage", return_value=usage):
            warning = storage_preflight.inspect_locations([("builder_root", Path(temporary))])
            critical = storage_preflight.inspect_locations([("builder_root", Path(temporary))], fail_gib=5)
        self.assertEqual(warning["status"], "warning")
        self.assertEqual(critical["status"], "failed")


class NisslDisplayDiagnosticTests(unittest.TestCase):
    def test_edge_coverage_reports_without_modifying_pixels(self) -> None:
        labels = np.zeros((2, 3, 4), dtype=np.uint16)
        labels[:, 1:, 1:3] = 1
        nissl = np.zeros_like(labels)
        nissl[:, 1:, 1] = 10
        before = nissl.copy()
        report = pipeline.measure_edge_coverage(labels, nissl)
        self.assertEqual(report["coverage_fraction_median"], 0.5)
        self.assertFalse(report["pixels_modified"])
        np.testing.assert_array_equal(nissl, before)


if __name__ == "__main__":
    unittest.main()
