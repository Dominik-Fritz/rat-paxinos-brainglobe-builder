# Rat Paxinos/Watson BrainGlobe Atlas Builder

This repository builds and installs the local BrainGlobe-compatible
`paxinos_watson_rat_40um` atlas for Fiji/ABBA. The 0.3.0 prerelease workflow adds
the final manually registered Waxholm Space Nissl series as an optional visual
reference channel while leaving the Paxinos annotation and ontology
authoritative and unchanged.

## 0.3.0 prerelease channel layout

| ABBA channel | Name | Purpose |
| --- | --- | --- |
| Ch. 0 | `reference` | Label-derived Paxinos display reference |
| Ch. 1 | `soft_region_fill_reference` | Optional soft label-derived orientation aid |
| Ch. 2 | `distance_to_2d_outline_reference` | Optional label-outline distance aid |
| Ch. 3 | `waxholm_anatomy_reference` | Optional manually registered WHS Nissl aid |

`annotation.tiff`, `annotation.nii.gz`, and `structures.json` remain the source
of truth for region lookup. Ch. 3 must never be interpreted as a replacement
annotation.

## Required local inputs

### Paxinos source data

`run_builder.bat` uses the release data manager to ensure the minimal Paxinos
inputs under:

```text
data\raw\bluebrainheadmodels\
```

Required files are the Paxinos/Watson atlas NIfTI and label table. The cortex
label table is optional.

### Final ABBA registration package

The Nissl build requires the completed registration package containing at least:

```text
final_for_V_0_3.abba
project.qpproj
data\
registered_slices_ImageJ_stack.tif
bdv_export_registered_slices_to_BDV_Json_Dataset
```

The package may be supplied in either of these ways, in priority order:

