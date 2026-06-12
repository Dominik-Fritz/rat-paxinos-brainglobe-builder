# Known issues and decisions

## Stable baseline

V32 remains the stable compatibility baseline:

- root id 997
- ABBA/Java structure fields
- `hemispheres.tiff`
- `additional_references=[]`
- clean BrainGlobe native install
- optional ABBA visibility patch

## V32.2 additions

V32.2 adds the validated ABBA display orientation:

```text
perm=(1,2,0)
orientation=PIL
```

This was chosen because earlier tests showed:

- `perm=(0,2,1)` improved sagittal display but did not fix ABBA button mapping.
- `perm=(1,0,2)` fixed the button plane but left coronal rotated.
- `perm=(1,2,0)` fixed coronal button mapping and upright coronal display.

## Rejected path: old V33 NeuroRat reference

The old NeuroRat reference replacement is not part of this pipeline. Diagnostic work showed that direct affine resampling from NeuroRat/Waxholm to Paxinos space was not safe. Reintroducing that would be less a fix and more a tribute act to previous mistakes.

## Current remaining limitation

`reference.tiff` is still provisional and generated from label edges. This causes unnaturally straight visible boundaries. The labels should remain discrete; the correct long-term fix is a real anatomical reference background.

## Next reference work

Use the SIGMA experiment, review its report and previews, and only then decide whether a SIGMA-derived reference should become the main `reference.tiff`.
