# Rat Paxinos BrainGlobe Builder

Tools for building a BrainGlobe-compatible rat atlas package from the BlueBrainHeadModels Paxinos-Watson atlas resources.

This repository does **not** distribute the original atlas data. It only provides setup scripts, validation scripts, and conversion scaffolding.

## Source dataset

This project is designed for the following source dataset:

- **Dataset:** BlueBrainHeadModels
- **Version:** v1
- **Published:** April 4, 2024
- **Version DOI:** `10.5281/zenodo.10926947`
- **Associated repository:** `BlueBrain/BlueBrainHeadModels`

Use this exact dataset version when reproducing results. Later source dataset versions may change file names, geometry, labels, or licensing. Because apparently data formats enjoy mutating when nobody is watching.

## What this project aims to do

The long-term goal is to package the Paxinos-Watson rat brain atlas resources from BlueBrainHeadModels into a BrainGlobe-compatible atlas that can be used by tools such as ABBA/Fiji.

The intended workflow is:

1. Validate local source files.
2. Inspect NIfTI geometry, orientation, voxel sizes, and label IDs.
3. Parse Paxinos-Watson label tables.
4. Generate a BrainGlobe-compatible structure tree.
5. Select and validate a reference volume.
6. Build a local BrainGlobe atlas package.
7. Test the atlas in ABBA.

The current scaffold performs steps 1-3 and prepares the project for later conversion.

## Repository layout

```text
rat-paxinos-brainglobe-builder/
├── run_builder.bat
├── requirements.txt
├── README.md
├── .gitignore
├── src/
│   ├── inspect_inputs.py
│   ├── parse_labels.py
│   ├── build_structures_json.py
│   ├── build_brainglobe_atlas.py
│   └── utils_paths.py
├── data/
│   ├── raw/
│   │   └── bluebrainheadmodels/
│   ├── processed/
│   └── output/
└── reports/
```

## Data placement

Download the BlueBrainHeadModels v1 dataset manually from Zenodo and place the files in:

```text
data/raw/bluebrainheadmodels/
```

The expected source files include:

```text
Paxinos_Watson_Atlas.nii.gz
Paxinos_Watson_Labels.txt
Paxinos_Watson_Labels_Cortex.txt
SIGMA_Anatomical_Brain_Atlas.nii
SIGMA_Anatomical_Brain_Atlas_Labels.txt
Neurorat.nii.gz
transform_waxholm_to_neurorat.h5
waxholm_aligned_to_neurorat.nii.gz
Waxholm_Atlas.nii.gz
Waxholm_Atlas_Labels.txt
```

Optional but useful files include:

```text
NeuroRat_MRI.nii.gz
NeuroRatLabels.nii.gz
Waxholm_Atlas_MRI.nii.gz
Waxholm_Atlas_Labels.nii.gz
Waxholm_Atlas_Mask.nii.gz
```

Large atlas data files are intentionally ignored by Git.

## One-click setup and inspection

Run:

```text
run_builder.bat
```

The BAT file will:

1. Check whether Python is installed.
2. If Python is missing, ask before installing Python 3.11 via `winget`.
3. Create a local `.venv`.
4. Install `requirements.txt`.
5. Run dataset inspection.
6. Write reports to `reports/`.

Generated reports:

```text
reports/input_inspection_report.txt
reports/input_inspection_report.json
```

## Licensing and data redistribution

This repository is intended to contain only original code and documentation for conversion/packaging.

The original atlas data are not redistributed here and remain subject to their respective licenses and terms of use. Users must obtain the required source files directly from the original dataset provider.

Attribution should include:

- BlueBrainHeadModels dataset
- Paxinos & Watson rat brain atlas source material
- SIGMA atlas, where applicable
- Waxholm Rat Brain Atlas, where applicable
- NeuroRat model, where applicable
- BrainGlobe, if a BrainGlobe atlas is generated

## Current status

Scaffold stage.

Implemented:

- Repository structure.
- Single `run_builder.bat`.
- Python virtual environment setup.
- Requirements installation.
- NIfTI metadata inspection.
- Paxinos label parsing.
- Draft flat `structures.json` generator.

Not finalized yet:

- Reference volume selection.
- BrainGlobe atlas generation.
- Mesh generation.
- Anatomical hierarchy.
- ABBA validation.

Do not treat the output as a validated atlas yet. That would be premature, and premature atlas confidence is how reviewers acquire new weapons.
