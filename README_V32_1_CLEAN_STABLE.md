# V32.1 Clean Stable Restart

This version starts from the trusted V32 Stable Restart baseline and cleans the project around it.

## Stable baseline retained

- V29 root compatibility: root ID 997.
- V30 ABBA/Java structure enrichment.
- V31 `hemispheres.tiff` generation.
- V32 `additional_references = []` to stop ABBA from treating literature citations as TIFF channel names.
- V25 clean native BrainGlobe installation.
- V17 ABBA visibility patch.

## Important correction

Earlier V33/V37/V38 documentation suggested using NeuroRat MRI as a direct reference replacement. The later diagnostic showed that NeuroRat and Paxinos have zero world-space overlap, so direct affine resampling is invalid and can produce empty/all-zero references.

Therefore, NeuroRat reference replacement is excluded from the main pipeline.

## Current limitation

The main stable atlas still uses a provisional label-edge reference generated from annotation boundaries. It is technically useful for loading and label inspection, but it is not a final anatomical reference.

Reference improvement now lives in separate diagnostics/experiments.
