# V32.15 Emergency restore LabelAtlas annotation display

This package is a rollback/repair step after the V32.14 `annotation.tiff` display-proxy experiment made the ABBA display worse.

## What it does

- Restores `annotation.tiff` in the BrainGlobe cache to a full label volume.
- Keeps the V32.13 strict LabelAtlas baseline: zero reference/no MRI helper channels.
- Does not restore or promote any Waxholm/SIGMA/NeuroRat reference channel.
- Does not delete raw data.
- Backs up current cache files before writing.

## Run first

Close Fiji/ABBA completely, then run:

```bat
RUN_V32_15_EMERGENCY_RESTORE_CACHE_LABEL_ANNOTATION.bat
```

Then restart Fiji/ABBA and open:

```text
paxinos_watson_rat_40um
```

## Only if cache-only restore does not work

Use the aggressive rebuild only if the cache restore fails:

```bat
RUN_V32_15_AGGRESSIVE_REBUILD_PROJECT_AND_CACHE_ANNOTATION_FROM_NIFTI.bat
```

This rebuilds `annotation.tiff` in both the project output and the BrainGlobe cache from `annotation.nii.gz`.

## Reports

Upload:

```text
reports\v32_15_emergency_restore_label_annotation\v32_15_emergency_restore_label_annotation_summary.txt
reports\v32_15_emergency_restore_label_annotation\v32_15_emergency_restore_label_annotation_report.json
```

## Important interpretation

If filled annotation views remain after the annotation is restored, they are ABBA's normal annotation rendering and should not be fixed by corrupting `annotation.tiff`. That has already been tested and was a terrible little gremlin.
