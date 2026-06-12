# V32.10 SimpleITK Affine Waxholm Reference Test Atlases

Purpose: build separate diagnostic BrainGlobe test atlases using the V32.9 SimpleITK affine registration path.

This package does **not** modify the stable atlas:

```text
paxinos_watson_rat_40um
```

It creates separate test atlases:

```text
paxinos_watson_rat_40um_waxholm_affine_rank01_test
paxinos_watson_rat_40um_waxholm_affine_rank02_test
```

## Why this version exists

V32.8 showed that bbox-fit + translation is insufficient. V32.9 showed that SimpleITK affine registration gives a real improvement in mask fit, with Dice improving from roughly 0.713 after translation to roughly 0.829 after affine registration.

V32.10 therefore builds test atlases from that affine path so the result can be checked in ABBA.

## Important limitation

The V32.10 reference is still diagnostic. It is generated from the low-resolution SimpleITK affine proof-of-concept and upsampled to the Paxinos target grid.

It is not a final deformable registration.
It is not a stable atlas.
It is not a publication-ready anatomical reference.

Humanity briefly resisted the urge to call a diagnostic preview “stable.” Record the miracle.

## How to run

Main runner:

```bat
RUN_V32_10_BUILD_SIMPLEITK_AFFINE_WAXHOLM_REFERENCE_TEST_ATLASES.bat
```

If dependencies are missing:

```bat
RUN_V32_10_INSTALL_DEPS_AND_RUN.bat
```

The dependency runner installs/checks:

```text
SimpleITK
tifffile
scipy
nibabel
matplotlib
```

inside the project `.venv`.

## Outputs

Reports are written to:

```text
reports\v32_10_simpleitk_affine_waxholm_reference_test_atlases
```

Main files:

```text
v32_10_simpleitk_affine_waxholm_reference_test_atlases_summary.txt
v32_10_simpleitk_affine_waxholm_reference_test_atlases_report.json
v32_10_paxinos_watson_rat_40um_waxholm_affine_rank01_test_preview.png
v32_10_paxinos_watson_rat_40um_waxholm_affine_rank02_test_preview.png
```

Candidate atlas folders are written to:

```text
data\output\brainglobe_official_candidate\paxinos_watson_rat_40um_waxholm_affine_rank01_test
data\output\brainglobe_official_candidate\paxinos_watson_rat_40um_waxholm_affine_rank02_test
```

BrainGlobe cache folders are written to:

```text
%USERPROFILE%\.brainglobe\paxinos_watson_rat_40um_waxholm_affine_rank01_test_v1.0
%USERPROFILE%\.brainglobe\paxinos_watson_rat_40um_waxholm_affine_rank02_test_v1.0
```

Existing matching test-cache folders are backed up before replacement.

## ABBA test

Restart Fiji/ABBA completely, then open:

```text
paxinos_watson_rat_40um_waxholm_affine_rank01_test
paxinos_watson_rat_40um_waxholm_affine_rank02_test
```

Check:

```text
reference = SimpleITK affine Waxholm MRI background
borders   = Paxinos borders
```

Judge:

```text
- Does the reference appear as a real anatomical background?
- Are coronal/sagittal/horizontal buttons still correct?
- Do Paxinos borders sit plausibly on Waxholm anatomy?
- Do reference and borders visibility controls behave independently?
- Is Rank 1 or Rank 2 visually better?
```

## Decision rule

If the affine test atlas is useful in ABBA, the next step is a cleaner affine integration package with stronger metadata and QC.

If it is still anatomically off, move to full-resolution affine/deformable registration. Do not promote this as stable.
