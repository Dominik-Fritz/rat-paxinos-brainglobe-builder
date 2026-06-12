# ABBA usage notes

After running `run_builder.bat`, restart ABBA/Fiji completely.

Open:

```text
Open Atlas
```

Select:

```text
paxinos_watson_rat_40um
```

Expected after V32.2:

```text
Coronal    -> coronal and upright
Sagittal   -> sagittal
Horizontal -> horizontal
```

If the atlas does not appear, run:

```text
patch_abba_visibility.bat
```

then restart ABBA/Fiji again.

## Known display limitation

The background reference is still a provisional label-edge image, not a true Nissl/MRI reference. Straight label boundaries are therefore expected. The labels themselves should not be smoothed; the proper fix is a real anatomical reference image.
