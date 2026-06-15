# Rat Paxinos/Watson BrainGlobe Builder

Builds and installs a local BrainGlobe-compatible Paxinos/Watson rat LabelAtlas for use in ABBA/Fiji.

## End-user quick start

1. Download/unzip this project release.
2. Put the required BlueBrainHeadModels/Paxinos source files into:

   ```text
   data/raw/bluebrainheadmodels/
   ```

3. Double-click:

   ```text
   run_builder.bat
   ```

4. Restart Fiji/ABBA completely.
5. Open this atlas:

   ```text
   paxinos_watson_rat_40um
   ```

6. In ABBA, use this display state:

   ```text
   reference (Ch. 0) = ON
   borders   (Ch. 1) = OFF
   ```

The `reference` channel is a clean 2D coronal border display proxy generated from the Paxinos labels. The `borders` channel must stay OFF because ABBA's generated borders can create filled 3D-looking surfaces in MultiSlice view.

## Current baseline

- Active atlas: `paxinos_watson_rat_40um`
- Mode: LabelAtlas-only
- MRI/Waxholm/SIGMA/NeuroRat reference channels: postponed
- Annotation files remain full label volumes for ABBA lookup
- `annotation.tiff` and `annotation.nii.gz` must not be patched into border-only files

## What this release does not do yet

- It does not automatically download source data yet.
- It does not create Nissl/MRI/additional reference channels.
- It does not run Waxholm/SIGMA/NeuroRat registration experiments.

These features are intentionally postponed until the LabelAtlas pipeline, release structure, and ontology/acronym review are stable.

## Expected source data

The current release expects the raw source files to be present locally under:

```text
data/raw/bluebrainheadmodels/
```

Automated download/repair/verification will be added later via the planned Data Manager.

## Troubleshooting

If ABBA shows filled gray/colored blocks instead of clean label boundaries, check this first:

```text
reference (Ch. 0) = ON
borders   (Ch. 1) = OFF
```

Do not patch `annotation.tiff` to border-only. That breaks ABBA label display/lookup. Yes, we already stepped on that rake so future users can keep their shins intact.

## Development notes

This release candidate is based on the V32.17 LabelAtlas Display Baseline and intentionally removes MRI/reference-channel experiments from the active workflow. Experimental scripts may exist historically in the development repository, but they are not part of the end-user workflow.
