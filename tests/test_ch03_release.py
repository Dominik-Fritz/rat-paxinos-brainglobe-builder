from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import tifffile

from src import ch03_nissl_pipeline as pipeline
from src import nissl_release_asset


class RegisteredStackTests(unittest.TestCase):
    def test_anterior_to_posterior_preserves_stack_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = {
                "TARGET_SHAPE": pipeline.TARGET_SHAPE,
                "ACTIVE_PATH": pipeline.ACTIVE_PATH,
                "REPORT_DIR": pipeline.REPORT_DIR,
                "load_fixed_label_mask": pipeline.load_fixed_label_mask,
                "qc_slices": pipeline.qc_slices,
                "write_json": pipeline.write_json,
            }
            try:
                pipeline.TARGET_SHAPE = (589, 2, 3)
                pipeline.ACTIVE_PATH = root / "active.tiff"
                pipeline.REPORT_DIR = root / "reports"
                fixed_mask = np.ones((589, 2, 3), dtype=bool)
                fixed_mask[0] = False
                fixed_mask[0, 0, 0] = True
                pipeline.load_fixed_label_mask = lambda: (fixed_mask, root / "annotation.tiff", "test")
                pipeline.qc_slices = lambda *args: None
                pipeline.write_json = lambda *args: None
                stack = np.empty((588, 2, 3), dtype=np.uint16)
                for index in range(588):
                    stack[index].fill(index + 1)
                source = root / "registered_slices_ImageJ_stack.tif"
                tifffile.imwrite(source, stack, imagej=True)

                self.assertEqual(
                    pipeline.import_imagej_registered_stack(str(source), "anterior-to-posterior"), 0
                )
                volume = tifffile.imread(pipeline.ACTIVE_PATH)
                # Even though the synthetic first endpoint has fewer label
                # voxels, the documented unmatched endpoint is the final one.
                self.assertTrue(np.all(volume[0] == 1))
                self.assertTrue(np.all(volume[-2] == 588))
                self.assertFalse(volume[-1].any())
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
            package = root / "resources" / "optional_ch03" / "abba_registration_package"
            package.mkdir()
            (package / "final.abba").write_text("{}", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
