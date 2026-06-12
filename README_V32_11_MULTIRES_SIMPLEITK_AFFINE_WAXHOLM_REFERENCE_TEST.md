# V32.11 Multi-resolution SimpleITK Affine Waxholm Reference Test

This package is the next step after V32.10.

V32.10 proved that the SimpleITK affine path is useful, but the generated reference was based on low-resolution registration and then upsampled. V32.11 repeats the affine workflow at a higher default diagnostic resolution (`factor=2` instead of `factor=4`) and builds a separate diagnostic BrainGlobe test atlas.

It does **not** modify the stable atlas:

```text
paxinos_watson_rat_40um
```

## Default run

Use this first:

```bat
RUN_V32_11_BUILD_MULTIREG_AFFINE_RANK01.bat
```

This builds only Rank 01:

```text
paxinos_watson_rat_40um_waxholm_multires_affine_rank01_test
```

Rank 01 remains the default because Rank 01 and Rank 02 were nearly identical after V32.9/V32.10, and Rank 01 was the original best anatomical/mask candidate.

## Optional comparison run

Only run this if Rank 01 looks suspicious or if direct Rank 02 comparison is needed:

```bat
RUN_V32_11_BUILD_MULTIREG_AFFINE_RANK01_RANK02.bat
```

## Dependency helper

If Python reports missing packages:

```bat
RUN_V32_11_INSTALL_DEPS_AND_RUN.bat
```

## Heavy full-resolution experiment

This is optional and may be slow or memory-heavy:

```bat
RUN_V32_11_FULLRES_RANK01_EXPERIMENT.bat
```

The default factor=2 run is the sensible first step. Full-res is not the first thing to throw at Windows unless you enjoy watching RAM suffer.

## Outputs to upload

After the default run, upload:

```text
reports\v32_11_multires_simpleitk_affine_waxholm_reference_test\v32_11_multires_simpleitk_affine_waxholm_reference_test_summary.txt
reports\v32_11_multires_simpleitk_affine_waxholm_reference_test\v32_11_multires_simpleitk_affine_waxholm_reference_test_report.json
reports\v32_11_multires_simpleitk_affine_waxholm_reference_test\v32_11_paxinos_watson_rat_40um_waxholm_multires_affine_rank01_test_preview.png
```

Then restart Fiji/ABBA and open:

```text
paxinos_watson_rat_40um_waxholm_multires_affine_rank01_test
```

Check whether:

```text
reference = sharper Waxholm MRI background
borders   = Paxinos borders
```

## Interpretation

This is still diagnostic. It is not a final deformable registration. A stable promote is only possible after ABBA visual QC confirms that the reference is useful and the metadata documents the limitations clearly.
