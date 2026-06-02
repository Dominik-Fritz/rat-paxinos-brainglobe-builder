from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "bluebrainheadmodels"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
REPORTS_DIR = PROJECT_ROOT / "reports"


EXPECTED_FILES = {
    "paxinos_atlas": [
        "Paxinos_Watson_Atlas.nii.gz",
        "Paxinos_Watson_Atlas.nii",
    ],
    "paxinos_labels": [
        "Paxinos_Watson_Labels.txt",
    ],
    "paxinos_labels_cortex": [
        "Paxinos_Watson_Labels_Cortex.txt",
    ],
    "sigma_reference": [
        "SIGMA_Anatomical_Brain_Atlas.nii",
        "SIGMA_Anatomical_Brain_Atlas.nii.gz",
    ],
    "sigma_labels": [
        "SIGMA_Anatomical_Brain_Atlas_Labels.txt",
    ],
    "neurorat": [
        "Neurorat.nii.gz",
        "Neurorat.nii",
    ],
    "neurorat_mri": [
        "NeuroRat_MRI.nii.gz",
        "NeuroRat_MRI.nii",
    ],
    "neurorat_labels": [
        "NeuroRatLabels.nii.gz",
        "NeuroRatLabels.nii",
    ],
    "waxholm_atlas": [
        "Waxholm_Atlas.nii.gz",
        "Waxholm_Atlas.nii",
    ],
    "waxholm_labels_txt": [
        "Waxholm_Atlas_Labels.txt",
    ],
    "waxholm_labels_nii": [
        "Waxholm_Atlas_Labels.nii.gz",
        "Waxholm_Atlas_Labels.nii",
    ],
    "waxholm_mask": [
        "Waxholm_Atlas_Mask.nii.gz",
        "Waxholm_Atlas_Mask.nii",
    ],
    "waxholm_mri": [
        "Waxholm_Atlas_MRI.nii.gz",
        "Waxholm_Atlas_MRI.nii",
    ],
    "waxholm_aligned_to_neurorat": [
        "waxholm_aligned_to_neurorat.nii.gz",
        "waxholm_aligned_to_neurorat.nii",
    ],
    "transform_waxholm_to_neurorat": [
        "transform_waxholm_to_neurorat.h5",
    ],
}


def find_existing_file(candidates: list[str]) -> Path | None:
    for name in candidates:
        path = RAW_DIR / name
        if path.exists():
            return path
    return None


def discover_expected_files() -> dict[str, Path | None]:
    return {
        key: find_existing_file(candidates)
        for key, candidates in EXPECTED_FILES.items()
    }


def ensure_project_dirs() -> None:
    for path in (RAW_DIR, PROCESSED_DIR, OUTPUT_DIR, REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
