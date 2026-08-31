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
