from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

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

# Maps expected local filename to Zenodo filename. Some files arrive without .gz,
# some are inside archives, because naturally there had to be five formats.
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

INSIDE_ZIP_MAP = {
    "NeuroRatLabels.nii.gz": ["NeuroRatLabels.nii.gz"],
    "NeuroRat_MRI.nii.gz": ["NeuroRat_MRI.nii.gz"],
    "Waxholm_Atlas_Labels.nii.gz": ["Waxholm_Atlas_Labels.nii.gz"],
    "Waxholm_Atlas_Mask.nii.gz": ["Waxholm_Atlas_Mask.nii.gz"],
    "Waxholm_Atlas_MRI.nii.gz": ["Waxholm_Atlas_MRI.nii.gz"],
}


def console_print(msg: str) -> None:
    if Console:
        Console().print(msg)
    else:
        print(msg)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_raw_dir() -> Path:
    RAW_BLUEBRAIN_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_BLUEBRAIN_DIR


def candidate_paths(filename: str) -> list[Path]:
    raw = normalize_raw_dir()
    base = Path(filename)
    names = {filename, base.name}
    if filename.endswith(".txt"):
        names.add(filename[:-4])
    if filename.endswith(".nii.gz"):
        names.add(filename[:-3])  # .nii
    return [raw / n for n in names]


def exists_any(filename: str) -> bool:
    return any(p.exists() and p.stat().st_size > 0 for p in candidate_paths(filename))


