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


class NativeGridPlacementTests(unittest.TestCase):
    def test_bdv_transform_places_smaller_native_raster_on_target_grid(self):
        import sys
        import types
        import numpy as np
        class Transform:
            values = [[0.04, 0.0, 0.0, 0.04], [0.0, 0.04, 0.0, 0.0], [0.0, 0.0, 0.04, 0.0]]
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
