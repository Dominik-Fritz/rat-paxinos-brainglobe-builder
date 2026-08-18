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

The `reference` channel is an ABBA-tested synthetic soft label-derived display channel generated from the Paxinos labels. The `borders` channel must stay OFF because ABBA's generated borders can create filled 3D-looking surfaces in MultiSlice view.

## Current baseline

- Active atlas: `paxinos_watson_rat_40um`
- Mode: LabelAtlas-only
- MRI/Waxholm/SIGMA/NeuroRat reference channels: postponed
- Active reference channel: synthetic soft label-derived Paxinos reference
- Annotation files remain full label volumes for ABBA lookup
- `annotation.tiff` and `annotation.nii.gz` must not be patched into border-only files

## What this release does not do yet

- It does not automatically download source data yet.
- It does not create Nissl/MRI/SIGMA/Waxholm anatomical reference channels.
- It does create a synthetic soft reference channel from the Paxinos labels.
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


## Synthetic soft reference channel

The current ABBA-tested display reference is generated reproducibly during `run_builder.bat` by:

```text
src\v34_apply_synthetic_soft_reference.py
```

This script is called after the V32.27 LabelAtlas display baseline. It overwrites the old border-only display proxy with:

```text
reference_strategy = synthetic_label_derived_soft_region_fill_reference
```

Important interpretation:

```text
The reference channel is label-derived.
It is congruent with the Paxinos annotation volume.
It is not Nissl, MRI, SIGMA, Waxholm, NeuroRat, or external anatomy.
```

ABBA display state remains:

```text
reference (Ch. 0) = ON
borders   (Ch. 1) = OFF
```

Optional helper BATs:

```text
RUN_05_INSTALL_SOFT_SYNTHETIC_REFERENCE.bat
RUN_06_RESTORE_BORDER_REFERENCE_PROXY.bat
RUN_07_STATUS_SYNTHETIC_REFERENCE.bat
```

`RUN_05` applies the synthetic soft reference to the installed atlas only.  
`RUN_06` restores the older V32.27 border-proxy reference baseline.  
`RUN_07` performs a dry-run/status check.

## Optional WHS Nissl Ch03 interpretation

The optional Ch03 Nissl channel is a manually registered visual aid, not a
Paxinos-derived anatomical ground truth. WHS Nissl sections were assigned to
their corresponding Paxinos label-volume planes with BigWarp spline
transformations. Registration aimed for the highest practical structural
agreement, and every retained section was visually checked and corrected where
necessary.

The Nissl images originate from the Waxholm Space atlas, whereas the annotation
volume originates from the Paxinos/Watson atlas. They therefore represent
different source anatomies and cannot be expected to coincide exactly. Local
deviations between Nissl structures and label boundaries may remain despite
careful registration. These differences can be more apparent in the far
anterior and far posterior parts of the series. The channel must consequently
be interpreted only as an orientation and visualization aid; the Paxinos label
volume remains authoritative for region assignment.

## Reconstructing the manually registered Ch03 channel

The final manual registration package should retain the ABBA state, standardized
ABBA project, complete QuPath project and original exported WHS slices, the ABBA
BDV JSON export, and the registered ImageJ TIFF stack. Before importing any
large artifact, inventory the package from the repository root:

```text
run_ch03_registration.bat abba-package-inspect G:\path\to\nissl_registration
```

Use the actual filesystem path, not the Windows Explorer drive label. For
example, if Explorer shows `Dominik_different_projects (G:) > nissl_registration`,
the path is normally `G:\nissl_registration`, not
`G:\Dominik_different_projects\nissl_registration`.

The inventory, including hashes, TIFF dimensions, source-index coverage, BDV
source classes, and serialized thin-plate-spline counts, is written to
`reports/v53_ch03_landmarks/abba_package_inventory.json`.

The preferred final artifact is a registered TIFF with the exact atlas shape
`AP,SI,LR = 608,286,409`. A 588-plane ImageJ stack can be imported directly only
when it contains exactly one plane for every non-empty annotation AP plane and
already has the exact target in-plane dimensions. The plane order must be stated
explicitly; the importer never guesses, crops, or resizes registered data:

```text
run_ch03_registration.bat ch03-import-imagej-stack G:\path\to\registered_slices_ImageJ_stack.tif anterior-to-posterior
```

If strict validation fails, keep the original package unchanged and use the
inventory report to implement a documented reconstruction from the standardized
ABBA/BDV transformations instead of forcing a geometrically ambiguous resize.

## Builder runtime compatibility

The Windows builder supports Python 3.11 and 3.12 and pins
`brainglobe-atlasapi==2.3.1`. BrainGlobe 3.x is not used because its atlas build
and installation API is incompatible with this legacy 0.2.x builder pipeline.
`run_builder.bat` recreates an unusable local virtual environment, verifies the
pinned BrainGlobe version after installation, and runs `pip check` before the
build starts.
