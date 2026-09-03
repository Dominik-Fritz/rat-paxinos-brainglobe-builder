from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

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

    def test_normal_windows_path_never_calls_python_tps_builder(self):
        batch = (Path(__file__).parents[1] / "run_builder.bat").read_text(encoding="utf-8")
        self.assertIn('src\\native_abba_renderer.py', batch)
        self.assertNotIn('ch03_nissl_pipeline.py" build-from-package', batch)
        self.assertIn("VISUAL_VALIDATION_PENDING", batch)
        self.assertIn('set "BUILD_WARNINGS=YES"', batch)


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
        with mock.patch.dict(sys.modules, {"scyjava": fake_scyjava}):
            result = renderer._source_to_ap_si_lr(ij, sac)
        self.assertEqual(result.shape, renderer.TARGET_SHAPE)
        np.testing.assert_array_equal(result[0:2, 0:2, 1:4], payload)
        self.assertEqual(int(result[:, :, 0].sum()), 0)

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

    def test_target_world_origin_matches_vendored_scale_only_abba_map(self):
        self.assertEqual(renderer.TARGET_ORIGIN_XYZ_MM, (0.0, 0.0, 0.0))
        vendor = (Path(__file__).parents[1] / "vendor/abba_python_0_11_0/abba_map.py").read_text(encoding="utf-8")
        self.assertIn("affine_transform = AffineTransform3D()", vendor)
        self.assertIn("affine_transform.scale", vendor)
        self.assertNotIn("affine_transform.translate", vendor)

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


class FixedAtlasViewTests(unittest.TestCase):
    def test_runtime_view_exposes_ap_si_lr_arrays_as_asr_without_permutation(self):
        atlas = mock.Mock()
        atlas.orientation = "pil"
        atlas.metadata = {"orientation": "pil", "resolution": [40, 40, 40]}
        atlas.annotation = object()
        view = renderer._AbbaAtlasView(atlas)
        self.assertEqual(view.orientation, "asr")
        self.assertEqual(view.metadata["orientation"], "asr")
        self.assertIs(view.annotation, atlas.annotation)
        self.assertEqual(atlas.metadata["orientation"], "pil")
