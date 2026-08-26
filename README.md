# Rat Paxinos/Watson BrainGlobe Atlas Builder

This repository builds and installs the local BrainGlobe-compatible
`paxinos_watson_rat_40um` atlas for Fiji/ABBA. Version 0.3.0 adds a manually
registered Waxholm Space (WHS) Nissl reference channel while preserving the
Paxinos/Watson annotation and ontology as the authoritative atlas data.

The repository has one Windows entry point:

```text
run_builder.bat
```

All other programs under `src/` are implementation or maintenance tools and do
not need to be started for a normal build.

## Channel layout

| ABBA channel | Reference name | Function |
| --- | --- | --- |
| Ch. 0 | `reference` | Label-derived Paxinos display reference |
| Ch. 1 | `soft_region_fill_reference` | Optional soft region-fill aid |
| Ch. 2 | `distance_to_2d_outline_reference` | Optional distance-to-outline aid |
| Ch. 3 | `waxholm_anatomy_reference` | Manually registered WHS Nissl visual aid |

`annotation.tiff`, `annotation.nii.gz`, and `structures.json` remain the source
of truth for region lookup. Ch. 3 is not an annotation and must not be used to
replace or modify Paxinos labels.

## Quick start

### Published 0.3.0 prerelease

After the Nissl package has been uploaded and pinned in
`resources/optional_ch03/nissl_release_asset.json`, extract the source archive
to a short Windows path and run:

```cmd
cd /d G:\paxinos_030
run_builder.bat
```

The builder downloads and verifies missing source data, builds the atlas,
resolves the versioned Nissl registration asset, installs all channels, applies
the ABBA compatibility patches, and writes a final report.

### Required embedded package for the next validation

The build has no workstation fallback and reads only:

```text
resources\optional_ch03\nissl_registration_0_3_0\
```

Keep the tracked `registration_manifest.json` and add the two required files
listed in that directory's README. Copy the complete project root including this
folder to the second computer; no `G:` drive or environment variable is used.

## What the single builder does

The console output is divided into six phases:

1. **Runtime and dependency setup**
   - detects Python 3.11 or 3.12;
   - creates or repairs `.venv`;
   - installs requirements;
   - verifies `brainglobe-atlasapi==2.3.1` and runs `pip check`.
2. **Source data and ontology preparation**
   - downloads and verifies the Paxinos source files;
   - analyzes labels and placeholder context;
   - builds and cleans the structure hierarchy.
3. **Provisional atlas construction and validation**
   - builds the provisional package;
   - exports TIFFs and hemispheres;
   - validates geometry, IDs, root structure, and BrainGlobe loading.
4. **Release candidate construction and native installation**
   - creates the official candidate and tarball;
   - applies metadata and ABBA structure compatibility;
   - installs a clean native BrainGlobe atlas.
5. **Registered Nissl channel**
   - resolves a local or versioned release package;
   - inventories and hashes provenance files;
   - maps the registered stack to the Paxinos grid;
   - installs Ch. 3 as TIFF and NIfTI;
   - repacks the official candidate including Ch. 3.
6. **ABBA integration and final report**
   - patches ABBA local-atlas visibility;
   - hides the native generated borders display source;
   - writes `reports\BUILD_SUMMARY.txt`.

The ABBA patch and Nissl import are enabled by default. Diagnostic opt-outs are
available, but they do not represent the intended 0.3.0 release build:

```cmd
run_builder.bat --no-patch-abba
run_builder.bat --without-nissl
```

## Nissl registration and AP direction

The accepted ImageJ stack is ordered in the same logical direction as the
Paxinos target: **anterior to posterior**. This is fixed in the package manifest:

```text
"stack_order": "anterior-to-posterior"
```

The builder does not accept an environment override, ensuring that both test
computers use the same direction and sequence offset.

## Registered stack geometry

The accepted registration uses WHS source indices 189 through 776, inclusive,
for a total of 588 manually registered sections. The Paxinos annotation mask
contains 589 non-empty AP planes. Visual validation showed that direct mapping
placed every Nissl section one target position too far left. The package manifest
therefore sets `target_sequence_offset` to `1`: target position zero remains
outside the one-to-one sequence and the 588 registered sections are assigned to
positions 1 through 588. Because no separately registered anterior edge image
exists, position zero displays a documented duplicate of the nearest registered
section. No interpolation or new registration is performed.

