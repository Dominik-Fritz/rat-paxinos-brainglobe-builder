#!/usr/bin/env python3
"""Measure native ABBA slice geometry around the export-thickness action.

Read-only probe. It restores the authoritative state exactly as the renderer
does, then records slice selection and per-slice geometry at three points:
after the state load, after selecting all slices, and after the
match-neighbours thickness action. Nothing is exported or installed.

Motivation: the instrumented build proved that 150 of 151 missing planes were
already empty in ABBA's own BDV export, and that the export carries ~0.487 of
the source intensity. Both are what one expects when the slices do not fill the
40 um export voxel, i.e. when set_slices_thickness_match_neighbors() did not
take effect. The renderer discards that command's return value and selects
slices immediately before calling it, so neither its success nor the selection
is currently verified.

Method names are discovered by reflection rather than assumed: an earlier audit
in this project failed because SliceSources has no getTolerance().
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import native_abba_renderer as renderer
import native_abba_runtime as runtime
import ch03_nissl_pipeline as pipeline

NUMERIC_JAVA_TYPES = {
    "double", "float", "int", "long", "boolean", "short", "byte",
    "java.lang.Double", "java.lang.Float", "java.lang.Integer",
    "java.lang.Long", "java.lang.Boolean", "java.lang.String",
}


def _numeric_getters(slice_source) -> list[str]:
    """Discover zero-argument scalar getters actually present on SliceSources."""
    names = set()
    for method in slice_source.getClass().getMethods():
        if int(method.getParameterCount()) != 0:
            continue
        name = str(method.getName())
        if not (name.startswith("get") or name.startswith("is")):
            continue
        if str(method.getReturnType().getName()) in NUMERIC_JAVA_TYPES:
            names.add(name)
    return sorted(names)


def _read_geometry(slice_source, getters: list[str]) -> dict:
    values = {}
    for name in getters:
        try:
            value = getattr(slice_source, name)()
        except Exception as exc:  # a getter may throw for an unloaded slice
            values[name] = f"<error: {type(exc).__name__}: {exc}>"
            continue
        try:
            values[name] = float(value)
        except (TypeError, ValueError):
            values[name] = str(value)
    return values


def _snapshot(abba, getters: list[str], stage: str) -> dict:
    slices = list(abba.mp.getSlices())
    try:
        selected = int(len(list(abba.mp.getSelectedSources())))
    except Exception as exc:
        selected = f"<unavailable: {exc}>"
    geometry = [{"source_id": index, **_read_geometry(slice_source, getters)}
                for index, slice_source in enumerate(slices)]
    distinct = {}
    for name in getters:
        values = [entry[name] for entry in geometry if isinstance(entry.get(name), float)]
        if values:
            unique = sorted(set(values))
            distinct[name] = {
                "unique_count": len(unique),
                "min": unique[0],
                "max": unique[-1],
                "first_five_unique": unique[:5],
            }
    return {"stage": stage, "slice_count": len(slices), "selected_slice_count": selected,
            "per_getter_summary": distinct, "per_slice_geometry": geometry}


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
    work = Path(tempfile.mkdtemp(prefix="native-geometry-", dir=paths.temporary))
    try:
        planes, _ = renderer._single_plane_tiffs(work / "moving_sources")
        rebound = paths.reports / "rebound_state.abba"
        renderer.build_rebound_state(authoritative, planes, rebound)
        ij, _ = runtime.initialize_native_api(paths)
        abba, _ = renderer._open_fixed_abba(ij, renderer._atlas_name())
        renderer._restore_state_and_wait(abba, renderer._java_file(rebound))

        first = list(abba.mp.getSlices())[0]
        getters = _numeric_getters(first)
        stages = [_snapshot(abba, getters, "after_state_load")]

        # The renderer selects and applies thickness back to back. Separate the
        # two here, with an explicit barrier after each, so a lost selection and
        # an ineffective thickness command cannot be confused.
        abba.select_all_slices()
        abba.wait_for_end_of_tasks()
        stages.append(_snapshot(abba, getters, "after_select_all_and_wait"))

        thickness_result = abba.set_slices_thickness_match_neighbors()
        abba.wait_for_end_of_tasks()
        stages.append(_snapshot(abba, getters, "after_thickness_match_neighbors_and_wait"))

        report = {
            "probe": "native slice geometry around the export thickness action",
            "read_only": True,
            "abba_state_sha256": runtime.STATE_SHA256,
            "abba_version": runtime.ABBA_VERSION,
            "discovered_slice_getters": getters,
            "thickness_command_result": str(thickness_result),
            "stages": stages,
        }
        destination = paths.reports / "slice_geometry_probe.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(json.dumps({
        "report": str(destination),
        "discovered_slice_getters": getters,
        "thickness_command_result": str(thickness_result),
        "stages": [{"stage": stage["stage"], "slice_count": stage["slice_count"],
                    "selected_slice_count": stage["selected_slice_count"],
                    "per_getter_summary": stage["per_getter_summary"]}
                   for stage in stages],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
