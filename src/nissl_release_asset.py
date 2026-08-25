#!/usr/bin/env python3
"""Resolve the immutable Nissl registration package used by the atlas build.

Large registration products are distributed as a versioned GitHub release
asset rather than committed to the source tree. Resolution is deterministic:
an explicitly configured package wins, followed by an embedded test package,
a verified cached release asset, and finally the published asset URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
import time
import socket
import ssl
import stat
import tifffile

MAX_ARCHIVE_BYTES = 8 * 1024**3
MAX_EXTRACTED_BYTES = 20 * 1024**3
MAX_FILES = 10000
ALLOWED_SUFFIXES = {".tif", ".tiff", ".abba", ".json", ".txt", ".md", ".csv"}


MANIFEST_RELATIVE = Path("resources") / "optional_ch03" / "nissl_release_asset.json"
REPORT_RELATIVE = Path("reports") / "nissl_release_asset"


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root: Path) -> dict:
    path = root / MANIFEST_RELATIVE
    if not path.is_file():
        raise FileNotFoundError(f"Nissl release manifest is missing: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key in ("release", "asset_name", "expected_registered_stack"):
        if not str(manifest.get(key, "")).strip():
            raise ValueError(f"Nissl release manifest has no {key!r}: {path}")
    return manifest


def validate_package(package: Path, manifest: dict) -> tuple[bool, str]:
    """Open the TIFF and reconcile the immutable release/runtime manifests."""
    if not package.is_dir(): return False, "not a directory"
    runtime_path = package / str(manifest.get("expected_registration_manifest", "registration_manifest.json"))
    if not runtime_path.is_file(): return False, "registration_manifest.json not found"
    try: runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: return False, f"invalid registration manifest: {exc}"
    for key in ("release", "stack_order", "target_sequence_offset", "anterior_edge_policy", "expected_stack_shape"):
        if key in manifest and runtime.get(key) != manifest[key]: return False, f"manifest mismatch for {key}: release={manifest[key]!r}, package={runtime.get(key)!r}"
    stack_name = str(runtime.get("stack_file", ""))
    if stack_name != manifest["expected_registered_stack"]:
        return False, f"manifest mismatch for stack_file: release={manifest['expected_registered_stack']!r}, package={stack_name!r}"
    state_name = runtime.get("state_file")
    stack, state = package / stack_name, package / str(state_name or "")
    if not stack.is_file(): return False, f"exact registered stack missing: {stack_name}"
    if not state_name or not state.is_file() or state.suffix.lower() != ".abba": return False, f"exact ABBA state missing: {state_name!r}"
    try:
        with tifffile.TiffFile(stack) as tif:
            series=tif.series[0]; shape=list(map(int,series.shape)); axes=series.axes; dtype=series.dtype
            if len(shape)!=3: return False, f"TIFF must be 3-D, got {shape}"
            expected=list(manifest.get("expected_stack_shape",runtime.get("expected_stack_shape",[])))
            if expected and shape != expected: return False, f"TIFF shape mismatch: expected {expected}, got {shape}"
            if not axes or len(axes)!=3: return False, f"TIFF axes invalid: {axes!r}"
            if dtype.kind not in "uif": return False, f"TIFF dtype unsupported: {dtype}"
            # Decode every page independently so late corruption cannot hide.
            for page in tif.pages:
                page.asarray()
    except Exception as exc: return False, f"TIFF cannot be opened: {exc}"
    return True, f"validated TIFF shape={shape}, axes={axes}, dtype={dtype}; exact ABBA state and manifests agree"


def safe_extract(zip_path: Path, destination: Path) -> None:
    if zip_path.stat().st_size > MAX_ARCHIVE_BYTES: raise ValueError("NISSL_ZIP_TOO_LARGE: compressed archive exceeds limit")
    destination.mkdir(parents=True, exist_ok=True); root=destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members=archive.infolist()
        if len(members)>MAX_FILES: raise ValueError("NISSL_ZIP_FILE_LIMIT: too many archive entries")
        if sum(m.file_size for m in members)>MAX_EXTRACTED_BYTES: raise ValueError("NISSL_ZIP_EXPANSION_LIMIT: extracted content exceeds limit")
        for member in members:
            target=(destination/member.filename).resolve()
            if target != root and root not in target.parents: raise ValueError(f"NISSL_ZIP_UNSAFE_PATH: {member.filename}")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode): raise ValueError(f"NISSL_ZIP_SYMLINK: {member.filename}")
            if not member.is_dir() and target.suffix.lower() not in ALLOWED_SUFFIXES: raise ValueError(f"NISSL_ZIP_FILE_TYPE: {member.filename}")
        archive.extractall(destination)


def download(url: str, destination: Path, retries: int = 3, backoff: float = 1.0) -> tuple[int, str]:
    """Download with bounded retry/backoff and HTTP Range resume."""
    destination.parent.mkdir(parents=True, exist_ok=True); partial=destination.with_suffix(destination.suffix+".partial")
    last=None
    for attempt in range(retries):
        offset=partial.stat().st_size if partial.exists() else 0
        headers={"User-Agent":"rat-paxinos-builder/0.3.1"}
        if offset: headers["Range"]=f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=120) as response:
                resumed=offset and getattr(response,"status",None)==206
                if offset and not resumed: partial.unlink(missing_ok=True); offset=0
                total=int(response.headers.get("Content-Length","0") or 0)+offset
                with partial.open("ab" if resumed else "wb") as output:
                    copied=offset
                    while chunk:=response.read(8*1024*1024): output.write(chunk); copied+=len(chunk)
                if total and copied != total: raise IOError(f"incomplete download: expected {total}, got {copied}")
            partial.replace(destination); digest=sha256_file(destination)
            print(f"Download complete: {destination.stat().st_size/1024**2:.1f} MiB; SHA-256 {digest}")
            return destination.stat().st_size,digest
        except urllib.error.HTTPError as exc:
            code=f"NISSL_HTTP_{exc.code}"; last=RuntimeError(f"{code}: {'private repository or authorization required' if exc.code in (401,403) else 'release asset not found or draft release' if exc.code==404 else exc.reason}")
            if exc.code in (401,403,404): break
        except (TimeoutError, socket.timeout) as exc: last=RuntimeError(f"NISSL_TIMEOUT: {exc}")
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                last = RuntimeError(f"NISSL_TLS_ERROR: certificate/TLS failure: {reason}")
            elif "proxy" in str(reason).lower():
                last = RuntimeError(f"NISSL_PROXY_ERROR: {reason}")
            else:
                last = RuntimeError(f"NISSL_NETWORK: {reason}")
        except OSError as exc:
            code="DISK_FULL" if getattr(exc,"errno",None)==28 else "NISSL_IO_ERROR"; last=RuntimeError(f"{code}: {exc}")
        if attempt+1<retries: time.sleep(backoff*(2**attempt))
    raise last or RuntimeError("NISSL_DOWNLOAD_FAILED")


def write_report(root: Path, report: dict) -> None:
    folder = root / REPORT_RELATIVE
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nissl_release_asset_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        "Nissl release asset resolution",
        "=" * 72,
        f"Status: {report['status']}",
        f"Release: {report.get('release')}",
        f"Source: {report.get('source')}",
        f"Package: {report.get('package')}",
        f"Asset: {report.get('asset')}",
        f"SHA-256: {report.get('sha256_actual')}",
        f"Message: {report.get('message')}",
    ]
    summary = "\n".join(lines) + "\n"
    (folder / "nissl_release_asset_summary.txt").write_text(summary, encoding="utf-8")
    build_id = os.environ.get("PAXINOS_BUILD_ID")
    if build_id:
        isolated = root / "reports" / "builds" / build_id
        isolated.mkdir(parents=True, exist_ok=True)
        (isolated / "nissl_release_asset_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (isolated / "nissl_release_asset_summary.txt").write_text(summary, encoding="utf-8")


def resolve(root: Path) -> Path:
    manifest = load_manifest(root)
    report = {"status": "failed", "release": manifest["release"]}
    embedded = root / "resources" / "optional_ch03" / "nissl_registration_0_3_0"
    cache_root = root / "data" / "release_assets" / manifest["release"]
    extracted = cache_root / "registration_package"

    candidates: list[tuple[str, Path]] = [("embedded 0.3.0 package", embedded)]
    for source, candidate in candidates:
        valid, message = validate_package(candidate, manifest)
        if valid:
            report.update(status="ok", source=source, package=str(candidate.resolve()), message=message)
            write_report(root, report)
            return candidate.resolve()

    url = str(manifest.get("download_url", "")).strip()
    expected_sha = str(manifest.get("sha256", "")).strip()
    asset = cache_root / manifest["asset_name"]
    if not url:
        valid, message = validate_package(extracted, manifest)
        if valid:
            report.update(
                status="ok",
                source="previously verified release cache",
                package=str(extracted.resolve()),
                asset=str(asset),
                message=message,
            )
            write_report(root, report)
            return extracted.resolve()
        report.update(
            source="release manifest",
            package=None,
            asset=str(asset),
            message=(
                "The required resources/optional_ch03/nissl_registration_0_3_0 package is incomplete "
                "and the release manifest has no download URL."
            ),
        )
        write_report(root, report)
        raise FileNotFoundError(report["message"])

    if not asset.is_file(): download(url, asset)
    actual_sha = sha256_file(asset)
    if not expected_sha:
        raise ValueError("A downloadable Nissl asset must have a pinned SHA-256 checksum.")
    if actual_sha.lower() != expected_sha.lower():
        print(f"NISSL_HASH_MISMATCH: deleting corrupt cache ({actual_sha}) and downloading again")
        asset.unlink(missing_ok=True); download(url, asset); actual_sha=sha256_file(asset)
        if actual_sha.lower()!=expected_sha.lower():
            asset.unlink(missing_ok=True); raise ValueError(f"NISSL_HASH_MISMATCH: expected {expected_sha}, got {actual_sha}")
    # Re-extract after verification. A corrupt archive/package cache is deleted
    # and downloaded once more in the same run.
    message = "not validated"
    for extraction_attempt in range(2):
        if extracted.exists():
            shutil.rmtree(extracted)
        try:
            safe_extract(asset, extracted)
            valid, message = validate_package(extracted, manifest)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            valid, message = False, str(exc)
        if valid:
            break
        shutil.rmtree(extracted, ignore_errors=True)
        asset.unlink(missing_ok=True)
        if extraction_attempt == 0:
            print(f"NISSL_CACHE_CORRUPT: {message}; downloading a clean asset")
            download(url, asset)
            actual_sha = sha256_file(asset)
            if actual_sha.lower() != expected_sha.lower():
                asset.unlink(missing_ok=True)
                raise ValueError(f"NISSL_HASH_MISMATCH: expected {expected_sha}, got {actual_sha}")
    else:
        raise ValueError(f"NISSL_PACKAGE_INVALID: {message}")
    report.update(
        status="ok",
        source=url,
        package=str(extracted.resolve()),
        asset=str(asset),
        sha256_actual=actual_sha,
        message=message,
    )
    write_report(root, report)
    return extracted.resolve()


def create_asset(root: Path, source: Path, output: Path) -> int:
    manifest = load_manifest(root)
    valid, message = validate_package(source, manifest)
    if not valid:
        raise ValueError(f"Cannot package Nissl registration source: {message}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    digest = sha256_file(output)
    print(f"Asset: {output.resolve()}")
    print(f"SHA-256: {digest}")
    print("Upload this immutable ZIP to the GitHub prerelease and copy its URL and SHA-256 into the manifest.")
    return 0


def pin_asset(root: Path, asset: Path, url: str) -> int:
    if not asset.is_file():
        raise FileNotFoundError(f"Nissl release asset is missing: {asset}")
    if not url.lower().startswith("https://"):
        raise ValueError("The published Nissl release asset URL must use HTTPS.")
    manifest_path = root / MANIFEST_RELATIVE
    manifest = load_manifest(root)
    manifest["asset_name"] = asset.name
    manifest["download_url"] = url
    manifest["sha256"] = sha256_file(asset)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Pinned manifest: {manifest_path}")
    print(f"Asset: {asset.name}")
    print(f"SHA-256: {manifest['sha256']}")
    print(f"URL: {url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the versioned Nissl registration release asset")
    sub = parser.add_subparsers(dest="command", required=True)
    resolver = sub.add_parser("resolve")
    resolver.add_argument("--root", required=True)
    resolver.add_argument("--path-file", default=None)
    creator = sub.add_parser("create")
    creator.add_argument("--root", required=True)
    creator.add_argument("--source", required=True)
    creator.add_argument("--output", required=True)
    pinner = sub.add_parser("pin")
    pinner.add_argument("--root", required=True)
    pinner.add_argument("--asset", required=True)
    pinner.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "resolve":
        try:
            package = resolve(root)
            if args.path_file:
                path_file = Path(args.path_file)
                path_file.parent.mkdir(parents=True, exist_ok=True)
                path_file.write_text(str(package) + "\n", encoding="utf-8")
            print(package)
            return 0
        except Exception as exc:
            write_report(
                root,
                {
                    "status": "failed",
                    "release": "unknown",
                    "source": None,
                    "package": None,
                    "asset": None,
                    "sha256_actual": None,
                    "message": str(exc),
                },
            )
            print(f"Nissl release asset error: {exc}", file=sys.stderr)
            return 2
    if args.command == "create":
        return create_asset(root, Path(args.source).resolve(), Path(args.output).resolve())
    return pin_asset(root, Path(args.asset).resolve(), args.url)


if __name__ == "__main__":
    raise SystemExit(main())
