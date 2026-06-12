# V32.14 Annotation TIFF display proxy

Purpose: ABBA still shows every third filled label view after V32.13 zeroed reference and hemispheres. V32.13 therefore proved that the remaining filled source is probably generated from the annotation source itself.

This package is a reversible workaround/test:

- It preserves `annotation.nii.gz`, `structures.json`, `structures.csv`, `reference.*`, and raw data.
- It patches only `annotation.tiff`, because ABBA may use that TIFF as a display source.
- Recommended first step: cache-only border proxy.

## Recommended runner

Run:

```bat
RUN_V32_14_CACHE_ANNOTATION_TIFF_BORDER_ONLY.bat
```

Then fully restart Fiji/ABBA and open only:

```text
paxinos_watson_rat_40um
```

Expected: the every-third filled panel becomes border-only or disappears as a filled view.

## Restore

If ABBA label lookup or region behavior breaks, run:

```bat
RUN_V32_14_RESTORE_LATEST_BACKUP.bat
```

## Persistent project patch

Only use this if the cache-only test works and you want the project candidate patched too:

```bat
RUN_V32_14_BOTH_ANNOTATION_TIFF_BORDER_ONLY.bat
```

This is still a display workaround, not a new atlas release.