The ImageJ export has the shape `(588, 656, 940)` at an in-plane calibration of
19.5 µm. The atlas TIFF grid is `(608, 286, 409)` in AP/SI/LR order at 40 µm.
The importer samples the centered physical ImageJ canvas onto the Paxinos grid.
This is a calibrated grid conversion, not a new anatomical registration; the
manually defined BigWarp transformations remain unchanged.

The NIfTI output is separately oriented and checked against
`annotation.nii.gz`. Unknown dimensions or ambiguous orientations terminate the
build rather than producing an unverified channel.

## Scientific interpretation and limitations

The Nissl images originate from the Waxholm Space rat atlas, whereas the label
volume originates from the Paxinos/Watson atlas. They represent different
specimens and source anatomies and cannot be expected to be exactly congruent.

The retained Nissl sections were assigned to Paxinos label-volume planes using
BigWarp spline transformations. Registrations propagated to neighboring
sections were reviewed and adjusted where necessary to improve structural
agreement. Residual local differences may remain, particularly at far anterior
and posterior levels.

Consequently:

- Ch. 3 is an orientation and visualization aid.
- Paxinos labels remain authoritative for region assignment.
- Nissl boundaries must not be interpreted as replacement region boundaries.
- The source images, ABBA state, transform exports, ImageJ stack, checksums, and
  build reports form the registration provenance record.

## Reproducible GitHub release asset

The complete ABBA/QuPath package is too large for normal Git history. It is
distributed as a GitHub release asset and described by:

```text
resources\optional_ch03\nissl_release_asset.json
```

The manifest pins:

- release identifier;
- asset filename;
- direct release download URL;
- SHA-256 checksum;
- expected stack filename, shape, and AP order.

The builder stores verified downloads under:

```text
data\release_assets\0.3.0-prerelease\
```

A partial or checksum-mismatched download is rejected. ZIP extraction is also
checked for unsafe paths. A package is accepted only if it contains exactly one
registered ImageJ stack and at least one ABBA state file.

### Preparing the release asset

This maintainer command creates the immutable ZIP from the completed project:

```cmd
.venv\Scripts\python.exe src\nissl_release_asset.py create ^
  --root . ^
  --source resources\optional_ch03\nissl_registration_0_3_0 ^
  --output paxinos_watson_rat_nissl_registration_0.3.0.zip
```

The command prints the SHA-256 checksum. Upload the ZIP to the GitHub 0.3.0
prerelease, then pin its direct download URL and checksum without manual JSON
editing:

```cmd
.venv\Scripts\python.exe src\nissl_release_asset.py pin ^
  --root . ^
  --asset paxinos_watson_rat_nissl_registration_0.3.0.zip ^
  --url https://github.com/OWNER/REPOSITORY/releases/download/TAG/paxinos_watson_rat_nissl_registration_0.3.0.zip
```

Commit the resulting manifest update before publishing the final source
archive. The runtime build never depends on a workstation-specific path.

For a private draft asset test, the manifest can be overridden temporarily:

```cmd
set "PAXINOS_NISSL_ASSET_URL=https://example.invalid/release/asset.zip"
set "PAXINOS_NISSL_ASSET_SHA256=<64-character-sha256>"
run_builder.bat
```

## Runtime compatibility

The current local-atlas installation path is tested with:

```text
Python 3.11 or 3.12
brainglobe-atlasapi 2.3.1
ABBA Python 0.10.x or 0.11.x
```

BrainGlobe AtlasAPI 3.x changed interfaces used by this local build pipeline.
The builder therefore pins and verifies 2.3.1. This pin concerns the builder
runtime and does not change the scientific atlas content.

## Outputs and reports

Important generated outputs include:

```text
data\output\brainglobe_official_candidate\paxinos_watson_rat_40um\
data\output\brainglobe_official_candidate\paxinos_watson_rat_40um.tar.gz
resources\optional_ch03\waxholm_anatomy_reference.tiff
reports\BUILD_SUMMARY.txt
reports\ch03_nissl\
reports\nissl_release_asset\
```

