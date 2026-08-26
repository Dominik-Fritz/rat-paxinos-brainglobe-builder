from __future__ import annotations

import json
from pathlib import Path
import tempfile
import shutil
import unittest
import zipfile
from unittest import mock
import os
import sys
import hashlib
import io

import nibabel as nib

from src import system_preflight

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
                        "expected_stack_shape": [2, 2, 2], "stack_order": "anterior-to-posterior",
                        "target_sequence_offset": 1, "anterior_edge_policy": "duplicate_first_registered_plane",
                    }
                ),
                encoding="utf-8",
            )
            package = root / "resources" / "optional_ch03" / "nissl_registration_0_3_0"
            package.mkdir()
            (package / "final.abba").write_text("{}", encoding="utf-8")
            (package / "registration_manifest.json").write_text(json.dumps({"release":"test","stack_file":"registered_slices_ImageJ_stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}), encoding="utf-8")
            tifffile.imwrite(package / "registered_slices_ImageJ_stack.tif", np.ones((2,2,2), dtype=np.uint16))
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
                archive.writestr("registration_manifest.json", json.dumps({"release":"test","stack_file":"registered_slices_ImageJ_stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}))
                temporary_tiff = base / "stack.tif"
                tifffile.imwrite(temporary_tiff, np.ones((2,2,2), dtype=np.uint16))
                archive.write(temporary_tiff, "registered_slices_ImageJ_stack.tif")
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
                        "expected_stack_shape": [2, 2, 2], "stack_order": "anterior-to-posterior",
                        "target_sequence_offset": 1, "anterior_edge_policy": "duplicate_first_registered_plane",
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


class StabilityTests(unittest.TestCase):
    def test_corrupt_tiff_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            package=Path(temporary); (package/"final.abba").write_text("{}")
            runtime={"release":"test","stack_file":"stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}
            (package/"registration_manifest.json").write_text(json.dumps(runtime)); (package/"stack.tif").write_bytes(b"broken")
            manifest={"release":"test","expected_registered_stack":"stack.tif","expected_stack_shape":[2,2,2],"stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane"}
            valid,message=nissl_release_asset.validate_package(package,manifest)
            self.assertFalse(valid); self.assertIn("TIFF cannot be opened",message)

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive=Path(temporary)/"bad.zip"
            with zipfile.ZipFile(archive,"w") as output: output.writestr("../escape.json","{}")
            with self.assertRaisesRegex(ValueError,"UNSAFE_PATH"):
                nissl_release_asset.safe_extract(archive,Path(temporary)/"out")

    def test_preflight_reports_low_disk(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(system_preflight.shutil,"disk_usage",return_value=shutil._ntuple_diskusage(100,99,1)):
            result=system_preflight.run(Path(temporary),min_disk_gib=1,min_ram_gib=0,acquire=False)
            self.assertEqual(result["status"],"failed")
            self.assertIn("DISK_SPACE_LOW",[c["code"] for c in result["checks"]])

    def test_wrong_tiff_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            runtime = {"release":"test","stack_file":"stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}
            (package / "registration_manifest.json").write_text(json.dumps(runtime))
            (package / "final.abba").write_text("{}")
            tifffile.imwrite(package / "stack.tif", np.zeros((3,2,2), dtype=np.uint16))
            manifest = dict(runtime, expected_registered_stack="stack.tif")
            valid, message = nissl_release_asset.validate_package(package, manifest)
            self.assertFalse(valid)
            self.assertIn("shape mismatch", message)

    def test_manifest_requires_exact_stack_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            runtime = {"release":"test","stack_file":"other.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}
            (package / "registration_manifest.json").write_text(json.dumps(runtime))
            (package / "final.abba").write_text("{}")
            tifffile.imwrite(package / "other.tif", np.zeros((2,2,2), dtype=np.uint16))
            manifest = dict(runtime, expected_registered_stack="stack.tif")
            valid, message = nissl_release_asset.validate_package(package, manifest)
            self.assertFalse(valid)
            self.assertIn("stack_file", message)

    def test_preflight_reports_missing_write_access(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(system_preflight, "writable", return_value=(False, "denied")):
            result = system_preflight.run(Path(temporary), min_disk_gib=0, min_ram_gib=0, acquire=False)
            self.assertIn("PATH_NOT_WRITABLE", [item["code"] for item in result["checks"]])

    def test_unicode_and_spaces_path_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Atlas ä space"
            result = system_preflight.run(root, min_disk_gib=0, min_ram_gib=0, acquire=False)
            path_check = next(item for item in result["checks"] if item["name"] == "path")
            self.assertTrue(path_check["ok"])

    def test_stale_local_lock_is_repaired(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(system_preflight, "process_is_running", return_value=False):
            root = Path(temporary)
            (root / system_preflight.LOCK_NAME).write_text(json.dumps({"pid":999999,"host":system_preflight.socket.gethostname()}))
            ok, _ = system_preflight.acquire_lock(root, "new-build")
            self.assertTrue(ok)

    def test_deterministic_zip_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir()
            runtime = {"release":"test","stack_file":"stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}
            (source / "registration_manifest.json").write_text(json.dumps(runtime)); (source / "final.abba").write_text("{}")
            tifffile.imwrite(source / "stack.tif", np.ones((2,2,2), dtype=np.uint16))
            manifest_path = root / nissl_release_asset.MANIFEST_RELATIVE; manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(json.dumps(dict(runtime, asset_name="asset.zip", expected_registered_stack="stack.tif")))
            first, second = root / "one.zip", root / "two.zip"
            nissl_release_asset.create_asset(root, source, first)
            os.utime(source / "final.abba", None)
            nissl_release_asset.create_asset(root, source, second)
            self.assertEqual(nissl_release_asset.sha256_file(first), nissl_release_asset.sha256_file(second))

    def test_range_download_resumes_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "asset.bin"
            partial = destination.with_suffix(".bin.partial")
            partial.write_bytes(b"abc")
            class Response(io.BytesIO):
                status = 206
                headers = {"Content-Length": "3"}
                def __enter__(self): return self
                def __exit__(self, *args): self.close()
            with mock.patch.object(nissl_release_asset.urllib.request, "urlopen", return_value=Response(b"def")) as opened:
                size, digest = nissl_release_asset.download("https://example.invalid/a", destination, retries=1)
            self.assertEqual(destination.read_bytes(), b"abcdef")
            self.assertEqual(size, 6)
            self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
            self.assertEqual(opened.call_args.args[0].headers["Range"], "bytes=3-")

    def test_hash_mismatch_cache_is_repaired_in_same_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / "source"; source.mkdir()
            runtime = {"release":"test","stack_file":"stack.tif","state_file":"final.abba","stack_order":"anterior-to-posterior","target_sequence_offset":1,"anterior_edge_policy":"duplicate_first_registered_plane","expected_stack_shape":[2,2,2]}
            (source / "registration_manifest.json").write_text(json.dumps(runtime)); (source / "final.abba").write_text("{}")
            tifffile.imwrite(source / "stack.tif", np.ones((2,2,2), dtype=np.uint16))
            release = root / "release.zip"
            manifest_path = root / nissl_release_asset.MANIFEST_RELATIVE; manifest_path.parent.mkdir(parents=True)
            provisional = dict(runtime, asset_name="asset.zip", expected_registered_stack="stack.tif", download_url="", sha256="")
            manifest_path.write_text(json.dumps(provisional))
            nissl_release_asset.create_asset(root, source, release)
            provisional["download_url"] = release.as_uri(); provisional["sha256"] = nissl_release_asset.sha256_file(release)
            manifest_path.write_text(json.dumps(provisional))
            cache = root / "data" / "release_assets" / "test" / "asset.zip"; cache.parent.mkdir(parents=True); cache.write_bytes(b"corrupt")
            package = nissl_release_asset.resolve(root)
            self.assertTrue((package / "stack.tif").is_file())
            self.assertEqual(nissl_release_asset.sha256_file(cache), provisional["sha256"])


class BuilderBatchTests(unittest.TestCase):
    def test_abba_root_is_forwarded_and_v44_is_guarded(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        self.assertIn('--all --abba-root "%ABBA_ROOT%"', batch)
        self.assertIn('if /I "%PATCH_ABBA%"=="YES" (', batch)
        self.assertIn('set "BUILD_WARNINGS=YES"', batch)
        abba_section = batch[batch.index('echo [25/30]'):batch.rindex('\n:parse_args')]
        self.assertNotIn('step_runner.py', abba_section)
        self.assertIn('ABBA_V17_EXIT=!ERRORLEVEL!', abba_section)
        self.assertIn('ABBA_V44_EXIT=!ERRORLEVEL!', abba_section)

    def test_explicit_missing_python_is_fatal(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        self.assertIn("PYTHON_EXPLICIT_NOT_FOUND", batch)
        self.assertIn("call :detect_python\nif errorlevel 1 goto fail", batch)

    def test_noninteractive_guards_every_pause(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        pause_lines = [line.strip() for line in batch.splitlines() if "pause" in line.lower()]
        self.assertTrue(pause_lines)
        self.assertTrue(all("NON_INTERACTIVE" in line for line in pause_lines))


class StepRunnerTests(unittest.TestCase):
    def test_records_command_exit_code_and_log(self):
        from src import step_runner
        with tempfile.TemporaryDirectory() as temporary:
            code = step_runner.main(["--build-dir", temporary, "--phase", "synthetic", "--", sys.executable, "-c", "print(42)"])
            self.assertEqual(code, 0)
            records = [json.loads(line) for line in (Path(temporary) / "steps.jsonl").read_text().splitlines()]
            self.assertEqual(records[0]["exit_code"], 0)
            self.assertEqual(records[0]["phase"], "synthetic")
            self.assertTrue(Path(records[0]["log"]).is_file())


class TransactionTests(unittest.TestCase):
    def test_repack_failure_rolls_back_every_atlas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); active = root / "active.tiff"
            tifffile.imwrite(active, np.ones((2,2,2), dtype=np.uint16))
            atlases = []
            for index in range(2):
                atlas = root / f"atlas{index}"; atlas.mkdir(); atlases.append(atlas)
                tifffile.imwrite(atlas / "annotation.tiff", np.ones((2,2,2), dtype=np.uint16))
                nib.save(nib.Nifti1Image(np.ones((2,2,2), dtype=np.uint16), np.eye(4)), atlas / "annotation.nii.gz")
                (atlas / "metadata.json").write_text(json.dumps({"marker": index}))
            original = {atlas: (atlas / "metadata.json").read_bytes() for atlas in atlases}
            with mock.patch.object(pipeline, "ACTIVE_PATH", active), mock.patch.object(pipeline, "TARGET_SHAPE", (2,2,2)), mock.patch.object(pipeline, "atlas_candidates", return_value=atlases), mock.patch.object(pipeline, "repack_candidate", side_effect=OSError("archive failed")):
                with self.assertRaisesRegex(OSError, "archive failed"):
                    pipeline.install_channel({"stack_order":"anterior-to-posterior", "target_sequence_offset":1})
            for atlas in atlases:
                self.assertEqual((atlas / "metadata.json").read_bytes(), original[atlas])
                self.assertFalse((atlas / "waxholm_anatomy_reference.tiff").exists())
                self.assertFalse((atlas / "waxholm_anatomy_reference.nii.gz").exists())


if __name__ == "__main__":
    unittest.main()
