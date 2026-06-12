# V32.2 Oriented Reference Prep

This version integrates the ABBA orientation that was validated manually in the test atlas `paxinos_watson_rat_40um_abba_coronal_upright_test`.

## Final orientation decision

The accepted transform is:

```text
perm=(1,2,0)
orientation=PIL
```

This maps the original display model:

```text
[LR, AP, SI] / LPI
```

to:

```text
[AP, SI, LR] / PIL
```

This fixes the ABBA plane buttons and coronal upright display.

## Channel cleanup

Earlier `hemispheres.tiff` filled the whole rectangular volume with 1/2. That could behave like a persistent third layer in ABBA. V32.2 now generates hemispheres only inside the annotation mask:

```text
0 = outside annotation
1 = one LR side inside annotation
2 = other LR side inside annotation
```

`hemispheres.tiff` remains available through:

```json
"hemispheres_file": "hemispheres.tiff"
```

but is not listed as a normal file/channel inside `metadata["files"]`.

## Reference image status

The mainline still uses the provisional label-edge reference. This is intentional. The NeuroRat direct resampling line is not used because it was invalidated by the overlap diagnostic. A real anatomical reference should only be merged after the SIGMA experiment produces a visually plausible and non-zero candidate.
