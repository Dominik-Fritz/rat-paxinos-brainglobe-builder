# V37 Complete Stable Builder

This package returns to the complete builder workflow and keeps the useful fixes:

- V33 real NeuroRat MRI reference replacement
- V36 ABBA Python environment repair
- V34/V35 orientation diagnostics
- hemispheres.tiff fix
- additional_references = [] fix

## Why this exists

Your V36 report shows the ABBA Python environment is fixed, but the installed atlas still reports:

```text
reference_strategy provisional_label_edge_reference_generated_from_annotation_boundaries
```

That means the installed atlas is still using the old synthetic edge-map reference.

## Recommended use

Copy the full ZIP contents into:

```text
G:\rat-paxinos-brainglobe-builder
```

Then run:

```text
RUN_V37_COMPLETE_STABLE_BUILDER.bat
```

This runs the full builder and then forces the installed atlas to use the V33 real NeuroRat MRI reference.

## Fast path

If the full builder already ran and you only need to fix the installed reference, run:

```text
RUN_FORCE_INSTALLED_V33_REFERENCE.bat
```

Afterwards restart ABBA/Fiji completely and open:

```text
paxinos_watson_rat_40um
```

Then run:

```text
RUN_V34_ORIENTATION_PREVIEWS.bat
```

and inspect:

```text
reports\v34_orientation_previews\index.html
```
