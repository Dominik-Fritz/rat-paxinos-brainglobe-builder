#!/usr/bin/env python3
"""Fail-fast host checks for the Windows builder without touching atlas data."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MIN_DISK_GIB = 12
MIN_RAM_GIB = 6
LOCK_NAME = ".builder.lock"


def available_ram() -> int:
    """Return currently available physical memory, or zero when unavailable."""
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong),
                ("extended_available", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        return int(status.available) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page_size * available_pages)
    except (ValueError, OSError, AttributeError):
        return 0


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_lock(root: Path, build_id: str, owner_pid: int | None = None) -> tuple[bool, str]:
    lock = root / LOCK_NAME
    if lock.exists():
        try:
            previous = json.loads(lock.read_text(encoding="utf-8"))
            same_host = previous.get("host") == socket.gethostname()
            if same_host and not process_is_running(int(previous.get("pid", 0))):
                lock.unlink()
            else:
                return False, f"active lock: {previous}"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False, f"unreadable/stale lock must be reviewed: {lock}"
    payload = {"pid": owner_pid or os.getpid(), "host": socket.gethostname(), "build_id": build_id,
               "created_utc": datetime.now(timezone.utc).isoformat()}
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.write("\n")
        return True, str(lock)
    except FileExistsError:
        return False, f"lock appeared concurrently: {lock}"


def writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".builder-write-", dir=path)
        os.close(fd)
        Path(name).unlink()
        return True, "write test passed"
    except OSError as exc:
        return False, str(exc)


def run(root: Path, *, min_disk_gib: float = MIN_DISK_GIB,
        min_ram_gib: float = MIN_RAM_GIB, acquire: bool = True,
        build_id: str = "preflight", extra_paths: tuple[Path, ...] = (),
        owner_pid: int | None = None) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, code: str, detail: str, hint: str) -> None:
        checks.append({"name": name, "ok": ok, "code": None if ok else code,
                       "detail": detail, "hint": None if ok else hint})

    bits = struct.calcsize("P") * 8
    version = sys.version_info[:2]
    add("python", bits == 64 and version in {(3, 11), (3, 12)}, "PYTHON_UNSUPPORTED",
        f"Python {version[0]}.{version[1]}, {bits}-bit",
        "Install 64-bit Python 3.11/3.12 and pass --python with its full path.")

    checked_volumes: set[str] = set()
    for path in (root, *extra_paths):
        anchor = path.anchor or str(path)
        if anchor in checked_volumes:
            continue
        checked_volumes.add(anchor)
        try:
            free = shutil.disk_usage(path if path.exists() else path.parent).free
            add(f"disk:{path}", free >= min_disk_gib * 1024**3, "DISK_SPACE_LOW",
                f"{free / 1024**3:.1f} GiB free",
                f"Free at least {min_disk_gib:.0f} GiB on the volume containing {path}.")
        except OSError as exc:
            add(f"disk:{path}", False, "DISK_CHECK_FAILED", str(exc), "Check that the target volume is mounted and accessible.")

    ram = available_ram()
    add("ram", not ram or ram >= min_ram_gib * 1024**3, "RAM_LOW",
        f"{ram / 1024**3:.1f} GiB currently available" if ram else "availability unknown",
        f"Make at least {min_ram_gib:.0f} GiB RAM available or use a larger host.")

    for path in (root, *extra_paths):
        ok, detail = writable(path)
        add(f"write:{path}", ok, "PATH_NOT_WRITABLE", detail,
            "Choose a writable local folder and review UAC, antivirus, and directory permissions.")

    path_text = str(root)
    unsupported = len(path_text) > 180 or any(char in path_text for char in "\r\n!")
    add("path", not unsupported, "PATH_UNSUPPORTED", path_text,
        "Use a short local NTFS path without exclamation marks; avoid UNC, FAT32, OneDrive, and network folders.")

    if acquire:
        ok, detail = acquire_lock(root, build_id, owner_pid)
        add("lock", ok, "BUILDER_ALREADY_RUNNING", detail,
            "Stop the other build. Remove .builder.lock only after confirming its process is no longer running.")

    return {"status": "ok" if all(item["ok"] for item in checks) else "failed",
            "build_id": build_id, "checked_utc": datetime.now(timezone.utc).isoformat(), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--build-id", default="preflight")
    parser.add_argument("--owner-pid", type=int, default=None)
    parser.add_argument("--extra-path", action="append", default=[])
    parser.add_argument("--min-disk-gib", type=float, default=MIN_DISK_GIB)
    parser.add_argument("--min-ram-gib", type=float, default=MIN_RAM_GIB)
    parser.add_argument("--no-lock", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    result = run(root, min_disk_gib=args.min_disk_gib, min_ram_gib=args.min_ram_gib,
                 acquire=not args.no_lock, build_id=args.build_id,
                 extra_paths=tuple(Path(value).resolve() for value in args.extra_path),
                 owner_pid=args.owner_pid)
    report_dir = root / "reports" / "builds" / args.build_id
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "preflight.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for check in result["checks"]:
        marker = "OK" if check["ok"] else f"ERROR {check['code']}"
        print(f"[{marker}] {check['name']}: {check['detail']}")
        if check["hint"]:
            print(f"  {check['hint']}")
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