def ensure_gz(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if src.suffix == ".gz":
        shutil.copy2(src, dst)
    else:
        with src.open("rb") as f_in, gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def normalize_existing_files() -> list[dict[str, str]]:
    raw = normalize_raw_dir()
    actions = []

    # Add .txt aliases for extensionless label files.
    for stem in ["Paxinos_Watson_Labels", "Paxinos_Watson_Labels_Cortex", "SIGMA_Anatomical_Brain_Atlas_Labels", "Waxholm_Atlas_Labels"]:
        src = raw / stem
        dst = raw / f"{stem}.txt"
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            actions.append({"action": "copied_txt_alias", "from": str(src), "to": str(dst)})

    # Add .nii.gz aliases for known NIfTI files stored as .nii.
    for stem in ["Paxinos_Watson_Atlas", "Neurorat", "waxholm_aligned_to_neurorat", "Waxholm_Atlas"]:
        src = raw / f"{stem}.nii"
        dst = raw / f"{stem}.nii.gz"
        if src.exists() and not dst.exists():
            ensure_gz(src, dst)
            actions.append({"action": "gzipped_alias", "from": str(src), "to": str(dst)})

    return actions


def status() -> dict[str, Any]:
    normalize_actions = normalize_existing_files()
    required = {name: exists_any(name) for name in REQUIRED_FINAL_FILES}
    optional = {name: exists_any(name) for name in OPTIONAL_FINAL_FILES}
    return {
        "raw_dir": str(normalize_raw_dir()),
        "required": required,
        "optional": optional,
        "missing_required": [k for k, v in required.items() if not v],
        "present_required_count": sum(1 for v in required.values() if v),
        "required_count": len(required),
        "normalize_actions": normalize_actions,
        "complete": all(required.values()),
    }


def fetch_zenodo_file_index() -> dict[str, dict[str, Any]]:
    if requests is None:
        raise RuntimeError("requests is not installed")
    r = requests.get(ZENODO_API, timeout=60)
    r.raise_for_status()
    rec = r.json()
    files = rec.get("files", [])
    index = {}
    for f in files:
        key = f.get("key") or f.get("filename")
        links = f.get("links", {})
        url = links.get("self") or links.get("download")
        if key and url:
            index[key] = {"url": url, "size": f.get("size"), "checksum": f.get("checksum")}
    return index


def download_file(url: str, dst: Path, retries: int = 3) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests is not installed")

    tmp = dst.with_suffix(dst.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", "0") or 0)
                done = 0
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
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
    return {"downloaded": False, "url": url, "path": str(dst), "error": "unknown"}


def try_extract_align_zip() -> list[dict[str, Any]]:
    raw = normalize_raw_dir()
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
            if name in INSIDE_ZIP_MAP:
                dst = raw / name
                if not dst.exists():
                    with z.open(info, "r") as src, dst.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    actions.append({"action": "extracted_from_align_zip", "file": name, "to": str(dst)})

    # Create useful aliases after extraction.
    label_nii = raw / "Waxholm_Atlas_Labels.nii.gz"
    label_txt = raw / "Waxholm_Atlas_Labels.txt"
    if label_nii.exists() and not label_txt.exists():
        # Do NOT pretend this is a text labels file. Leave it as NIfTI only.
        actions.append({"action": "not_aliasing_nifti_to_txt", "file": str(label_nii)})

    return actions


def auto_download(include_large: bool = False) -> dict[str, Any]:
    raw = normalize_raw_dir()
    before = status()
    report = {
        "generated_at": now(),
        "dataset_doi": DATASET_DOI,
        "zenodo_record_id": ZENODO_RECORD_ID,
        "raw_dir": str(raw),
        "before": before,
        "file_index_count": None,
        "downloads": [],
        "extract_actions": [],
        "after": None,
        "note": "Downloads only files needed for the builder unless include_large is requested.",
    }

    if before["complete"]:
        report["after"] = before
        return report

    index = fetch_zenodo_file_index()
    report["file_index_count"] = len(index)

    wanted = list(REQUIRED_FINAL_FILES)
    # Always include the align zip if several Waxholm/NeuroRat pieces are missing.
    if not exists_any("align_waxholm_to_neurorat.zip"):
        wanted.append("align_waxholm_to_neurorat.zip")
    if include_large:
        wanted += OPTIONAL_FINAL_FILES

    seen = set()
    for local_name in wanted:
        if local_name in seen:
            continue
        seen.add(local_name)
        if exists_any(local_name):
            continue

        candidates = REMOTE_CANDIDATES.get(local_name, [local_name])
        remote_name = next((c for c in candidates if c in index), None)
        if remote_name is None:
            report["downloads"].append({"requested": local_name, "downloaded": False, "error": "not_found_in_zenodo_index", "candidates": candidates})
            continue

        dst = raw / remote_name
        result = download_file(index[remote_name]["url"], dst)
        result["requested"] = local_name
        result["remote_name"] = remote_name
        report["downloads"].append(result)

    report["extract_actions"] = try_extract_align_zip()
    normalize_existing_files()
    report["after"] = status()
    return report


def write_report(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "v18_data_download_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    after = report.get("after", {})
    lines = [
        "V18 data availability / auto-download report",
        "=" * 72,
        f"Generated: {report.get('generated_at')}",
        f"Dataset DOI: {report.get('dataset_doi')}",
        f"Zenodo record: {report.get('zenodo_record_id')}",
        f"Raw dir: {report.get('raw_dir')}",
        "",
        f"Complete after: {after.get('complete')}",
        f"Required present: {after.get('present_required_count')}/{after.get('required_count')}",
        "",
        "Missing required after:",
    ]
    for x in after.get("missing_required", []):
        lines.append(f"- {x}")
    if not after.get("missing_required"):
        lines.append("- none")
    lines.append("")
    lines.append("Downloads:")
    for d in report.get("downloads", []):
        lines.append(json.dumps(d, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("Extraction/actions:")
    for a in report.get("extract_actions", []):
        lines.append(json.dumps(a, indent=2, ensure_ascii=False))
    (REPORTS_DIR / "v18_data_download_report.txt").write_text("\n".join(lines), encoding="utf-8")


def print_status_table(st: dict[str, Any]) -> None:
    if Console and Table:
        console = Console()
        table = Table(title="V18 data status")
        table.add_column("Check")
        table.add_column("Value")
        table.add_row("Raw dir", st["raw_dir"])
        table.add_row("Complete", str(st["complete"]))
        table.add_row("Required present", f"{st['present_required_count']}/{st['required_count']}")
        table.add_row("Missing required", str(len(st["missing_required"])))
        console.print(table)
        if st["missing_required"]:
            console.print("[yellow]Missing:[/yellow] " + ", ".join(st["missing_required"]))
    else:
        print(json.dumps(st, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--auto-download", action="store_true")
    parser.add_argument("--include-large", action="store_true", help="Also attempt optional large files. Not recommended by default.")
    args = parser.parse_args()

    if args.check_only or not args.auto_download:
        st = status()
        write_report({"generated_at": now(), "dataset_doi": DATASET_DOI, "zenodo_record_id": ZENODO_RECORD_ID, "raw_dir": st["raw_dir"], "before": st, "downloads": [], "extract_actions": [], "after": st})
        print_status_table(st)
        return 0 if st["complete"] else 2

    report = auto_download(include_large=args.include_large)
    write_report(report)
    print_status_table(report["after"])
    return 0 if report["after"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
