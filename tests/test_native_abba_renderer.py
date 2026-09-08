from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

import tifffile

from src import ch03_nissl_pipeline as pipeline
from src import native_abba_renderer as renderer
from src import native_abba_runtime as runtime


class PortableStateTests(unittest.TestCase):
    def test_real_state_rebinds_every_source_without_historical_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            planes = [root / f"whs_nissl_40um_ap_{ap}.tiff" for ap in range(189, 777)]
            destination = root / "portable.abba"
            report = renderer.build_rebound_state(runtime.STATE, planes, destination)
            self.assertEqual(report["source_ids"], [0, 587])
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), {"sources.json", "state.json", "_bdvdataset_0.xml"})
                xml = archive.read("_bdvdataset_0.xml").decode()
            self.assertNotIn("project.qpproj", xml)
            self.assertNotIn("G:\\nissl_registration", xml)
            self.assertEqual(xml.count('"type":"BIOFORMATS"'), 998)
            self.assertIn(str(planes[0].resolve()).replace("\\", "\\\\"), xml)
            self.assertIn(str(planes[-1].resolve()).replace("\\", "\\\\"), xml)

    def test_wrong_source_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(Exception, "expected 588 explicit planes"):
                renderer.build_rebound_state(runtime.STATE, [Path("one.tiff")], Path(temporary) / "x.abba")


