from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import shutil
import sys
import zipfile

import numpy as np
import tifffile

from src import ch03_nissl_pipeline as pipeline
from src import nissl_release_asset
from src import storage_preflight
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import v25_clean_native_brainglobe_install as native_install
from src import summarize_nissl_coverage
from src import write_build_summary


class RegisteredStackTests(unittest.TestCase):
    def test_unverified_renderer_cannot_be_installed(self) -> None:
        with self.assertRaisesRegex(pipeline.NisslBuildError, "NISSL_INSTALL_UNVERIFIED"):
            pipeline.install_channel({
                "renderer_backend": "experimental_python_tps",
                "native_parity_verified": False,
            })

    def test_release_build_rejects_unverified_python_bigwarp_renderer(self) -> None:
        with self.assertRaisesRegex(pipeline.NisslBuildError, "ABBA_NATIVE_PARITY_REQUIRED"):
            pipeline.require_scientific_render_readiness(False)

    def test_experimental_renderer_requires_explicit_opt_in(self) -> None:
        pipeline.require_scientific_render_readiness(True)

    def test_target_offset_is_applied_to_nonempty_label_sequence_not_volume_zero(self) -> None:
        labels = np.zeros((608, 1, 1), dtype=np.uint16)
        labels[10:599] = 1
        target_ap, duplicate = pipeline.registered_target_ap_mapping(labels)
        self.assertEqual(duplicate, 10)
        self.assertEqual((int(target_ap[0]), int(target_ap[-1])), (11, 598))
        self.assertEqual(len(target_ap), 588)

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


class MultiAtlasTransactionTests(unittest.TestCase):
    def test_later_target_failure_restores_every_earlier_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atlases = [root / "atlas_a", root / "atlas_b"]
            for atlas in atlases:
                atlas.mkdir()
                (atlas / "metadata.json").write_text("original", encoding="utf-8")
                (atlas / "annotation.tiff").touch()
                (atlas / "annotation.nii.gz").touch()
            calls = 0
            def activate(atlas, active, report):
                nonlocal calls
                calls += 1
                (atlas / "metadata.json").write_text("changed", encoding="utf-8")
                (atlas / "waxholm_anatomy_reference.tiff").write_text("partial", encoding="utf-8")
                if calls == 2:
                    raise OSError("second atlas activation failed")
                return {"atlas": str(atlas)}
            provenance = {"renderer_backend": "native_abba_0.11", "native_backend_verified": True,
                          "visual_parity_status": "pending"}
            with mock.patch.object(pipeline, "atlas_candidates", return_value=atlases), \
                    mock.patch.object(pipeline.tifffile, "imread", return_value=np.zeros((1,), dtype=np.uint16)), \
                    mock.patch.object(pipeline, "_transactional_atlas_install", side_effect=activate), \
                    mock.patch.object(pipeline, "REPORT_DIR", root / "reports"):
                with self.assertRaisesRegex(OSError, "second atlas"):
                    pipeline.install_channel(provenance)
            for atlas in atlases:
                self.assertEqual((atlas / "metadata.json").read_text(encoding="utf-8"), "original")
                self.assertFalse((atlas / "waxholm_anatomy_reference.tiff").exists())