The final summary states whether the build succeeded, where the atlas was
installed, whether the Nissl channel was found, which additional references are
registered, and where detailed reports are located.

## ABBA inspection after a successful build

1. Close all Fiji/ABBA windows.
2. Restart Fiji/ABBA.
3. Open `paxinos_watson_rat_40um`.
4. Enable `reference` and `waxholm_anatomy_reference`.
5. Keep the native generated borders source hidden.
6. Verify that both label and Nissl progress anterior to posterior.
7. Confirm in `BUILD_SUMMARY.txt` that `Target sequence offset: 1` was used.
8. Inspect representative anterior, septal, hippocampal, midbrain, and
   posterior levels.

## Troubleshooting

### Release asset cannot be resolved

Read:

```text
reports\nissl_release_asset\nissl_release_asset_summary.txt
```

Before publication, complete the embedded `nissl_registration_0_3_0` directory.
After publication, verify the URL and SHA-256 in the release manifest.

### Nissl channel runs in the wrong AP direction

Confirm that the embedded `registration_manifest.json` is unchanged and reports
`stack_order: anterior-to-posterior` and `target_sequence_offset: 1`.

### Ch. 3 is absent in ABBA

Restart Fiji/ABBA. Confirm that the installed `metadata.json` contains
`waxholm_anatomy_reference` in `additional_references` and that both
`waxholm_anatomy_reference.tiff` and `.nii.gz` exist in the installed atlas.

### Native borders are visible

The builder first performs normal ABBA Python discovery and then a deeper search
when required. Review the V44 report under `reports/` if no installation could
be patched.

### Build failure

Read `reports\BUILD_SUMMARY.txt` first, then inspect the report directory named
by the failing phase. The pipeline stops on failed geometry, checksum,
dependency, metadata, or atlas validation checks instead of silently omitting
the affected component.

## Release acceptance criteria

The 0.3.0 prerelease is ready for publication only after:

1. the Nissl ZIP is uploaded as an immutable release asset;
2. its direct URL and SHA-256 are committed in the manifest;
3. `run_builder.bat` succeeds from a clean root without a local registration
   folder;
4. installation succeeds on a second Windows computer;
5. Ch. 0 and Ch. 3 have the same AP direction;
6. representative levels pass visual ABBA QC;
7. `reports\BUILD_SUMMARY.txt` reports success.

## v0.3.1 incremental test build

Version 0.3.1 deliberately starts from the working 0.3.0 prerelease pipeline.
It does not change the Paxinos annotation, ontology, AP mapping, or validated
Nissl registration. This first increment only fixes optional-component control:
`--no-patch-abba` disables both ABBA patches, missing ABBA is a warning unless
`--require-abba` is supplied, `--without-nissl` is reported accurately, and
`--non-interactive` suppresses `pause`. Broader preflight, transaction, locking,
and dependency changes are intentionally deferred until this smaller Windows
build has been validated.

### v0.3.1 next safety increment

The builder reports free space separately for the volume containing the project,
the configured BrainGlobe installation, and Windows temporary directories. Low
space is initially a warning; only a critically full volume stops the build.
Duplicate volumes are reported once with all of their roles.

The Nissl importer now records per-plane edge-coverage diagnostics comparing the
non-zero registered signal with the Paxinos label bounds. It does not stretch,
fill, or re-register the validated images. Installed metadata includes a preferred
warm-yellow (`#FFD54F`), low-opacity (`0.22`) display hint. This is deliberately a
client hint: current ABBA versions may still require the converter color and
opacity to be applied in the ABBA UI until a separately tested loader patch is
available.

Repeated clean installations no longer accumulate full atlas backups inside the
BrainGlobe directory (usually on `C:`). Existing and newly created native-atlas
backups are moved, never deleted, under `backups/native_brainglobe` on the builder
volume. This preserves rollback material while freeing the installation volume
for the new atlas and its display channels.

The large per-plane Nissl diagnostic can be reduced to a small text file with:

```cmd
.venv\Scripts\python.exe src\summarize_nissl_coverage.py --root .
```

Share `reports\ch03_nissl\NISSL_EDGE_COVERAGE_SUMMARY.txt`; the full JSON is not
needed for initial edge-gap analysis.
