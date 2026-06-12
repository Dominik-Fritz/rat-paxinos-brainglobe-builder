# V32.13 LabelAtlas display cleanup

Purpose: restore a strict label-only Paxinos working state after MRI/Waxholm test-atlas experiments.

This package does **not** modify raw data. It does **not** promote Waxholm/SIGMA/NeuroRat. It preserves the Paxinos annotation and structure ontology.

## What it fixes

In ABBA/BDV the cleaned atlas can still show multiple duplicate-looking sources at low display scale. The likely causes are synthetic helper/display volumes rather than the actual Paxinos annotation:

- `reference.nii.gz` / `reference.tiff` generated from labels or edges
- `hemispheres.tiff` as a filled helper mask
- old test atlases still present in the BrainGlobe cache or `last_versions.conf`

V32.13 makes the active atlas strict-label display only by:

- quarantining experimental/test atlas folders
- removing test atlas entries from `last_versions.conf`
- zeroing `reference.nii.gz` and `reference.tiff`
- in strict mode: also zeroing `hemispheres.tiff`
- keeping `annotation.nii.gz`, `annotation.tiff`, `structures.json`, and `structures.csv` unchanged

## Recommended use

Close Fiji/ABBA first.

Run:

```bat
RUN_V32_13_LABEL_ONLY_DISPLAY_CLEANUP_DRY_RUN.bat
```

Then run the actual strict cleanup:

```bat
RUN_V32_13_STRICT_LABEL_ONLY_DISPLAY_CLEANUP.bat
```

If Python dependencies are missing:

```bat
RUN_V32_13_INSTALL_DEPS_AND_STRICT_CLEANUP.bat
```

## Safer variant

If you want to keep `hemispheres.tiff` untouched and only hide the synthetic reference source:

```bat
RUN_V32_13_REFERENCE_ONLY_DISPLAY_CLEANUP.bat
```

However, if the filled duplicate source in ABBA comes from `hemispheres.tiff`, this safer variant will not remove that filled view.

## Restore

The cleanup backs up patched files and quarantined atlases under:

```text
G:\rat-paxinos-brainglobe-builder\data\output\v32_13_label_only_display_cleanup_backups
```

To restore the latest backed-up display files:

```bat
RUN_V32_13_RESTORE_LATEST_BACKUP.bat
```

## Reports

After running, upload these if review is needed:

```text
G:\rat-paxinos-brainglobe-builder\reports\v32_13_label_only_display_cleanup\v32_13_label_only_display_cleanup_summary.txt
G:\rat-paxinos-brainglobe-builder\reports\v32_13_label_only_display_cleanup\v32_13_label_only_display_cleanup_report.json
```

## Expected result

After restarting Fiji/ABBA, the only active Paxinos atlas should be:

```text
paxinos_watson_rat_40um
```

MRI/Waxholm/SIGMA/NeuroRat/test atlases should not appear as active choices, and the synthetic filled helper views should disappear or go black. If ABBA still generates filled views, those are likely derived internally from the annotation source itself rather than from `reference`/`hemispheres` files.