class WaxholmSourceTests(unittest.TestCase):
    def manifest(self):
        return {
            "waxholm_atlas_name": "whs_sd_rat_39um",
            "waxholm_dataset_version": "4.0",
            "waxholm_brainglobe_package_version": "1.2",
            "waxholm_reference_shape_ap_si_lr": [4, 2, 3],
            "waxholm_orientation": "asr",
        }

    def write_atlas(self, root: Path, version="1.2", orientation="asr") -> Path:
        atlas = root / "whs_sd_rat_39um_v1.2"
        atlas.mkdir(parents=True)
        (atlas / "metadata.json").write_text(
            json.dumps({"version": version, "orientation": orientation}), encoding="utf-8"
        )
        tifffile.imwrite(atlas / "reference.tiff", np.zeros((4, 2, 3), dtype=np.uint16))
        return atlas

    def test_missing_cache_is_downloaded_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atlas = self.write_atlas(root)
            fake = mock.Mock(brainglobe_dir=root, local_full_name=atlas.name)
            # Simulate that the downloader creates the atlas during construction.
            shutil.rmtree(atlas)
            def download(*args, **kwargs):
                created = self.write_atlas(root)
                fake.local_full_name = created.name
                return fake
            with mock.patch.object(pipeline.brainglobe_config, "get_brainglobe_dir", return_value=root), \
                    mock.patch.object(pipeline, "BrainGlobeAtlas", side_effect=download) as constructor:
                source, report = pipeline.find_waxholm_source(self.manifest())
            constructor.assert_called_once_with("whs_sd_rat_39um", brainglobe_dir=root, check_latest=True)
            self.assertTrue(source.is_file())
            self.assertEqual(report["source_kind"], "downloaded and validated by BrainGlobe AtlasAPI")

    def test_wrong_downloaded_package_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other = root / "whs_sd_rat_39um_v2.0"
            fake = mock.Mock(brainglobe_dir=root, local_full_name=other.name)
            with mock.patch.object(pipeline.brainglobe_config, "get_brainglobe_dir", return_value=root), \
                    mock.patch.object(pipeline, "BrainGlobeAtlas", return_value=fake):
                with self.assertRaisesRegex(pipeline.NisslBuildError, "WHS_VERSION"):
                    pipeline.find_waxholm_source(self.manifest())

    def test_wrong_orientation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_atlas(root, orientation="pir")
            with mock.patch.object(pipeline.brainglobe_config, "get_brainglobe_dir", return_value=root):
                with self.assertRaisesRegex(pipeline.NisslBuildError, "WHS_ORIENTATION"):
                    pipeline.find_waxholm_source(self.manifest())


class WindowsTiffActivationTests(unittest.TestCase):
    def test_validation_handle_is_closed_before_atomic_replace(self) -> None:
        class FakeTiff:
            closed = False
            series = [mock.Mock(shape=pipeline.TARGET_SHAPE, dtype=np.dtype(np.uint16))]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.closed = True

        opened = FakeTiff()
        replaced = []

        def windows_replace(source, destination):
            if not opened.closed:
                raise PermissionError(32, "file is used by another process")
            replaced.append((source, destination))
            return destination

        temporary = Path("channel.tiff.partial")
        destination = Path("channel.tiff")
        with mock.patch.object(pipeline.tifffile, "TiffFile", return_value=opened), \
                mock.patch.object(Path, "replace", windows_replace):
            pipeline.activate_validated_tiff(temporary, destination)
        self.assertTrue(opened.closed)
        self.assertEqual(replaced, [(temporary, destination)])

    def test_memmap_is_closed_explicitly(self) -> None:
        mapping = mock.Mock()
        pipeline.close_memmap(mock.Mock(_mmap=mapping))
        mapping.close.assert_called_once_with()

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
                        "expected_abba_state": "final.abba", "expected_abba_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                    }
                ),
                encoding="utf-8",
            )
            package = root / "resources" / "optional_ch03" / "nissl_registration_0_3_0"
            package.mkdir()
            (package / "final.abba").write_text("{}", encoding="utf-8")
            manifest = json.loads(manifest_path.read_text()); manifest["expected_abba_sha256"] = nissl_release_asset.sha256_file(package / "final.abba"); manifest_path.write_text(json.dumps(manifest))
            (package / "registration_manifest.json").write_text("{}", encoding="utf-8")
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
                archive.writestr("unused-legacy-stack.tif", "test")
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
                        "expected_abba_state": "final.abba", "expected_abba_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                    }
                ),
                encoding="utf-8",
            )
            package = nissl_release_asset.resolve(root)
            self.assertTrue((package / "final.abba").is_file())
            self.assertFalse((package / "registered_slices_ImageJ_stack.tif").is_file())

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
                        "expected_abba_state": "final.abba", "expected_abba_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
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


