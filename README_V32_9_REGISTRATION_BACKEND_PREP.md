# V32.9 Proper Registration Backend Prep

Purpose: prepare the next real registration step after V32.8 showed that bbox-fit + translation is not enough.

This package is diagnostic/pre-integration only.

It does **not** modify the stable Paxinos atlas.
It does **not** promote Waxholm as final reference.
It does **not** install a new BrainGlobe atlas.

## What V32.9 does

1. Reuses the two viable Waxholm candidates from V32.7b/V32.8:
   - rank01: `perm=[2,1,0]`, `flips=[True, True, False]`, shift lowres `[-3,-4,2]`
   - rank02: `perm=[2,1,0]`, `flips=[True, True, True]`, shift lowres `[-3,-4,-2]`
2. Rebuilds the low-resolution initial masks and MRI previews.
3. Checks local registration backends:
   - SimpleITK
   - ANTsPy/ants
   - scipy
   - nibabel
4. If SimpleITK is available, performs a small low-resolution proof-of-concept registration:
   - rigid registration from rank01/rank02 initial state
   - affine registration initialized from the rigid result
   - exports preview panels
5. If SimpleITK is not available, it writes a clear report and stops safely.

## How to run

Diagnostic only:

```bat
RUN_V32_9_REGISTRATION_BACKEND_PREP.bat
```

Optional SimpleITK installation into the project `.venv`, then run:

```bat
RUN_V32_9_INSTALL_SIMPLEITK_AND_RUN.bat
```

The installer runner changes only the Python environment by installing SimpleITK with pip. It does not change atlas data.

## Expected reports

Reports are written to:

```text
reports\v32_9_registration_backend_prep
```

Main files:

```text
v32_9_registration_backend_prep_summary.txt
v32_9_registration_backend_prep_report.json
v32_9_backend_status.json
v32_9_rank01_initial_preview.png
v32_9_rank02_initial_preview.png
v32_9_rank01_simpleitk_registration_preview.png   # only if SimpleITK works
v32_9_rank02_simpleitk_registration_preview.png   # only if SimpleITK works
```

## Decision rule

If SimpleITK registration improves anatomy clearly, the next version can build a separate affine-refined Waxholm test atlas.

If it fails or does not improve the fit, the next step should be proper external registration, for example ANTs/Elastix/BigWarp, not more bbox shifting.
