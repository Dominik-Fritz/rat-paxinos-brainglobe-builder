# ABBA usage notes

After running `run_builder.bat` and accepting the ABBA patch, restart ABBA/Fiji completely.

Open:

```text
Open Atlas
```

Select:

```text
paxinos_watson_rat_40um
```

Recommended first orientation test:

```text
Coronal
X_axis: RL
Y_axis: SI
Z_axis: AP
```

Then import one test section and check:

- atlas loads
- reference display appears
- annotation overlay appears
- structure tree opens
- region names appear on selection
- gross orientation is plausible

If the atlas does not appear, run:

```text
patch_abba_visibility.bat
```

then restart ABBA again.
