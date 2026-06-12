from __future__ import annotations

import argparse
import gzip
import json
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except Exception:
    requests = None

try:
    from rich.console import Console
    from rich.table import Table
except Exception:
    Console = None
    Table = None

from utils_paths import RAW_BLUEBRAIN_DIR, REPORTS_DIR

DATASET_DOI = "10.5281/zenodo.10926947"
ZENODO_RECORD_ID = "10926947"
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_RECORD_ID}"
ZENODO_RECORD_URL = f"https://zenodo.org/records/{ZENODO_RECORD_ID}"

REQUIRED_FINAL_FILES = [
    "Paxinos_Watson_Atlas.nii.gz",
    "Paxinos_Watson_Labels.txt",
    "Paxinos_Watson_Labels_Cortex.txt",
    "SIGMA_Anatomical_Brain_Atlas.nii",
    "SIGMA_Anatomical_Brain_Atlas_Labels.txt",
    "Neurorat.nii.gz",
    "waxholm_aligned_to_neurorat.nii.gz",
    "Waxholm_Atlas.nii.gz",
    "Waxholm_Atlas_Labels.txt",
    "transform_waxholm_to_neurorat.h5",
]

OPTIONAL_FINAL_FILES = [
    "align_waxholm_to_neurorat.zip",
    "Waxholm_Atlas_MRI.nii.gz",
    "Waxholm_Atlas_Mask.nii.gz",
    "NeuroRat_MRI.nii.gz",
    "NeuroRatLabels.nii.gz",
]

REMOTE_CANDIDATES = {
    "align_waxholm_to_neurorat.zip": ["align_waxholm_to_neurorat.zip"],
    "Neurorat.nii.gz": ["Neurorat.nii.gz", "Neurorat.nii"],
    "Paxinos_Watson_Atlas.nii.gz": ["Paxinos_Watson_Atlas.nii.gz", "Paxinos_Watson_Atlas.nii"],
    "Paxinos_Watson_Labels.txt": ["Paxinos_Watson_Labels.txt", "Paxinos_Watson_Labels"],
    "Paxinos_Watson_Labels_Cortex.txt": ["Paxinos_Watson_Labels_Cortex.txt", "Paxinos_Watson_Labels_Cortex"],
    "SIGMA_Anatomical_Brain_Atlas.nii": ["SIGMA_Anatomical_Brain_Atlas.nii"],
    "SIGMA_Anatomical_Brain_Atlas_Labels.txt": ["SIGMA_Anatomical_Brain_Atlas_Labels.txt", "SIGMA_Anatomical_Brain_Atlas_Labels"],
    "transform_waxholm_to_neurorat.h5": ["transform_waxholm_to_neurorat.h5"],
    "waxholm_aligned_to_neurorat.nii.gz": ["waxholm_aligned_to_neurorat.nii.gz", "waxholm_aligned_to_neurorat.nii"],
    "Waxholm_Atlas.nii.gz": ["Waxholm_Atlas.nii.gz", "Waxholm_Atlas.nii"],
    "Waxholm_Atlas_Labels.txt": ["Waxholm_Atlas_Labels.txt", "Waxholm_Atlas_Labels"],
}

