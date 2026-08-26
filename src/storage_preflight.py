#!/usr/bin/env python3
"""Report free space on every volume used by the incremental Windows build."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

WARN_GIB = 10.0
FAIL_GIB = 0.5


def volume_key(path: Path) -> str:
    resolved = path.resolve()
    return (resolved.drive or resolved.anchor or str(resolved)).casefold()


def inspect_locations(locations: list[tuple[str, Path]], warn_gib: float = WARN_GIB,
                      fail_gib: float = FAIL_GIB) -> dict:
    volumes: dict[str, dict] = {}
    for role, path in locations:
        resolved = path.expanduser().resolve()
        probe = resolved
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        key = volume_key(probe)
        entry = volumes.setdefault(key, {"volume": resolved.drive or resolved.anchor,
                                         "roles": [], "paths": [], "free_bytes": None,
                                         "free_gib": None, "status": "ok"})
        entry["roles"].append(role)
        entry["paths"].append(str(resolved))
        try:
            free = shutil.disk_usage(probe).free
            entry["free_bytes"] = free
            entry["free_gib"] = round(free / 1024**3, 2)
            if free < fail_gib * 1024**3:
                entry["status"] = "failed"
            elif free < warn_gib * 1024**3 and entry["status"] != "failed":
                entry["status"] = "warning"
        except OSError as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
    overall = "failed" if any(v["status"] == "failed" for v in volumes.values()) else (
        "warning" if any(v["status"] == "warning" for v in volumes.values()) else "ok")
    return {"status": overall, "warning_threshold_gib": warn_gib,
            "failure_threshold_gib": fail_gib, "volumes": list(volumes.values())}


def default_locations(root: Path) -> list[tuple[str, Path]]:
    from brainglobe_atlasapi import config
    locations = [("builder_root", root),
                 ("brainglobe_install", Path(config.get_brainglobe_dir())),
                 ("temporary", Path(tempfile.gettempdir()))]
    tmp = os.environ.get("TMP")
    if tmp:
        locations.append(("TMP", Path(tmp)))
    return locations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--warn-gib", type=float, default=WARN_GIB)
    parser.add_argument("--fail-gib", type=float, default=FAIL_GIB)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    report = inspect_locations(default_locations(root), args.warn_gib, args.fail_gib)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "storage_preflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Storage preflight")
    print("=" * 72)
    for volume in report["volumes"]:
        marker = volume["status"].upper()
        roles = ", ".join(volume["roles"])
        free = f"{volume['free_gib']:.2f} GiB free" if volume["free_gib"] is not None else volume.get("error", "unknown")
        print(f"[{marker}] {volume['volume'] or volume['paths'][0]}: {free} ({roles})")
        for path in volume["paths"]:
            print(f"  - {path}")
    if report["status"] == "warning":
        print("WARNING [DISK_SPACE_LOW]: build may need more working space; continuing this test build.")
    elif report["status"] == "failed":
        print("ERROR [DISK_SPACE_CRITICAL]: less than the critical free-space reserve remains.")
    return 2 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