class NativeBackupTests(unittest.TestCase):
    def test_existing_atlas_backup_moves_to_builder_volume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            brain_globe = root / "brain_globe"
            atlas = brain_globe / "paxinos_watson_rat_40um_v1.0"
            atlas.mkdir(parents=True)
            (atlas / "metadata.json").write_text("{}", encoding="utf-8")
            legacy = brain_globe / "_paxinos_cleanup_backup_old"
            legacy.mkdir()
            (legacy / "old.txt").write_text("old", encoding="utf-8")
            backup_base = root / "builder" / "backups"
            report = native_install.backup_existing_paxinos_cache(brain_globe, True, backup_base)
            self.assertFalse(atlas.exists())
            self.assertEqual(report["bytes_moved"], 2)
            destination = Path(report["moved_items"][0]["to"])
            self.assertTrue((destination / "metadata.json").is_file())
            self.assertTrue(str(destination).startswith(str(backup_base)))
            self.assertFalse(legacy.exists())
            legacy_destination = Path(report["legacy_backup_items"][0]["to"])
            self.assertTrue((legacy_destination / "old.txt").is_file())


class CoverageSummaryTests(unittest.TestCase):
    def test_compact_summary_ranks_worst_plane_and_reports_insets(self) -> None:
        report = {"ch03_import": {"edge_coverage": {
            "plane_count": 2, "coverage_fraction_min": 0.5,
            "coverage_fraction_median": 0.7, "coverage_fraction_max": 0.9,
            "pixels_modified": False, "planes": [
                {"ap": 10, "coverage_fraction": 0.9, "label_pixels": 10,
                 "label_pixels_with_nissl_signal": 9,
                 "label_bbox_si_lr": [[0, 0], [9, 9]], "nissl_signal_bbox_si_lr": [[0, 0], [9, 9]]},
                {"ap": 11, "coverage_fraction": 0.5, "label_pixels": 10,
                 "label_pixels_with_nissl_signal": 5,
                 "label_bbox_si_lr": [[0, 0], [9, 9]], "nissl_signal_bbox_si_lr": [[2, 1], [8, 7]]},
            ]}}}
        text = summarize_nissl_coverage.summarize(report, worst_count=1)
        self.assertIn("AP 11", text)
        self.assertIn("si_min_inset': 2", text)
        self.assertNotIn("AP 10:", text)


class BuildSummaryTests(unittest.TestCase):
    def test_abba_reconstruction_fields_are_not_reported_as_missing(self) -> None:
        report = {"abba_reconstruction": {
            "stack_order": "anterior-to-posterior", "target_sequence_offset": 1,
            "reconstruction": {
                "mapped_plane_count": 588,
                "anterior_edge_policy": "duplicate_first_registered_plane",
                "duplicated_anterior_target_ap": 0,
                "unused_target_sequence_positions": {"before": 1, "after": 0},
            },
        }}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "reports/ch03_nissl/ch03_nissl_report.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(report), encoding="utf-8")
            atlas = root / "atlas"
            atlas.mkdir()
            (atlas / "annotation.tiff").write_bytes(b"x")
            (atlas / "waxholm_anatomy_reference.tiff").write_bytes(b"x")
            (atlas / "metadata.json").write_text(json.dumps({"additional_references": []}))
            argv = ["write_build_summary.py", "--root", str(root), "--status", "success"]
            with mock.patch.object(write_build_summary, "locate_installed_atlas", return_value=atlas), \
                    mock.patch.object(sys, "argv", argv):
                self.assertEqual(write_build_summary.main(), 0)
            summary = (root / "reports/BUILD_SUMMARY.txt").read_text(encoding="utf-8")
            self.assertIn("Nissl AP order: anterior-to-posterior", summary)
            self.assertIn("Mapped Nissl planes: 588", summary)
            self.assertIn("Duplicated anterior target AP: 0", summary)
            self.assertIn("Nissl renderer backend: unverified_or_legacy", summary)
            self.assertIn("Native backend verified: False", summary)
            self.assertIn("Visual parity status: not_applicable", summary)
            self.assertIn("Release eligible: False", summary)
            self.assertNotIn("not recorded", summary)


