# Rat Paxinos BrainGlobe Builder

Version: **V32.2 Oriented Reference Prep**

This package is based on the last stable V32 line and adds the orientation fix that was validated in ABBA.

## What is fixed

- ABBA button mapping is corrected by applying the validated display-space orientation:
  - old internal axis model: `[LR, AP, SI] / LPI`
  - new internal axis model: `[AP, SI, LR] / PIL`
  - permutation: `perm=(1,2,0)`
- ABBA `Coronal`, `Sagittal`, and `Horizontal` should now match the actual slice plane.
- Coronal slices should be upright.
- `hemispheres.tiff` is now masked to `annotation > 0`, instead of filling the full rectangular volume.
- `hemispheres.tiff` is kept under the dedicated `hemispheres_file` metadata key and is not advertised as a normal display/reference channel in `metadata["files"]`.
- The invalid old NeuroRat reference replacement is not included in the main pipeline.

## What is not fixed yet

The current `reference.tiff` is still a synthetic label-edge reference. It is useful for loading tests, but it is not a Nissl/MRI-like anatomical background. Do not pretend otherwise; that is how software becomes folklore.

Use:

```text
RUN_SIGMA_REFERENCE_EXPERIMENT.bat
```

for the separate, non-destructive SIGMA reference experiment.

## Main run

Copy this project folder to:

```text
G:\rat-paxinos-brainglobe-builder
```

Keep raw data in:

```text
data\raw\bluebrainheadmodels
```

Then run:

```text
run_builder.bat
```

After completion, restart Fiji/ABBA completely and open:

```text
paxinos_watson_rat_40um
```

Expected ABBA behavior:

```text
Coronal    = coronal and upright
Sagittal   = sagittal
Horizontal = horizontal
```

## Useful checks

```text
reports\v32_2_abba_orientation_report_official.txt
reports\v31_hemispheres_report_official.txt
reports\v25_final_status.txt
```

Optional direct viewer:

```text
RUN_VIEW_INSTALLED_IN_NAPARI.bat
```
