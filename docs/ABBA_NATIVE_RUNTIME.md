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