class NativeStatusTests(unittest.TestCase):
    def test_python_renderer_cannot_install(self):
        with self.assertRaisesRegex(pipeline.NisslBuildError, "NISSL_INSTALL_UNVERIFIED"):
            pipeline.validate_install_provenance({"renderer_backend": "experimental_python_tps"})

    def test_native_pending_can_install_but_is_not_release_eligible(self):
        parity, eligible = pipeline.validate_install_provenance({
            "renderer_backend": "native_abba_0.11", "native_backend_verified": True,
            "visual_parity_status": "pending"})
        self.assertEqual(parity, "pending")
        self.assertFalse(eligible)

    def test_native_passed_is_release_eligible(self):
        self.assertEqual(pipeline.validate_install_provenance({
            "renderer_backend": "native_abba_0.11", "native_backend_verified": True,
            "visual_parity_status": "passed"}), ("passed", True))


class NativeRuntimePolicyTests(unittest.TestCase):
    def test_direct_vendor_layout_and_state_binding(self):
        from src import native_abba_runtime as runtime
        runtime.validate_vendor()
        report = runtime.inspect_state(Path(__file__).parents[1] / "resources/optional_ch03/nissl_registration_0_3_0/final_for_V_0_3.abba")
        self.assertEqual(report["source_id_range"], [0, 587])
        self.assertEqual(report["waxholm_ap_range"], [189, 776])

    def test_cache_paths_are_builder_local(self):
        from src.native_abba_runtime import RuntimePaths
        with tempfile.TemporaryDirectory() as temporary:
            paths = RuntimePaths(Path(temporary) / "native")
            env = paths.environment()
            self.assertTrue(all(str(Path(temporary)) in value for key, value in env.items() if key != "BRAINGLOBE_DIR"))

    def test_jgo_repositories_are_explicit_and_builder_local(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            config = runtime.configure_jgo(paths)
            text = config.read_text(encoding="utf-8")
            self.assertEqual(config.parent, paths.maven_user_home)
            self.assertIn("[repositories]", text)
            self.assertIn("maven.scijava.org", text)
            self.assertIn("artifacts.openmicroscopy.org", text)
            env = paths.environment()
            self.assertEqual(env["HOME"], str(paths.maven_user_home))
            self.assertEqual(env["USERPROFILE"], str(paths.maven_user_home))

    def test_maven_settings_force_ome_repository_for_jgo_subprocess(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            settings = runtime.configure_maven_settings(paths)
            tree = runtime.ET.parse(settings)
            namespace = {"m": "http://maven.apache.org/SETTINGS/1.2.0"}
            repositories = {
                item.find("m:id", namespace).text: item.find("m:url", namespace).text
                for item in tree.findall(".//m:repository", namespace)
            }
            self.assertEqual(repositories, runtime.JGO_REPOSITORIES)
            self.assertEqual(settings, paths.maven_settings)
            self.assertIn(str(settings), paths.environment()["MAVEN_ARGS"])
            active = tree.find(".//m:activeProfile", namespace)
            self.assertEqual(active.text, "native-abba-repositories")

    def test_maven_and_legacy_jgo_use_the_same_local_repository(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            self.assertEqual(
                paths.maven_repository,
                paths.maven_user_home / ".m2" / "repository",
            )
            self.assertIn(str(paths.maven_repository), paths.environment()["MAVEN_OPTS"])


class NativeDependencyParityTests(unittest.TestCase):
    def test_runtime_coordinates_equal_vendored_get_java_dependencies(self):
        import ast
        from src import native_abba_runtime as runtime
        tree = ast.parse((runtime.VENDOR / "abba.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_java_dependencies")
        returned = next(node.value for node in ast.walk(function) if isinstance(node, ast.Return))
        vendor_dependencies = ast.literal_eval(returned)
        self.assertEqual(list(runtime.JAVA_DEPENDENCIES), vendor_dependencies)
        self.assertIn("ch.epfl.biop.atlas.aligner.command.ImportStdZipStateCommand", runtime.REQUIRED_JAVA_CLASSES)


class PythonBridgeCompatibilityTests(unittest.TestCase):
    def test_requirements_pin_jgo_api_compatible_with_pyimagej(self):
        requirements = (Path(__file__).parents[1] / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("jgo==1.0.6", requirements)

    def test_jgo_3_is_rejected_before_pyimagej_import(self):
        from src import native_abba_runtime as runtime
        versions = dict(runtime.PYTHON_BRIDGE_VERSIONS)
        versions["jgo"] = "3.1.0"
        with mock.patch.object(runtime.importlib.metadata, "version", side_effect=lambda name: versions[name]):
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "PYIMAGEJ_VERSION.*jgo==1.0.6"):
                runtime.validate_python_bridge()

    def test_pinned_bridge_requires_legacy_jgo_export(self):
        from src import native_abba_runtime as runtime
        with mock.patch.object(runtime.importlib.metadata, "version",
                               side_effect=lambda name: runtime.PYTHON_BRIDGE_VERSIONS[name]), \
                mock.patch.object(runtime.importlib, "import_module", return_value=object()):
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "PYIMAGEJ_API"):
                runtime.validate_python_bridge()


class NativeRuntimeInitializationTests(unittest.TestCase):
    def test_missing_builder_java_is_classified(self):
        from src.native_abba_runtime import NativeRuntimeError, RuntimePaths, validate_java
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(NativeRuntimeError, "JAVA_COMPONENT_MISSING"):
                validate_java(RuntimePaths(Path(temporary)))

    def test_native_api_initialization_uses_pinned_dependencies(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            executable = paths.java / "bin" / ("java.exe" if runtime.os.name == "nt" else "java")
            executable.parent.mkdir(parents=True)
            executable.touch()
            (paths.java / "runtime-manifest.json").write_text(
                json.dumps({"pinned_version": "17.0.14+7"}), encoding="utf-8"
            )
            fake_imagej = mock.Mock()
            fake_ij = mock.Mock()
            fake_imagej.init.return_value = fake_ij
            fake_scyjava = mock.Mock()
            fake_scyjava.jimport.side_effect = lambda name: "class:" + name
            real_import = runtime.importlib.import_module
            def imported(name):
                if name == "imagej": return fake_imagej
                if name == "scyjava": return fake_scyjava
                return real_import(name)
            java_version = mock.Mock(returncode=0, stdout="", stderr='openjdk version "17.0.14"')
            with mock.patch.object(runtime, "install_vendor_package"), \
                    mock.patch.object(runtime, "validate_maven"), \
                    mock.patch.object(runtime, "validate_python_bridge", return_value=dict(runtime.PYTHON_BRIDGE_VERSIONS)), \
                    mock.patch.object(runtime.importlib, "import_module", side_effect=imported), \
                    mock.patch.object(runtime.subprocess, "run", return_value=java_version):
                ij, classes = runtime.initialize_native_api(paths)
            self.assertIs(ij, fake_ij)
            fake_imagej.init.assert_called_once_with(list(runtime.JAVA_DEPENDENCIES), mode="headless")
            self.assertEqual(set(classes), set(runtime.REQUIRED_JAVA_CLASSES))

    def test_missing_builder_maven_is_classified(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "MAVEN_COMPONENT_MISSING"):
                runtime.validate_maven(runtime.RuntimePaths(Path(temporary)))

    def test_wrong_maven_marker_version_is_rejected(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            executable = paths.maven_home / "bin" / ("mvn.cmd" if runtime.os.name == "nt" else "mvn")
            executable.parent.mkdir(parents=True)
            executable.touch()
            paths.maven.mkdir(parents=True, exist_ok=True)
            (paths.maven / "runtime-manifest.json").write_text(
                json.dumps({"pinned_version": "3.8.8"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "MAVEN_VERSION"):
                runtime.validate_maven(paths)

    def test_pinned_builder_maven_is_accepted(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            executable = paths.maven_home / "bin" / ("mvn.cmd" if runtime.os.name == "nt" else "mvn")
            executable.parent.mkdir(parents=True)
            executable.touch()
            paths.maven.mkdir(parents=True, exist_ok=True)
            (paths.maven / "runtime-manifest.json").write_text(
                json.dumps({"pinned_version": "3.9.9"}), encoding="utf-8"
            )
            completed = mock.Mock(returncode=0, stdout="Apache Maven 3.9.9", stderr="")
            with mock.patch.object(runtime.subprocess, "run", return_value=completed) as invoked:
                self.assertEqual(runtime.validate_maven(paths), executable)
            self.assertIn(str(executable), invoked.call_args.args[0])

    def test_corrupt_java_marker_is_classified(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            executable = paths.java / "bin" / ("java.exe" if runtime.os.name == "nt" else "java")
            executable.parent.mkdir(parents=True)
            executable.touch()
            (paths.java / "runtime-manifest.json").write_text("not json", encoding="utf-8")
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "JAVA_CACHE_CORRUPT"):
                runtime.validate_java(paths)

    def test_wrong_java_version_is_rejected(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            executable = paths.java / "bin" / ("java.exe" if runtime.os.name == "nt" else "java")
            executable.parent.mkdir(parents=True)
            executable.touch()
            (paths.java / "runtime-manifest.json").write_text(
                json.dumps({"pinned_version": "21.0.1+12"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(runtime.NativeRuntimeError, "JAVA_VERSION"):
                runtime.validate_java(paths)

    def test_native_preflight_failure_is_persisted(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            paths = runtime.RuntimePaths(Path(temporary))
            try:
                raise runtime.NativeRuntimeError("NATIVE_API_INITIALIZATION", "maven resolution failed")
            except runtime.NativeRuntimeError as exc:
                report_path = runtime.write_failure_report(paths, exc)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error_code"], "NATIVE_API_INITIALIZATION")
            self.assertIn("maven resolution failed", report["error_message"])
            self.assertIn("NativeRuntimeError", report["traceback"])
            self.assertFalse(report["native_backend_verified"])

    def test_wrong_state_runtime_version_is_rejected(self):
        from src import native_abba_runtime as runtime
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.abba"
            with zipfile.ZipFile(state_path, "w") as archive:
                archive.writestr("sources.json", "[]")
                archive.writestr("state.json", json.dumps({"version": "0.10.4"}))
                archive.writestr("_bdvdataset_0.xml", "")
            with mock.patch.object(runtime, "STATE_SHA256", runtime.sha256(state_path)):
                with self.assertRaisesRegex(runtime.NativeRuntimeError, "RUNTIME_VERSION"):
                    runtime.inspect_state(state_path)


class FailedBuildSummaryTests(unittest.TestCase):
    def test_failed_run_does_not_claim_preexisting_nissl_as_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atlas = root / "atlas"
            atlas.mkdir()
            (atlas / "annotation.tiff").touch()
            (atlas / "waxholm_anatomy_reference.tiff").touch()
            (atlas / "metadata.json").write_text(json.dumps({
                "additional_references": ["waxholm_anatomy_reference"],
                "optional_ch03_registration": {
                    "renderer_backend": "native_abba_0.11",
                    "native_backend_verified": True,
                    "visual_parity_status": "passed",
                    "release_eligible": True,
                },
            }), encoding="utf-8")
            argv = ["write_build_summary.py", "--root", str(root), "--status", "failed",
                    "--stage", "Paxinos source preflight", "--failure-exit-code", "2"]
            with mock.patch.object(write_build_summary, "locate_installed_atlas", return_value=atlas), \
                    mock.patch.object(sys, "argv", argv):
                self.assertEqual(write_build_summary.main(), 0)
            summary = (root / "reports/BUILD_SUMMARY.txt").read_text(encoding="utf-8")
            self.assertIn("Failure exit code: 2", summary)
            self.assertIn("pre-existing; not validated by this failed build", summary)
            self.assertIn("Nissl renderer backend: not produced by failed build", summary)
            self.assertIn("Native backend verified: False", summary)
            self.assertNotIn("Native ABBA 0.11 backend execution is verified", summary)


if __name__ == "__main__":
    unittest.main()
