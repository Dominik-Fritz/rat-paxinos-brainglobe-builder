# ABBA button mapping test atlas

This package creates a separate non-destructive BrainGlobe atlas to test whether ABBA's plane buttons can be made anatomically consistent.

It does **not** overwrite:

```text
paxinos_watson_rat_40um_v1.0
```

It creates:

```text
paxinos_watson_rat_40um_abba_buttons_test_v1.0
```

ABBA name:

```text
paxinos_watson_rat_40um_abba_buttons_test
```

## Why

The previous display test fixed the sagittal rotation but did not fix ABBA's plane button mapping:

```text
Coronal   -> sagittal
Sagittal  -> horizontal
Horizontal -> coronal
```

This test changes the array axis order from:

```text
old: [LR, AP, SI]
```

to:

```text
new: [AP, LR, SI]
```

using:

```text
perm = (1, 0, 2)
orientation = PLI
```

Expected result:

```text
Coronal   -> coronal
Sagittal  -> sagittal
Horizontal -> horizontal
```

Sagittal should still show:

```text
anterior left, posterior right
superior top, inferior bottom
```

## Run

Copy this package into your project root:

```text
G:\rat-paxinos-brainglobe-builder
```

Then run:

```text
RUN_ORIENTATION_BUTTON_MAPPING_TEST.bat
```

Restart Fiji/ABBA completely and open:

```text
paxinos_watson_rat_40um_abba_buttons_test
```

## If this works

Then this is the correct axis order to integrate into the full builder.

Do not integrate the previous `perm=(0,2,1)` test into the main builder. It solved the sagittal display but not ABBA's button mapping.