class NativeOutputTests(unittest.TestCase):
    def test_source_output_is_found_by_type_not_key(self):
        sac = mock.Mock()
        sac.getSpimSource = mock.Mock()
        outputs = mock.Mock()
        outputs.values.return_value = [[sac]]
        module = mock.Mock()
        module.getOutputs.return_value = outputs
        self.assertEqual(renderer._find_source_and_converters(module), [sac])

    def test_memory_and_disk_errors_are_classified(self):
        self.assertIn("NATIVE_MEMORY", str(renderer.classify_native_failure(MemoryError())))
        self.assertIn("NATIVE_ENOSPC", str(renderer.classify_native_failure(OSError(28, "full"))))

    def test_signal_stats_distinguish_dark_from_missing_without_normalizing(self):
        import numpy as np
        plane = np.array([[0, 1], [3, 0]], dtype=np.uint16)
        before = plane.copy()
        stats = renderer._signal_stats(plane)
        self.assertEqual(stats["dtype"], "uint16")
        self.assertEqual(stats["maximum"], 3.0)
        self.assertEqual(stats["nonzero_pixels"], 2)
        self.assertEqual(stats["nonzero_mean"], 2.0)
        np.testing.assert_array_equal(plane, before)

    def test_spatial_diagnostics_measure_residual_signal_offset_without_changes(self):
        import numpy as np
        labels = np.zeros((1, 5, 5), dtype=np.uint16)
        volume = np.zeros_like(labels)
        labels[0, 1:3, 1:3] = 1
        volume[0, 2:4, 3:5] = 7
        before = volume.copy()
        result = renderer._spatial_diagnostics(labels, volume, np.array([0]))
        self.assertEqual(result["median_centroid_delta_si_lr_voxels"], [1.0, 2.0])
        self.assertEqual(result["median_centroid_delta_si_lr_um"], [40.0, 80.0])
        self.assertFalse(result["pixels_modified"])
        np.testing.assert_array_equal(volume, before)

    def test_normal_windows_path_never_calls_python_tps_builder(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        self.assertIn('src\\native_abba_renderer.py', batch)
        self.assertNotIn('ch03_nissl_pipeline.py" build-from-package', batch)
        self.assertIn("VISUAL_VALIDATION_PENDING", batch)
        self.assertIn('set "BUILD_WARNINGS=YES"', batch)

    def test_optional_native_renderer_failure_preserves_completed_atlas(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        self.assertIn('if /I "%%~A"=="--nissl-required" set "NISSL_REQUIRED=YES"', batch)
        self.assertIn("WARNING [OPTIONAL_NISSL_FAILED]", batch)
        self.assertIn('set "WITH_NISSL=FAILED"', batch)
        renderer_call = '"%VENV_PY%" "src\\native_abba_renderer.py" "!NISSL_PACKAGE!"'
        self.assertIn(renderer_call, batch)
        self.assertNotIn(renderer_call + " || goto fail", batch)

    def test_roundtrip_debug_entrypoint_writes_persistent_evidence(self):
        source = (Path(__file__).parents[1] / "src/v34_debug_transform_roundtrip.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('paths.reports / "native_state_roundtrip.abba"', source)
        self.assertIn('paths.reports / "transform_roundtrip_diff.json"', source)


class NativeZipImportTests(unittest.TestCase):
    def test_multipositioner_is_discovered_by_api_shape(self):
        positioner = mock.Mock()
        positioner.getSlices = mock.Mock()
        positioner.selectSlice = mock.Mock()
        outputs = mock.Mock()
        outputs.values.return_value = [{"nested": "ignored"}, [positioner]]
        module = mock.Mock()
        module.getOutputs.return_value = outputs
        self.assertIs(renderer._find_multipositioner(module, None), positioner)

    def test_renderer_uses_vendored_state_loader_for_three_member_abba_project(self):
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(encoding="utf-8")
        self.assertIn("abba.state_load", source)
        self.assertNotIn("abba.import_std_zip_state", source)
        self.assertIn("ImportStdZipStateCommand expects a different interchange format", source)


class NativeTaskSynchronizationTests(unittest.TestCase):
    class FakeAbba:
        def __init__(self, loaded=True, count=588):
            self.loaded = loaded
            self.count = count
            self.events = []

        def state_load(self, state_file):
            self.events.append(("state_load", state_file))
            return self.loaded

        def wait_for_end_of_tasks(self):
            self.events.append(("wait", None))

        def get_n_slices(self):
            self.events.append(("count", None))
            return self.count

        def select_all_slices(self):
            self.events.append(("select", None))

        def set_slices_thickness_match_neighbors(self):
            self.events.append(("thickness", None))

    def test_state_restore_crosses_task_barrier_before_counting(self):
        abba = self.FakeAbba()
        renderer._restore_state_and_wait(abba, "state.abba")
        self.assertEqual(abba.events, [
            ("state_load", "state.abba"), ("wait", None), ("count", None)
        ])

    def test_failed_state_load_never_enters_task_queue_or_exports(self):
        abba = self.FakeAbba(loaded=False)
        with self.assertRaisesRegex(Exception, "ABBAStateLoadCommand reported failure"):
            renderer._restore_state_and_wait(abba, "state.abba")
        self.assertEqual(abba.events, [("state_load", "state.abba")])

    def test_export_preparation_waits_after_thickness_command(self):
        abba = self.FakeAbba()
        renderer._prepare_slices_for_export_and_wait(abba)
        self.assertEqual(abba.events, [
            ("select", None), ("thickness", None), ("wait", None)
        ])


class NativeTransformRoundtripTests(unittest.TestCase):
    @staticmethod
    def tps(value: float, interval_min=None) -> dict:
        return {
            "type": "BoundedRealTransform",
            "realTransform": {"type": "ThinplateSplineTransform",
                              "srcPts": [[value]], "tgtPts": [[value + 1.0]]},
            "interval_min": interval_min or [0.0, 0.0, 0.0],
            "interval_max": [1.0, 1.0, 1.0],
        }

    @staticmethod
    def write_state(path: Path, transforms: list[dict]) -> None:
        slices = []
        for transform in transforms:
            slices.append({"actions": [{
                "type": "RegisterSliceAction",
                "registration": {"type": "SacBigWarp2DRegistration",
                                 "transform": json.dumps(transform)},
            }]})
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("state.json", json.dumps({"slices_state_list": slices}))

    def test_fingerprint_ignores_transform_json_formatting(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transform = self.tps(1.0)
            self.write_state(first, [transform])
            self.write_state(second, [transform])
            left = renderer._registration_fingerprints(first)[0]
            right = renderer._registration_fingerprints(second)[0]
            self.assertEqual(left["deformation_sha256"], right["deformation_sha256"])

    def test_roundtrip_rejects_any_changed_native_transform(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transforms = [self.tps(float(index)) for index in range(588)]
            changed = list(transforms)
            changed[123] = self.tps(-1.0)
            self.write_state(first, transforms)
            self.write_state(second, changed)
            diff = Path(temporary) / "diff.json"
            with self.assertRaisesRegex(Exception, r"1/588.*first: \[123\]"):
                renderer._verify_transform_roundtrip(first, second, diff)
            self.assertEqual(json.loads(diff.read_text())["deformation_mismatch_count"], 1)

    def test_roundtrip_reports_identical_and_copied_transforms(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transforms = [self.tps(float(index // 2)) for index in range(588)]
            self.write_state(first, transforms)
            self.write_state(second, transforms)
            result = renderer._verify_transform_roundtrip(
                first, second, Path(temporary) / "diff.json")
            self.assertTrue(result["verified"])
            self.assertEqual(result["transform_count"], 588)
            self.assertEqual(result["unique_transform_count"], 294)
            self.assertEqual(result["unique_deformation_count"], 294)
            self.assertEqual(result["copied_deformation_count"], 294)

    def test_roundtrip_allows_source_dependent_bounded_interval_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transforms = [{
                "type": "BoundedRealTransform",
                "realTransform": {"type": "ThinplateSplineTransform",
                                  "srcPts": [[float(index)]],
                                  "tgtPts": [[float(index + 1)]]},
                "interval_min": [0.0, 0.0, 0.0],
                "interval_max": [1.0, 1.0, 1.0],
            } for index in range(588)]
            rebound = [dict(transform, interval_min=[-2.0, -3.0, -4.0])
                       for transform in transforms]
            self.write_state(first, transforms)
            self.write_state(second, rebound)
            result = renderer._verify_transform_roundtrip(
                first, second, Path(temporary) / "diff.json")
            self.assertTrue(result["verified"])
            self.assertEqual(result["source_dependent_wrapper_change_count"], 588)
            self.assertEqual(
                result["criterion"],
                "deformation_landmarks",
            )

    def test_last_ulp_bound_noise_is_not_reported_as_a_changed_interval(self):
        # Moving the fixed source off origin zero perturbs the recomputed bounds
        # by ~2e-15 mm. Exact float equality reported all 588 sources as changed
        # and buried the real differences in a 0.9 MB diff.
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transforms = [self.tps(float(index)) for index in range(588)]
            noisy = [dict(transform, interval_min=[0.0, 0.0, 1.7763568394002505e-15])
                     for transform in transforms]
            self.write_state(first, transforms)
            self.write_state(second, noisy)
            result = renderer._verify_transform_roundtrip(
                first, second, Path(temporary) / "diff.json")
            self.assertTrue(result["verified"])
            self.assertEqual(result["bounds_changed_source_count"], 0)
            self.assertLess(result["bounds_max_abs_delta_mm"], renderer.BOUNDS_TOLERANCE_MM)
            self.assertEqual(
                json.loads((Path(temporary) / "diff.json").read_text())["differences"], [])

    def test_real_bound_change_is_still_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.abba"
            second = Path(temporary) / "second.abba"
            transforms = [self.tps(float(index)) for index in range(588)]
            moved = [dict(transform, interval_min=[0.0, 0.0, -2.0])
                     for transform in transforms]
            self.write_state(first, transforms)
            self.write_state(second, moved)
            result = renderer._verify_transform_roundtrip(
                first, second, Path(temporary) / "diff.json")
            self.assertTrue(result["verified"])
            self.assertEqual(result["bounds_changed_source_count"], 588)
            self.assertEqual(result["bounds_max_abs_delta_mm"], 2.0)

    def test_existing_roundtrip_artifact_is_cleared_before_native_save(self):
        # ABBA refuses to overwrite; a leftover artifact from an earlier build
        # otherwise fails every rebuild at state_save before anything renders.
        with tempfile.TemporaryDirectory() as temporary:
            authoritative = Path(temporary) / "state.abba"
            destination = Path(temporary) / "reports" / "native_state_roundtrip.abba"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"stale artifact from a previous build")
            transforms = [self.tps(float(index)) for index in range(588)]
            self.write_state(authoritative, transforms)

            observed = {}

            class FakeAbba:
                def state_save(self, state_file):
                    observed["existed_at_save"] = destination.exists()
                    NativeTransformRoundtripTests.write_state(destination, transforms)
                    return True

                def wait_for_end_of_tasks(self):
                    pass

            result = renderer._save_and_verify_state_roundtrip(
                FakeAbba(), authoritative, destination, Path(temporary) / "diff.json")
            self.assertFalse(observed["existed_at_save"])
            self.assertTrue(result["verified"])

    def test_slice_state_audit_failure_does_not_block_native_export(self):
        with mock.patch.object(
            renderer, "_save_and_verify_state_roundtrip",
            return_value={"verified": True},
        ), mock.patch.object(
            renderer, "_audit_native_slice_state",
            side_effect=RuntimeError("optimizer accessor unavailable"),
        ):
            roundtrip, slice_state, warnings = renderer._collect_state_diagnostics(
                mock.Mock(), Path("input.abba"), Path("output.abba")
            )
        self.assertTrue(roundtrip["verified"])
        self.assertFalse(slice_state["verified"])
        self.assertIn("optimizer accessor unavailable", warnings[0])


class NativeGridPlacementTests(unittest.TestCase):
    def test_bdv_transform_places_smaller_native_raster_on_target_grid(self):
        import sys
        import types
        import numpy as np
        class Transform:
            values = [[0.04, 0.0, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[0] + 0.04],
                      [0.0, 0.04, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[1]],
                      [0.0, 0.0, 0.04, renderer.TARGET_ORIGIN_XYZ_MM[2]]]
            def get(self, row, column): return self.values[row][column]
        fake_scyjava = types.SimpleNamespace(jimport=lambda name: Transform)
        rai = mock.Mock()
        rai.dimension.side_effect = [3, 2, 2]
        source = mock.Mock()
        source.getSource.return_value = rai
        source.getSourceTransform.side_effect = lambda time, level, transform: None
        sac = mock.Mock()
        sac.getSpimSource.return_value = source
        ij = mock.Mock()
        payload = np.arange(12, dtype=np.uint16).reshape(2, 2, 3)
        ij.py.from_java.return_value = payload
        diagnostics = {}
        with mock.patch.dict(sys.modules, {"scyjava": fake_scyjava}):
            result = renderer._source_to_ap_si_lr(ij, sac, diagnostics)
        self.assertEqual(result.shape, renderer.TARGET_SHAPE)
        np.testing.assert_array_equal(result[0:2, 0:2, 1:4], payload)
        self.assertEqual(int(result[:, :, 0].sum()), 0)
        self.assertEqual(diagnostics["native_array_shape_ap_si_lr"], [2, 2, 3])
        self.assertEqual(diagnostics["native_start_ap_si_lr_voxels"], [0.0, 0.0, 1.0])

    def test_half_voxel_native_origin_is_linearly_resampled_not_rounded(self):
        import sys
        import types
        import numpy as np
        class Transform:
            values = [[0.04, 0.0, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[0] - 0.02],
                      [0.0, 0.04, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[1]],
                      [0.0, 0.0, 0.04, renderer.TARGET_ORIGIN_XYZ_MM[2]]]
            def get(self, row, column): return self.values[row][column]
        fake_scyjava = types.SimpleNamespace(jimport=lambda name: Transform)
        rai = mock.Mock()
        rai.dimension.side_effect = [4, 2, 2]
        source = mock.Mock()
        source.getSource.return_value = rai
        source.getSourceTransform.side_effect = lambda time, level, transform: None
        sac = mock.Mock()
        sac.getSpimSource.return_value = source
        ij = mock.Mock()
        payload = np.zeros((2, 2, 4), dtype=np.float32)
        payload[:, :, 1] = 10
        payload[:, :, 2] = 20
        ij.py.from_java.return_value = payload
        with mock.patch.dict(sys.modules, {"scyjava": fake_scyjava}):
            result = renderer._source_to_ap_si_lr(ij, sac)
        # Target LR=0 samples source LR=0.5: a true half-voxel interpolation.
        np.testing.assert_allclose(result[0:2, 0:2, 0], 5.0)
        np.testing.assert_allclose(result[0:2, 0:2, 1], 15.0)

    def test_ap_sampling_uses_one_native_section_without_brightness_blending(self):
        import sys
        import types
        import numpy as np
        class Transform:
            values = [[0.04, 0.0, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[0]],
                      [0.0, 0.04, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[1]],
                      [0.0, 0.0, 0.04, 0.02]]
            def get(self, row, column): return self.values[row][column]
        fake_scyjava = types.SimpleNamespace(jimport=lambda name: Transform)
        rai = mock.Mock()
        rai.dimension.side_effect = [2, 2, 3]
        source = mock.Mock()
        source.getSource.return_value = rai
        source.getSourceTransform.side_effect = lambda time, level, transform: None
        sac = mock.Mock()
        sac.getSpimSource.return_value = source
        ij = mock.Mock()
        payload = np.stack([
            np.full((2, 2), 10, dtype=np.float32),
            np.full((2, 2), 100, dtype=np.float32),
            np.full((2, 2), 200, dtype=np.float32),
        ])
        ij.py.from_java.return_value = payload
        with mock.patch.dict(sys.modules, {"scyjava": fake_scyjava}):
            result = renderer._source_to_ap_si_lr(ij, sac)
        # AP target 1 maps to native coordinate 0.5. Tie-breaking selects one
        # complete section (index 1), never an artificial 55-intensity blend.
        np.testing.assert_array_equal(result[1, :2, :2], payload[1])

    def test_target_world_origin_centres_voxel_centres_and_is_applied_by_abba_map(self):
        self.assertEqual(renderer.TARGET_ORIGIN_XYZ_MM, (-8.16, -5.7, 0.0))
        vendor = (Path(__file__).parents[1] / "vendor/abba_python_0_11_0/abba_map.py").read_text(encoding="utf-8")
        self.assertIn("affine_transform = AffineTransform3D()", vendor)
        self.assertIn("affine_transform.scale", vendor)
        self.assertIn("abba_world_origin_xyz_mm", vendor)
        self.assertIn("affine_transform.set(JDouble(value), axis, 3)", vendor)

    def test_empty_registered_planes_are_reported_without_filling_or_failure(self):
        import numpy as np
        volume = np.ones((4, 2, 2), dtype=np.uint16)
        volume[2] = 0
        before = volume.copy()
        self.assertEqual(renderer._registered_blank_planes(volume, np.array([1, 2, 3])), [2])
        np.testing.assert_array_equal(volume, before)

    def test_renderer_uses_native_neighbor_thickness_before_export(self):
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(encoding="utf-8")
        state_load_at = source.index("loaded = abba.state_load")
        first_wait_at = source.index("abba.wait_for_end_of_tasks()", state_load_at)
        select_at = source.index("abba.select_all_slices()")
        thickness_at = source.index("abba.set_slices_thickness_match_neighbors()")
        second_wait_at = source.index("abba.wait_for_end_of_tasks()", first_wait_at + 1)
        export_at = source.index("abba.export_resampled_slices_to_bdv_source(")
        self.assertLess(state_load_at, first_wait_at)
        self.assertLess(first_wait_at, select_at)
        self.assertLess(select_at, thickness_at)
        self.assertLess(thickness_at, second_wait_at)
        self.assertLess(second_wait_at, export_at)
        vendor = (Path(__file__).parents[1] / "vendor/abba_python_0_11_0/abba.py").read_text(encoding="utf-8")
        self.assertIn("def wait_for_end_of_tasks(self):", vendor)
        self.assertIn("self.mp.waitForTasks()", vendor)
        # The margin value itself is pinned by NativeExportZGridTests; here only
        # its presence in the export call matters.
        self.assertIn("margin_z=NATIVE_EXPORT_MARGIN_Z_UM", source)
        self.assertIn("nearest_native_plane_no_inter_slice_intensity_blending", source)


class PinnedMovingPlaneTests(unittest.TestCase):
    """The registration must be fed the images it was actually built against."""

    def test_pinned_planes_and_manifest_are_complete_and_intact(self):
        manifest = json.loads(renderer.MOVING_PLANE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["plane_count"], 588)
        self.assertEqual(manifest["waxholm_ap_range"], [189, 776])
        self.assertEqual(manifest["plane_dtype"], "uint8")
        names = [f"whs_nissl_40um_ap_{ap}.tiff" for ap in range(189, 777)]
        self.assertEqual(sorted(manifest["planes"]), sorted(names))
        for offset in (0, 293, 587):          # spot-check the pinned bytes
            name = names[offset]
            path = renderer.MOVING_PLANE_DIR / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(pipeline.sha256_file(path), manifest["planes"][name]["sha256"])

    def test_materialization_verifies_hashes_and_scales_to_uint16(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            paths, diagnostics = renderer._single_plane_tiffs(folder)
            self.assertEqual(len(paths), 588)
            self.assertEqual(paths[0].name, "whs_nissl_40um_ap_189.tiff")
            self.assertEqual(paths[-1].name, "whs_nissl_40um_ap_776.tiff")
            written = tifffile.imread(paths[0])
            self.assertEqual(written.dtype, np.dtype(np.uint16))
            original = tifffile.imread(renderer.MOVING_PLANE_DIR / paths[0].name)
            np.testing.assert_array_equal(
                written, original.astype(np.uint16) * renderer.UINT8_TO_UINT16_SCALE)
            self.assertEqual(diagnostics[0]["source_id"], 0)
            self.assertEqual(diagnostics[0]["waxholm_ap"], 189)
            self.assertIn("pinned_sha256", diagnostics[0])

    def test_a_tampered_plane_stops_the_build(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as temporary:
            # renderer imports ch03_nissl_pipeline through sys.path while the
            # tests import src.ch03_nissl_pipeline; those are separate module
            # objects, so patch the one the renderer actually calls.
            with mock.patch.object(renderer.pipeline, "sha256_file", lambda path: "0" * 64):
                with self.assertRaisesRegex(Exception, "does not match the pinned"):
                    renderer._single_plane_tiffs(Path(temporary))

    def test_scale_maps_the_full_uint8_range_without_per_slice_normalization(self):
        self.assertEqual(255 * renderer.UINT8_TO_UINT16_SCALE, 65535)
        source = renderer.MOVING_PLANE_MANIFEST.read_text(encoding="utf-8")
        self.assertIn("never per-slice", source)

    def test_native_path_never_requires_the_waxholm_package(self):
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        # A 2.3 GB download must not be needed to restate provenance. The
        # experimental Python renderer in ch03_nissl_pipeline still uses it.
        self.assertNotIn("find_waxholm_source", source)
        for entrypoint in ("v34_debug_transform_roundtrip", "v35_debug_slice_geometry",
                           "v36_debug_export_parameters"):
            text = (Path(__file__).parents[1] / f"src/{entrypoint}.py").read_text(encoding="utf-8")
            self.assertNotIn("find_waxholm_source", text, entrypoint)

    def test_provenance_records_origin_without_reading_the_package(self):
        package_manifest = json.loads(
            (Path(__file__).parents[1]
             / "resources/optional_ch03/nissl_registration_0_3_0/registration_manifest.json"
             ).read_text(encoding="utf-8"))
        plane_manifest = renderer._load_moving_plane_manifest()
        report = renderer._moving_source_provenance(package_manifest, plane_manifest)
        self.assertFalse(report["brainglobe_package_required"])
        self.assertEqual(report["plane_count"], 588)
        self.assertEqual(report["ap_range"], [189, 776])
        self.assertEqual(report["derived_from_atlas_name"], "whs_sd_rat_39um")
        self.assertEqual(len(report["sha256"]), 64)
        # Stable identity: the same pinned planes always give the same digest.
        self.assertEqual(report["sha256"],
                         renderer._moving_source_provenance(package_manifest,
                                                            plane_manifest)["sha256"])

    def test_planes_are_not_derived_from_the_waxholm_volume(self):
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        materialize = source.split("def _single_plane_tiffs", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("tifffile.memmap(source)", materialize)
        self.assertIn("MOVING_PLANE_DIR", materialize)


class AlignmentGuardTests(unittest.TestCase):
    """Measure lateral drift every build; never silently correct it."""

    @staticmethod
    def _volume(shift_si: int, shift_lr: int):
        import numpy as np
        labels = np.zeros((3, 60, 60), dtype=np.uint16)
        volume = np.zeros_like(labels)
        labels[:, 20:40, 20:40] = 7
        volume[:, 20 + shift_si:40 + shift_si, 20 + shift_lr:40 + shift_lr] = 900
        return labels, volume

    def test_recovers_a_known_shift_and_touches_nothing(self):
        import numpy as np
        labels, volume = self._volume(0, 6)
        before = volume.copy()
        result = renderer._alignment_diagnostics(labels, volume, np.array([0, 1, 2]))
        self.assertEqual(result["median_shift_si_lr_voxels"], [0.0, 6.0])
        self.assertEqual(result["median_shift_si_lr_um"],
                         [0.0, 6 * renderer.VOXEL_SIZE_MM * 1000.0])
        self.assertFalse(result["pixels_modified"])
        np.testing.assert_array_equal(volume, before)

    def test_aligned_data_reports_no_shift(self):
        import numpy as np
        labels, volume = self._volume(0, 0)
        result = renderer._alignment_diagnostics(labels, volume, np.array([0, 1, 2]))
        self.assertEqual(result["max_abs_shift_um"], 0.0)
        self.assertLess(result["max_abs_shift_um"], renderer.ALIGNMENT_WARNING_UM)

    def test_guard_fires_on_a_visible_offset(self):
        import numpy as np
        labels, volume = self._volume(0, 15)   # 600 um at 40 um voxels
        result = renderer._alignment_diagnostics(labels, volume, np.array([0, 1, 2]))
        self.assertGreater(result["max_abs_shift_um"], renderer.ALIGNMENT_WARNING_UM)

    def test_threshold_is_ten_voxels_and_the_warning_says_nothing_moved(self):
        self.assertEqual(renderer.ALIGNMENT_WARNING_UM,
                         10 * renderer.VOXEL_SIZE_MM * 1000.0)
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("ALIGNMENT_REGRESSION", source)
        self.assertIn("Nothing was ", source)


class ReconstructionInstallStatusTests(unittest.TestCase):
    def test_reconstruction_records_whether_it_reached_an_atlas(self):
        # write_report() merges, so a persisted reconstruction block outlives
        # the run that wrote it. Without a status a failed install -- or a later
        # failed run -- leaves it reading as a complete success.
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        pending_at = source.index('report["install_status"] = "pending"')
        write_at = source.index('pipeline.write_report({"abba_reconstruction": report})')
        install_at = source.index("pipeline.install_channel(report)")
        installed_at = source.index('report["install_status"] = "installed"')
        self.assertLess(pending_at, write_at)
        self.assertLess(write_at, install_at)
        self.assertLess(install_at, installed_at)
        summary = (Path(__file__).parents[1] / "src/write_build_summary.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("install_status", summary)


class NativeExportZGridTests(unittest.TestCase):
    """The export margin sets the Z phase; a wrong one empties planes silently."""

    def test_export_uses_the_measured_margin_and_reports_it(self):
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(renderer.NATIVE_EXPORT_MARGIN_Z_UM, 60.0)
        self.assertIn("margin_z=NATIVE_EXPORT_MARGIN_Z_UM", source)
        self.assertNotIn("margin_z=40.0", source)
        self.assertIn('"native_export_margin_z_um": NATIVE_EXPORT_MARGIN_Z_UM', source)

    def test_margin_is_an_odd_multiple_of_the_half_voxel_phase_correction(self):
        # ABBA aligns the export box to slice boundaries, half a voxel off the
        # slice centres. Only an odd number of half-voxels restores phase 0;
        # a whole-voxel change (0 vs 40 um) measurably does nothing.
        half_voxel_um = renderer.VOXEL_SIZE_MM * 1000.0 / 2.0
        multiples = renderer.NATIVE_EXPORT_MARGIN_Z_UM / half_voxel_um
        self.assertAlmostEqual(multiples, round(multiples))
        self.assertEqual(round(multiples) % 2, 1)

    def test_summarizer_expects_the_same_margin_as_the_renderer(self):
        import summarize_native_abba_diagnostics as summary
        self.assertEqual(summary.CURRENT_EXPORT_MARGIN_Z_UM,
                         renderer.NATIVE_EXPORT_MARGIN_Z_UM)


class NativeExportCoverageEvidenceTests(unittest.TestCase):
    """Stage-2 evidence: what ABBA exported, before Python resamples anything."""

    def test_native_z_profile_and_plane_selection_are_recorded(self):
        import sys
        import types
        import numpy as np
        class Transform:
            # One native plane sits anterior to every registered target, so it
            # can never be addressed; the last target runs off the native stack.
            values = [[0.04, 0.0, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[0]],
                      [0.0, 0.04, 0.0, renderer.TARGET_ORIGIN_XYZ_MM[1]],
                      [0.0, 0.0, 0.04, renderer.TARGET_ORIGIN_XYZ_MM[2] - 0.04]]
            def get(self, row, column): return self.values[row][column]
        fake_scyjava = types.SimpleNamespace(jimport=lambda name: Transform)
        rai = mock.Mock()
        rai.dimension.side_effect = [3, 2, 3]
        source = mock.Mock()
        source.getSource.return_value = rai
        source.getSourceTransform.side_effect = lambda time, level, transform: None
        sac = mock.Mock()
        sac.getSpimSource.return_value = source
        ij = mock.Mock()
        payload = np.ones((3, 2, 3), dtype=np.uint16)
        payload[2] = 0  # an all-zero plane inside ABBA's own export
        ij.py.from_java.return_value = payload
        diagnostics = {}
        with mock.patch.dict(sys.modules, {"scyjava": fake_scyjava}):
            renderer._source_to_ap_si_lr(ij, sac, diagnostics)

        self.assertEqual(diagnostics["native_start_ap_si_lr_voxels"][0], -1.0)
        profile = diagnostics["native_plane_intensity_diagnostics"]
        self.assertEqual([item["native_ap_index"] for item in profile], [0, 1, 2])
        self.assertEqual([item["nonzero_pixels"] for item in profile], [6, 6, 0])
        self.assertEqual(profile[2]["target_ap_coordinate"], 1.0)

        selection = {item["target_ap"]: item for item in diagnostics["native_plane_selection"]}
        self.assertEqual(selection[0]["native_ap_index"], 1)
        self.assertTrue(selection[0]["within_native_range"])
        self.assertFalse(selection[2]["within_native_range"])
        self.assertEqual(diagnostics["unused_native_ap_indices"], [0])

    def test_blank_planes_are_attributed_to_the_stage_that_produced_them(self):
        import numpy as np
        grid = {
            "native_plane_selection": [
                {"target_ap": 10, "native_ap_index": 0, "within_native_range": True},
                {"target_ap": 11, "native_ap_index": 1, "within_native_range": True},
                {"target_ap": 12, "native_ap_index": 2, "within_native_range": True},
                {"target_ap": 13, "native_ap_index": 3, "within_native_range": False},
            ],
            "native_plane_intensity_diagnostics": [
                {"native_ap_index": 0, "nonzero_pixels": 0, "maximum": 0.0},
                {"native_ap_index": 1, "nonzero_pixels": 50, "maximum": 9.0},
                {"native_ap_index": 2, "nonzero_pixels": 0, "maximum": 0.0},
            ],
            "unused_native_ap_indices": [],
        }
        source_diagnostics = [
            {"source_id": 0, "nonzero_pixels": 100},
            {"source_id": 1, "nonzero_pixels": 100},
            {"source_id": 2, "nonzero_pixels": 0},
            {"source_id": 3, "nonzero_pixels": 100},
        ]
        result = renderer._classify_blank_registered_planes(
            grid, np.array([10, 11, 12, 13]), [10, 11, 12, 13], source_diagnostics)

        self.assertEqual(result["blank_registered_plane_count"], 4)
        self.assertEqual(result["empty_waxholm_source_count"], 1)
        self.assertEqual(result["native_export_empty_count"], 1)
        self.assertEqual(result["sampling_loss_count"], 1)
        self.assertEqual(result["no_native_plane_count"], 1)
        self.assertEqual(result["native_empty_plane_count"], 2)
        self.assertEqual(result["native_export_empty"][0]["target_ap"], 10)
        self.assertEqual(result["sampling_loss"][0]["target_ap"], 11)
        self.assertEqual(result["sampling_loss"][0]["native_plane_nonzero_pixels"], 50)
        self.assertEqual(result["empty_waxholm_source"][0]["waxholm_ap"], 2 + 189)

    def test_classification_never_fills_or_modifies_a_plane(self):
        import numpy as np
        volume = np.zeros((3, 2, 2), dtype=np.uint16)
        before = volume.copy()
        renderer._classify_blank_registered_planes(
            {}, np.array([0, 1, 2]), [0, 1, 2],
            [{"source_id": index, "nonzero_pixels": 5} for index in range(3)])
        np.testing.assert_array_equal(volume, before)


class FixedAtlasViewTests(unittest.TestCase):
    def test_runtime_view_exposes_ap_si_lr_arrays_as_asr_without_permutation(self):
        atlas = mock.Mock()
        atlas.orientation = "pil"
        atlas.metadata = {"orientation": "pil", "resolution": [40, 40, 40]}
        atlas.annotation = object()
        view = renderer._AbbaAtlasView(atlas)
        self.assertEqual(view.orientation, "asr")
        self.assertEqual(view.metadata["orientation"], "asr")
        self.assertEqual(view.metadata["abba_world_origin_xyz_mm"], [-8.16, -5.7, 0.0])
        self.assertIs(view.annotation, atlas.annotation)
        self.assertEqual(atlas.metadata["orientation"], "pil")


class NativeSliceStateAuditTests(unittest.TestCase):
    """The old audit called getTolerance()/getMaxIteration(); neither exists."""

    @staticmethod
    def _abba(count=588, spacing=renderer.VOXEL_SIZE_MM, registrations=1):
        method = mock.Mock()
        method.getParameterCount.return_value = 0
        method.getName.return_value = "getSlicingAxisPosition"
        method.getReturnType.return_value.getName.return_value = "double"
        slices = []
        for index in range(count):
            slice_source = mock.Mock()
            slice_source.getSlicingAxisPosition.return_value = 2.194 + index * spacing
            slice_source.getThicknessInMm.return_value = 0.001
            slice_source.getNumberOfRegistrations.return_value = registrations
            slice_source.getClass.return_value.getMethods.return_value = [method]
            slices.append(slice_source)
        abba = mock.Mock()
        abba.mp.getSlices.return_value = slices
        return abba

    def test_audit_verifies_the_restored_slice_lattice(self):
        audit = renderer._audit_native_slice_state(self._abba())
        self.assertTrue(audit["verified"])
        self.assertEqual(audit["slice_count"], 588)
        self.assertTrue(audit["slicing_axis_spacing_uniform"])
        self.assertAlmostEqual(audit["slicing_axis_spacing_mm"], renderer.VOXEL_SIZE_MM)
        self.assertEqual(audit["registrations_per_slice"], [1])
        self.assertEqual(audit["discovered_scalar_getters"], ["getSlicingAxisPosition"])

    def test_audit_states_that_optimizer_settings_are_not_exposed(self):
        audit = renderer._audit_native_slice_state(self._abba())
        self.assertFalse(audit["iterative_inverse_settings_available"])
        source = (Path(__file__).parents[1] / "src/native_abba_renderer.py").read_text(
            encoding="utf-8"
        )
        # The docstring names the dead API deliberately; what must not come back
        # is a call to it, so inspect the executable body only.
        body = source.split("def _audit_native_slice_state", 1)[1].split('"""', 2)[2]
        body = body.split("\ndef ", 1)[0]
        self.assertNotIn("getTolerance", body)
        self.assertNotIn("getMaxIteration", body)

    def test_a_slice_without_a_registration_is_rejected(self):
        with self.assertRaisesRegex(Exception, "NATIVE_SLICE_STATE"):
            renderer._audit_native_slice_state(self._abba(registrations=0))

    def test_a_wrong_slice_count_is_rejected(self):
        with self.assertRaisesRegex(Exception, "expected 588 restored slices"):
            renderer._audit_native_slice_state(self._abba(count=587))

    def test_a_non_uniform_slicing_axis_is_reported_not_hidden(self):
        audit = renderer._audit_native_slice_state(self._abba(spacing=0.05))
        self.assertFalse(audit["slicing_axis_spacing_uniform"])
