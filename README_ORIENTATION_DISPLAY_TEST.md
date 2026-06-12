# Orientation Display Test Atlas

This package creates a **separate** BrainGlobe atlas test copy.

It does not overwrite:

```text
paxinos_watson_rat_40um_v1.0
```

## Why this exists

The current Paxinos atlas is stored as:

```text
shape: [409, 608, 286]
orientation: LPI
axis 0: left/right
axis 1: anterior/posterior
axis 2: superior/inferior
```

When a sagittal slice is displayed directly from the raw array, axis 1 is vertical and axis 2 is horizontal. Therefore anterior/posterior appears vertical, which is the 90° rotated sagittal view you observed.

This test creates a display-oriented copy with:

```text
perm = (0, 2, 1)
old orientation: LPI
new orientation: LIP
old shape: [409, 608, 286]
new shape: [409, 286, 608]
```

That makes sagittal views display as:

```text
left/right fixed
vertical: superior/inferior
horizontal: anterior/posterior
```

So anterior is left and posterior is right.

## Run

Copy these files into the project root:

```text
G:\rat-paxinos-brainglobe-builder
```

Then run:

```text
RUN_ORIENTATION_DISPLAY_TEST.bAT
```

It installs a separate atlas named:

```text
paxinos_watson_rat_40um_sag_ap_lr_test
```

in:

```text
C:\Users\<you>\.brainglobe\paxinos_watson_rat_40um_sag_ap_lr_test_v1.0
```

and patches `last_versions.conf` with a new entry.

## After running

Restart Fiji/ABBA completely.

Open:

```text
paxinos_watson_rat_40um_sag_ap_lr_test
```

Compare it to:

```text
paxinos_watson_rat_40um
```

## Important

This does not solve the synthetic reference problem. The reference is still the label-edge reference unless a separate SIGMA reference test is applied later.

This test only answers:

```text
Does perm=(0,2,1) fix the sagittal 90° display problem?
```