INSIDE_ZIP_NAMES = {
    "NeuroRatLabels.nii.gz",
    "NeuroRat_MRI.nii.gz",
    "Waxholm_Atlas_Labels.nii.gz",
    "Waxholm_Atlas_Mask.nii.gz",
    "Waxholm_Atlas_MRI.nii.gz",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def raw_dir() -> Path:
    RAW_BLUEBRAIN_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_BLUEBRAIN_DIR


def reports_dir() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def candidate_paths(filename: str) -> list[Path]:
    raw = raw_dir()
    names = {filename}
    if filename.endswith(".txt"):
        names.add(filename[:-4])
    if filename.endswith(".nii.gz"):
        names.add(filename[:-3])
    return [raw / n for n in names]


def exists_any(filename: str) -> bool:
    return any(p.exists() and p.stat().st_size > 0 for p in candidate_paths(filename))


def gzip_alias(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    with src.open("rb") as f_in, gzip.open(dst, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def normalize_existing_files() -> list[dict[str, str]]:
    raw = raw_dir()
    actions = []

    for stem in [
        "Paxinos_Watson_Labels",
        "Paxinos_Watson_Labels_Cortex",
        "SIGMA_Anatomical_Brain_Atlas_Labels",
        "Waxholm_Atlas_Labels",
    ]:
        src = raw / stem
        dst = raw / f"{stem}.txt"
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            actions.append({"action": "copied_txt_alias", "from": str(src), "to": str(dst)})

    for stem in [
        "Paxinos_Watson_Atlas",
        "Neurorat",
        "waxholm_aligned_to_neurorat",
        "Waxholm_Atlas",
    ]:
        src = raw / f"{stem}.nii"
        dst = raw / f"{stem}.nii.gz"
        if src.exists() and not dst.exists():
            gzip_alias(src, dst)
            actions.append({"action": "gzipped_alias", "from": str(src), "to": str(dst)})

    return actions


def status() -> dict[str, Any]:
    normalize_actions = normalize_existing_files()
    required = {name: exists_any(name) for name in REQUIRED_FINAL_FILES}
    optional = {name: exists_any(name) for name in OPTIONAL_FINAL_FILES}
    return {
        "raw_dir": str(raw_dir()),
        "required": required,
        "optional": optional,
        "missing_required": [k for k, v in required.items() if not v],
        "present_required_count": sum(1 for v in required.values() if v),
        "required_count": len(required),
        "normalize_actions": normalize_actions,
        "complete": all(required.values()),
        "manual_download_url": ZENODO_RECORD_URL,
    }


def write_report(report: dict[str, Any]) -> None:
    rd = reports_dir()
    (rd / "v19_data_download_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    # compatibility
    (rd / "v18_data_download_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    after = report.get("after") or {}
    before = report.get("before") or {}
    lines = [
        "V19 data availability / auto-download report",
        "=" * 72,
        f"Generated: {report.get('generated_at')}",
        f"Dataset DOI: {DATASET_DOI}",
        f"Zenodo record URL: {ZENODO_RECORD_URL}",
        f"Zenodo API URL: {ZENODO_API}",
        f"Raw dir: {report.get('raw_dir', raw_dir())}",
        "",
        f"Complete before: {before.get('complete')}",
        f"Complete after: {after.get('complete')}",
        f"Required present after: {after.get('present_required_count')}/{after.get('required_count')}",
        "",
        "Missing required after:",
    ]
    missing = after.get("missing_required") or before.get("missing_required") or []
    lines.extend([f"- {x}" for x in missing] or ["- none"])
    lines += [
        "",
        "Manual fallback:",
        f"- Download files from: {ZENODO_RECORD_URL}",
        "- Put files into: data/raw/bluebrainheadmodels",
        "- Keep original filenames where possible.",
        "",
        "Exception:",
        str(report.get("exception")),
        "",
        "Traceback:",
        str(report.get("traceback")),
        "",
        "Downloads:",
    ]
    for d in report.get("downloads", []):
        lines.append(json.dumps(d, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("Extraction/actions:")
    for a in report.get("extract_actions", []):
        lines.append(json.dumps(a, indent=2, ensure_ascii=False))

    text = "\n".join(lines)
    (rd / "v19_data_download_report.txt").write_text(text, encoding="utf-8")
    (rd / "v18_data_download_report.txt").write_text(text, encoding="utf-8")


def print_status_table(st: dict[str, Any]) -> None:
    if Console and Table:
        console = Console()
        table = Table(title="V19 data status")
        table.add_column("Check")
        table.add_column("Value")
        table.add_row("Raw dir", st["raw_dir"])
        table.add_row("Complete", str(st["complete"]))
        table.add_row("Required present", f"{st['present_required_count']}/{st['required_count']}")
        table.add_row("Missing required", str(len(st["missing_required"])))
        console.print(table)
        if st["missing_required"]:
            console.print("[yellow]Missing:[/yellow] " + ", ".join(st["missing_required"]))
            console.print("[cyan]Manual fallback:[/cyan] " + ZENODO_RECORD_URL)
    else:
        print(json.dumps(st, indent=2, ensure_ascii=False))


def fetch_zenodo_file_index() -> dict[str, dict[str, Any]]:
    if requests is None:
        raise RuntimeError("requests is not installed")
    r = requests.get(ZENODO_API, timeout=90)
    r.raise_for_status()
    rec = r.json()
    index = {}
    for f in rec.get("files", []):
        key = f.get("key") or f.get("filename")
        links = f.get("links", {})
        url = links.get("self") or links.get("download")
        if key and url:
            index[key] = {"url": url, "size": f.get("size"), "checksum": f.get("checksum")}
    return index


def download_file(url: str, dst: Path, retries: int = 3) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests is not installed")

    tmp = dst.with_name(dst.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=90) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", "0") or 0)
                done = 0
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done / total * 100
                            print(f"\rDownloading {dst.name}: {pct:5.1f}%", end="")
                print()
            tmp.replace(dst)
            return {"downloaded": True, "url": url, "path": str(dst), "size": dst.stat().st_size}
        except Exception as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            if attempt >= retries:
                return {"downloaded": False, "url": url, "path": str(dst), "error": repr(exc)}
            time.sleep(3)
    return {"downloaded": False, "url": url, "path": str(dst), "error": "unknown download failure"}


def try_extract_align_zip() -> list[dict[str, Any]]:
    raw = raw_dir()
    actions = []
    zpath = raw / "align_waxholm_to_neurorat.zip"
    if not zpath.exists():
        return actions

    import zipfile
    with zipfile.ZipFile(zpath, "r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name in INSIDE_ZIP_NAMES:
                dst = raw / name
                if not dst.exists():
                    with z.open(info, "r") as src, dst.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    actions.append({"action": "extracted_from_align_zip", "file": name, "to": str(dst)})
    return actions


def auto_download(include_large: bool = False) -> dict[str, Any]:
    before = status()
    report = {
        "generated_at": now(),
        "dataset_doi": DATASET_DOI,
        "zenodo_record_id": ZENODO_RECORD_ID,
        "zenodo_record_url": ZENODO_RECORD_URL,
        "zenodo_api": ZENODO_API,
        "raw_dir": str(raw_dir()),
        "before": before,
        "downloads": [],
        "extract_actions": [],
        "after": before,
        "exception": None,
        "traceback": None,
    }

    try:
        if before["complete"]:
            write_report(report)
            return report

        index = fetch_zenodo_file_index()
        report["file_index_count"] = len(index)

        wanted = list(REQUIRED_FINAL_FILES)
        if not exists_any("align_waxholm_to_neurorat.zip"):
            wanted.append("align_waxholm_to_neurorat.zip")
        if include_large:
            wanted.extend(OPTIONAL_FINAL_FILES)

        seen = set()
        for local_name in wanted:
            if local_name in seen or exists_any(local_name):
                continue
            seen.add(local_name)
            candidates = REMOTE_CANDIDATES.get(local_name, [local_name])
            remote_name = next((c for c in candidates if c in index), None)
            if remote_name is None:
                report["downloads"].append({
                    "requested": local_name,
                    "downloaded": False,
                    "error": "not_found_in_zenodo_index",
                    "candidates": candidates,
                })
                continue
            result = download_file(index[remote_name]["url"], raw_dir() / remote_name)
            result["requested"] = local_name
            result["remote_name"] = remote_name
            report["downloads"].append(result)

        report["extract_actions"] = try_extract_align_zip()
        normalize_existing_files()
        report["after"] = status()
        write_report(report)
        return report

    except Exception as exc:
        report["exception"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        report["after"] = status()
        write_report(report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--auto-download", action="store_true")
    parser.add_argument("--include-large", action="store_true")
    args = parser.parse_args()

    if args.check_only or not args.auto_download:
        st = status()
        write_report({
            "generated_at": now(),
            "dataset_doi": DATASET_DOI,
            "zenodo_record_id": ZENODO_RECORD_ID,
            "zenodo_record_url": ZENODO_RECORD_URL,
            "zenodo_api": ZENODO_API,
            "raw_dir": st["raw_dir"],
            "before": st,
            "downloads": [],
            "extract_actions": [],
            "after": st,
            "exception": None,
            "traceback": None,
        })
        print_status_table(st)
        return 0 if st["complete"] else 2

    report = auto_download(include_large=args.include_large)
    print_status_table(report["after"])
    return 0 if report["after"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
