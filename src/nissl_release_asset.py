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
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.request
import zipfile


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
    if not package.is_dir():
        return False, "not a directory"
    stack_name = manifest["expected_registered_stack"].lower()
    stacks = [p for p in package.rglob("*") if p.is_file() and p.name.lower() == stack_name]
    states = [p for p in package.rglob("*.abba") if p.is_file()]
    runtime_manifest = package / "registration_manifest.json"
    if len(stacks) != 1:
        return False, f"expected one {manifest['expected_registered_stack']}, found {len(stacks)}"
    if not states:
        return False, "no ABBA state file found"
    if not runtime_manifest.is_file():
        return False, "registration_manifest.json not found"
    return True, "runtime manifest, registration stack, and ABBA state found"


def safe_extract(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Unsafe path in Nissl release archive: {member.filename}")
        archive.extractall(destination)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "rat-paxinos-builder/0.3.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            copied = 0
            next_report = 64 * 1024 * 1024
            print(f"Downloading Nissl release asset: {url}")
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                copied += len(chunk)
                if copied >= next_report:
                    if total:
                        print(f"  {copied / 1024**2:.0f} / {total / 1024**2:.0f} MiB")
                    else:
                        print(f"  {copied / 1024**2:.0f} MiB")
                    next_report += 64 * 1024 * 1024
            print(f"Download complete: {copied / 1024**2:.1f} MiB")
        partial.replace(destination)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        partial.unlink(missing_ok=True)
        raise


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
    (folder / "nissl_release_asset_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    if not asset.is_file():
        download(url, asset)
    actual_sha = sha256_file(asset)
    if not expected_sha:
        raise ValueError("A downloadable Nissl asset must have a pinned SHA-256 checksum.")
    if actual_sha.lower() != expected_sha.lower():
        asset.unlink(missing_ok=True)
        raise ValueError(f"Nissl release asset SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")
    # Re-extract after every checksum verification so a modified extracted
    # cache can never silently replace the immutable release content.
    if extracted.exists():
        shutil.rmtree(extracted)
    safe_extract(asset, extracted)
    valid, message = validate_package(extracted, manifest)
    if not valid:
        raise ValueError(f"Extracted Nissl release package is invalid: {message}")
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
            archive.write(path, path.relative_to(source))
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
