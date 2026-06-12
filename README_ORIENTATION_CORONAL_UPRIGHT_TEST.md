# Orientation Coronal Upright Test

This is a non-destructive ABBA display-orientation test for the local Paxinos BrainGlobe atlas.

It creates a separate test atlas:

```text
paxinos_watson_rat_40um_abba_coronal_upright_test
```

It does **not** overwrite:

```text
paxinos_watson_rat_40um
```

## Why this exists

The previous button-mapping test made ABBA's **Coronal** button show a coronal section, but the displayed coronal section was still rotated: superior/inferior appeared horizontally and left/right appeared vertically.

This test uses:

```text
old axes: [LR, AP, SI]
new axes: [AP, SI, LR]
perm = (1, 2, 0)
metadata orientation: PIL
```

Expected ABBA behavior:

```text
Coronal    -> AP fixed, display SI vertical and LR horizontal
Sagittal   -> LR fixed
Horizontal -> SI fixed
```

## Run

Copy/extract this package into:

```text
G:\rat-paxinos-brainglobe-builder
```

Then double-click:

```text
RUN_ORIENTATION_CORONAL_UPRIGHT_TEST.bat
```

Restart Fiji/ABBA completely and open:

```text
paxinos_watson_rat_40um_abba_coronal_upright_test
```

## What to report back

For each ABBA button:

```text
Coronal:
- Is it truly coronal?
- Is dorsal/top up?
- Is left/right horizontal?

Sagittal:
- Is it truly sagittal?
- Is anterior/posterior horizontal?

Horizontal:
- Is it truly horizontal?
```