1. Set `PAXINOS_NISSL_PACKAGE` to its full path.
2. Copy it to `resources\optional_ch03\abba_registration_package\`.
3. On the original workstation, keep it at `G:\nissl_registration`.

The original ABBA state, standardized project, QuPath project, BDV JSON and
ImageJ stack are provenance artifacts and must be retained unchanged.

## One-command Windows build

For the original workstation, where the package is at `G:\nissl_registration`,
open the extracted repository root and double-click:

```text
run_builder.bat
```

The equivalent CMD command is:

```cmd
cd /d G:\path\to\the\new\repository && call run_builder.bat
```

For another package location:

```cmd
cd /d G:\path\to\the\new\repository
set "PAXINOS_NISSL_PACKAGE=D:\data\nissl_registration"
set "PAXINOS_NISSL_STACK_ORDER=posterior-to-anterior"
call run_builder.bat
```

The final registration export currently uses `posterior-to-anterior` stack
order by default. To override it, set `PAXINOS_NISSL_STACK_ORDER` explicitly to
`anterior-to-posterior` or `posterior-to-anterior` before running the builder.
The ABBA visibility patch is enabled by default, so the intended test requires
only `run_builder.bat`; `--no-patch-abba` remains available for diagnostics.

The single builder performs all of the following:

1. Detects Python 3.11/3.12 or offers to install Python 3.12.
2. Creates or repairs the local `.venv`.
3. Installs and verifies `brainglobe-atlasapi==2.3.1`.
4. Downloads/verifies the minimal Paxinos source package.
5. Builds and validates the Paxinos LabelAtlas candidate.
6. Installs the native atlas into the local BrainGlobe cache.
7. Applies the validated three-channel Paxinos display layout.
8. Inventories and hashes the ABBA/QuPath registration package.
9. Strictly reconstructs a `608 x 286 x 409` AP/SI/LR Nissl volume.
10. Installs that volume as `waxholm_anatomy_reference` TIFF/NIfTI and updates
    atlas metadata.
11. Applies the ABBA visibility/borders compatibility patch.

The build fails clearly instead of silently omitting Ch. 3 when the registration
package is absent or geometrically ambiguous. A legacy label-only development
build can be requested explicitly with:

```cmd
run_builder.bat --without-nissl
```

## Nissl reconstruction validation

The final manually used range contains 588 registered slices corresponding to
WHS export indices 189 through 776. The Paxinos mask contains 589 non-empty AP
planes because it includes one additional minimally populated terminal plane
for which no Nissl registration exists. The importer removes only that terminal
Paxinos endpoint and maps the 588 manually curated sections one-to-one; it does
not invent or interpolate a Nissl section.

The observed ImageJ export has a `(588,656,940)` stack/canvas shape at the WHS
in-plane calibration of 19.5 um. The importer samples its centered physical
field of view onto the `(608,286,409)` Paxinos 40-um grid. This conversion only
changes the output sampling grid; it does not estimate or alter the manual
BigWarp registration.

The package can be inspected independently before a full build:

```cmd
run_ch03_registration.bat abba-package-inspect "G:\nissl_registration"
```

The compact report is written to:

```text
reports\v53_ch03_landmarks\abba_package_inventory.json
```

It records file hashes, TIFF geometry, source-index coverage, BDV source classes
and serialized thin-plate-spline counts. Large embedded ImageJ strings are
represented only by their size, SHA-256 hash and a short preview. The inventory
is rejected if it unexpectedly exceeds 8 MiB.

The complete import/install step can also be run independently:

```cmd
run_ch03_registration.bat ch03-build-from-package "G:\nissl_registration" posterior-to-anterior
```

## Scientific interpretation of Ch. 3

The Nissl images originate from the Waxholm Space rat atlas, while the label
volume originates from the Paxinos/Watson atlas. They represent different source
anatomies and therefore cannot be exactly congruent.

The retained Nissl sections were assigned to their Paxinos label-volume planes
using BigWarp spline transformations. Every retained slice was visually checked,
and registrations copied to neighboring slices were corrected manually whenever
the structural agreement was insufficient. The aim was the highest practical
agreement of major anatomical structures.

Local deviations between Nissl anatomy and label boundaries may remain despite
careful registration, especially in far anterior and far posterior regions.
Accordingly:

- Ch. 3 is an orientation and visualization aid.
- Paxinos labels remain authoritative for region assignment.
- Nissl contrast or boundaries must not be used to overwrite atlas labels.
- The ABBA state, source images and transform exports remain part of the
  provenance record.

## Runtime compatibility

This legacy local-atlas build pipeline is pinned to:

```text
Python 3.11 or 3.12
brainglobe-atlasapi 2.3.1
```

BrainGlobe 3.x changed the build/install API expected by this pipeline and is
not used. The builder verifies the exact AtlasAPI version and runs `pip check`
before processing atlas data. A broken or unsupported local virtual environment
is recreated automatically.

## ABBA display and QC

After a successful build:

1. Restart Fiji/ABBA completely.
2. Open `paxinos_watson_rat_40um`.
3. Enable `reference` and `waxholm_anatomy_reference`.
4. Keep the native generated borders source hidden.
5. Inspect anterior, septal, hippocampal, midbrain and posterior levels.

QC and provenance reports are written beneath:

```text
reports\
reports\v53_ch03_landmarks\
```

The accepted active Nissl asset is generated at:

```text
resources\optional_ch03\waxholm_anatomy_reference.tiff
```

## Troubleshooting

### Registration package not found

Windows Explorer may show a drive label such as
`Dominik_different_projects (G:)`. The label is not a directory component. If
the package appears directly below that drive, use:

```text
G:\nissl_registration
```

not:

```text
G:\Dominik_different_projects\nissl_registration
```

### Nissl stack geometry rejected

Do not force a resize. Preserve the package and inspect the reported TIFF shape,
axes, calibration and first/last slice order. A rejected import means the
ImageJ canvas has not yet been proven equivalent to the Paxinos target grid.

### Filled gray/colored blocks in ABBA

Keep ABBA's native generated borders source hidden. Do not replace
`annotation.tiff` with a border-only volume; doing so breaks label lookup.

### Ch. 3 does not appear immediately

Restart Fiji/ABBA after installation. Confirm that the installed atlas metadata
contains `waxholm_anatomy_reference` in `additional_references` and that both its
TIFF and NIfTI files exist in the installed atlas folder.

## Development and release policy

Large ABBA/QuPath/TIFF registration artifacts are not committed directly to
normal Git history. A release package must provide them as an external release
asset or place them in the documented local registration-package directory.
Source code, manifests, hashes, provenance, strict reconstruction logic and QC
remain version controlled.

The 0.3.0 prerelease is acceptable only after a clean-root Windows build, a
second-computer installation test, strict volume validation and visual ABBA QC.
