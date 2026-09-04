#!/usr/bin/env python3
"""Run only the native ABBA state load/save landmark audit."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import native_abba_renderer as renderer
import native_abba_runtime as runtime
import ch03_nissl_pipeline as pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args()
    package = Path(args.package).resolve()
    manifest = pipeline.load_package_manifest(package)
    authoritative = package / manifest["abba_state_file"]
    runtime.inspect_state(authoritative)
    paths = runtime.RuntimePaths()
    paths.create()
    work = Path(tempfile.mkdtemp(prefix="native-roundtrip-", dir=paths.temporary))
    try:
        source, _ = pipeline.find_waxholm_source(manifest)
        planes, _ = renderer._single_plane_tiffs(source, work / "moving_sources")
        rebound = paths.reports / "rebound_state.abba"
        renderer.build_rebound_state(authoritative, planes, rebound)
        ij, _ = runtime.initialize_native_api(paths)
        abba, _ = renderer._open_fixed_abba(ij, renderer._atlas_name())
        renderer._restore_state_and_wait(abba, renderer._java_file(rebound))
        result = renderer._save_and_verify_state_roundtrip(
            abba,
            authoritative,
            paths.reports / "native_state_roundtrip.abba",
            paths.reports / "transform_roundtrip_diff.json",
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
