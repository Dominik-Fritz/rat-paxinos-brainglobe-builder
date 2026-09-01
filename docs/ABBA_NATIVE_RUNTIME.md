# Builder-local native ABBA runtime

The authoritative registration is `final_for_V_0_3.abba`, SHA-256
`e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06`.
The vendored package contents live directly in `vendor/abba_python_0_11_0`.

`run_builder.bat` installs Eclipse Temurin JDK `17.0.14+7` below
`data/native_abba_runtime/java`. `bootstrap_native_java.ps1` obtains the
version-specific asset metadata from the Eclipse Adoptium API, verifies the
archive against the publisher's SHA-256 before extraction, and writes the URL
and observed hash to `runtime-manifest.json`. A mismatching marker, archive, or
Java layout is rejected rather than repaired using a global Java installation.

The Java dependency resolver also requires a Maven executable. The builder
therefore installs Apache Maven `3.9.9` below
`data/native_abba_runtime/maven`, verifies the downloaded archive against the
publisher's SHA-512, and confines both Maven's user home and artifact repository
to `data/native_abba_runtime`. Neither `PATH` nor the user's `.m2` directory is
used as a fallback. Missing, corrupt, and wrong-version Maven runtimes are
reported separately.

Legacy jgo also reads repository definitions from `~/.jgo.rc`. The runtime
redirects both `HOME` and `USERPROFILE` to its builder-local user directory and
writes an explicit repository list there. Besides SciJava Public this includes
the Open Microscopy Environment repository required by the pinned Bio-Formats
and OMERO transitive dependencies. The Maven bootstrap itself sets
`JAVA_HOME` to the builder-local Temurin runtime before executing `mvn.cmd`, so
an older globally installed Java cannot be selected.

ABBA 0.11.0 initializes PyImageJ with the exact Maven coordinates recorded by
the vendored `Abba.get_java_dependencies()`. The preflight resolves the actual
Java classes used by the vendor for ABBA startup, state loading, moving-source
import, native BDV export, SourceAndConverter handling, and BigWarp TPS. Java,
JGO, Maven, ImageJ, BrainGlobe, downloads, and temporary data use only
builder-local paths. The historical QuPath path is detected but never used.

The old TIFF stack is comparison-only. The Python TPS implementation remains
diagnostic-only and cannot install Ch03. A native test installation records
`visual_parity_status: pending` and `release_eligible: false`; only a separate
visual acceptance may change those fields to `passed` and `true`.

The uploaded vendor directory contains no upstream `METADATA`, `dist-info`, or
license file. It is therefore retained as source provenance only; this
repository does not infer additional redistribution rights from its presence.

## Native rendering path

`native_abba_renderer.py` materializes the 588 required Waxholm planes only in
the builder-local temporary directory. It rewrites all 998 historical loader
entries to local Bio-Formats TIFF openers and binds `sources.json` viewsetups
197–784 explicitly to source IDs 0–587 / Waxholm AP 189–776. `sources.json`,
`state.json`, every BDV view registration, and every ABBA action are otherwise
preserved. The rewritten archive contains no historical QuPath path.

ABBA loads that portable archive through its vendored `ImportStdZipStateCommand`.
The renderer requires 588 Java slices, calls the vendored
`ExportResampledSlicesToBDVSourceCommand` at an explicit 40-µm isotropic grid,
and discovers its output by the Java `SourceAndConverter` type rather than by a
guessed command-output name. Only that native BDV result can set
`native_backend_verified: true`; it is installed with visual parity `pending`
and remains non-release-eligible until manual acceptance.

### Audited native-state and grid handling

The `.abba` ZIP is loaded with the vendored `ImportStdZipStateCommand`, not the
JSON-only `ABBAStateLoadCommand`. The returned Java `MultiSlicePositioner` must
contain exactly 588 slices before export. The complete Maven coordinate list is
kept identical to vendored `get_java_dependencies()`, including its N5 modules.

The native BDV output transform must be axis-aligned at exactly 0.04 mm and its
translation must lie on the 40-µm target grid. The renderer uses that Java
source transform to crop/place the native raster on the explicit zero-origin
608×286×409 AP/SI/LR grid; it does not infer placement from array shape. The
fixed runtime atlas view exposes the already validated AP/SI/LR arrays to ABBA
as `asr` without permuting or modifying the installed atlas data or metadata.

### Python bridge compatibility

PyImageJ 1.5.0 and ScyJava 1.10.2 use the legacy `from jgo import jgo`
interface. Therefore `jgo==1.0.6` is pinned explicitly; jgo 3.x is not API
compatible with this bridge. Runtime preflight verifies all four distribution
versions and the `jgo` export before importing PyImageJ, so a transitive upgrade
is reported as `PYIMAGEJ_VERSION` or `PYIMAGEJ_API` rather than an opaque import
failure.
