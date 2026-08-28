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
        self.assertEqual(state.registrations[0].source_affine[:4], (.039, 0., 0., -9.984))
        self.assertTrue(state.report["bdv_pixel_to_world_affines_applied"])

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
        source_affine = (.04, 0, 0, -.1, 0, .04, 0, -.1, 0, 0, .001, -.0005)
        return abba_nissl.Registration(
            0, 189, tuple(affine or [1,0,0,0, 0,1,0,0, 0,0,1,0]),
            points, points, "x", source_affine,
        )

    def test_identity_spline_and_affine(self):
        source = np.arange(25, dtype=np.uint16).reshape(5, 5)
        output = abba_nissl.render_plane(source, self.registration(), (3, 3))
        self.assertEqual(output.shape, (3, 3))
        self.assertEqual(output[1, 1], source[2, 2])

    def test_iterative_inverse_recovers_nonlinear_forward_tps(self):
        source = np.array([[-1., -1.], [1., -1.], [-1., 1.], [1., 1.], [0., 0.]])
        target = source.copy(); target[-1] = [.25, -.2]
        radial, affine = abba_nissl._fit_tps(source, target)
        samples = np.array([[-.4, .2], [.3, -.5], [.1, .4]])
        warped = abba_nissl._apply_tps(samples, source, radial, affine)
        recovered = abba_nissl.invert_bigwarp_tps(warped, source, target)
        np.testing.assert_allclose(recovered, samples, atol=1e-6)

    def test_noninvertible_boundary_pixels_are_zero_and_reported(self):
        source = np.full((5, 5), 100, dtype=np.uint16)
        diagnostics = {}
        def inverse_with_one_undefined(target, *args, **kwargs):
            result = target.copy()
            result[0] = np.nan
            return result
        with mock.patch.object(abba_nissl, "invert_bigwarp_tps", side_effect=inverse_with_one_undefined):
            output = abba_nissl.render_plane(source, self.registration(), (3, 3), diagnostics)
        self.assertEqual(output.flat[0], 0)
        self.assertEqual(diagnostics["noninvertible_target_pixels"], 1)

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
