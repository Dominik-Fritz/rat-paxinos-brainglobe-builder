# Rat Paxinos BrainGlobe Builder - V38 Stable

Use one main file:

```text
RUN_BUILDER_V38_STABLE.bat
```

## What V38 fixes

- V33 NeuroRat reference path bug
- V33 JSON report bug with NumPy bool/scalars
- ABBA Python environment check via V36
- Keeps:
  - hemispheres.tiff fix
  - additional_references = []
  - root 997 compatibility
  - ABBA visibility patch workflow
  - V34/V35 orientation tools

## Required raw files

They must already exist here:

```text
G:\rat-paxinos-brainglobe-builder\data\raw\bluebrainheadmodels
```

Required for the current reference workflow:

```text
NeuroRat_MRI.nii.gz
Paxinos_Watson_Atlas.nii.gz
Paxinos_Watson_Labels.txt
Paxinos_Watson_Labels_Cortex.txt
```

## Run

Copy the ZIP contents into:

```text
G:\rat-paxinos-brainglobe-builder
```

Overwrite existing files.

Then run:

```text
RUN_BUILDER_V38_STABLE.bat
```

After completion:

1. Restart ABBA/Fiji completely.
2. Open `paxinos_watson_rat_40um`.
3. If the display is still wrong, run `RUN_V34_ORIENTATION_PREVIEWS.bat`.

## Important limitation

The NeuroRat MRI reference is resampled onto Paxinos geometry. This is a practical display/reference fix, not a validated nonlinear registration.
