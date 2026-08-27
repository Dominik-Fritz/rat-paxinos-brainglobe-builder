from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import numpy as np

from src import abba_nissl


REAL = Path(__file__).parents[1] / "resources/optional_ch03/nissl_registration_0_3_0/final_for_V_0_3.abba"


def repack(root: Path, mutate=None, omit=None) -> tuple[Path, str]:
    with zipfile.ZipFile(REAL) as original:
        members = {name: original.read(name) for name in original.namelist() if name != omit}
    if mutate:
        name, function = mutate
        value = json.loads(members[name]); function(value)
        members[name] = json.dumps(value).encode()
    target = root / "test.abba"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in members.items(): output.writestr(name, data)
    return target, abba_nissl.sha256_file(target)


class AbbaValidationTests(unittest.TestCase):
    def test_real_state_is_complete(self):
        state = abba_nissl.validate_abba(REAL)
        self.assertEqual(len(state.registrations), 588)
        self.assertGreater(state.report["copied_registration_planes"], 0)

    def test_wrong_hash(self):
        with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_HASH_MISMATCH"):
            abba_nissl.validate_abba(REAL, "0" * 64)

    def test_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.abba"; path.write_bytes(b"not zip")
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_CORRUPT"):
                abba_nissl.validate_abba(path, abba_nissl.sha256_file(path))

    def test_missing_component(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest = repack(Path(folder), omit="sources.json")
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_COMPONENTS"):
                abba_nissl.validate_abba(path, digest)

    def test_duplicate_source_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest = repack(Path(folder), ("sources.json", lambda value: value[1].update(source_id=0)))
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_SOURCE_ID"):
                abba_nissl.validate_abba(path, digest)

    def test_wrong_ap_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            path, digest = repack(Path(folder), ("sources.json", lambda value: value[0].update(source_name="wrong.tiff")))
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_AP_NAME"):
                abba_nissl.validate_abba(path, digest)

    def test_unknown_action(self):
        def change(value): value["slices_state_list"][0]["actions"][0]["type"] = "GuessTransformAction"
        with tempfile.TemporaryDirectory() as folder:
            path, digest = repack(Path(folder), ("state.json", change))
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "ABBA_ACTION_TYPE"):
                abba_nissl.validate_abba(path, digest)


class RenderTests(unittest.TestCase):
    def registration(self, affine=None):
        points = np.array([[-1., -1.], [1., -1.], [-1., 1.], [1., 1.]])
        return abba_nissl.Registration(0, 189, tuple(affine or [1,0,0,0, 0,1,0,0, 0,0,1,0]), points, points, "x")

    def test_identity_spline_and_affine(self):
        source = np.arange(25, dtype=np.uint16).reshape(5, 5)
        output = abba_nissl.render_plane(source, self.registration(), (3, 3))
        self.assertEqual(output.shape, (3, 3))
        self.assertEqual(output[1, 1], source[2, 2])

    def test_memory_and_enospc_are_classified(self):
        with mock.patch("numpy.memmap", side_effect=MemoryError):
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "MEMORY_EXHAUSTED"):
                abba_nissl.allocate_memmap(Path("x"), (1,))
        with mock.patch("numpy.memmap", side_effect=OSError(28, "full")):
            with self.assertRaisesRegex(abba_nissl.NisslBuildError, "STORAGE_EXHAUSTED"):
                abba_nissl.allocate_memmap(Path("x"), (1,))

    def test_comparison_includes_ap150_without_claim(self):
        report = abba_nissl.compare_reference(np.zeros((151, 1, 1)), np.ones((151, 1, 1)))
        self.assertIn("150", report["selected_planes"])
        self.assertIn("not asserted", report["scientific_equivalence"])


if __name__ == "__main__": unittest.main()
