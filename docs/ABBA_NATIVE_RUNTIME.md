# Builder-local native ABBA runtime

The authoritative registration is `final_for_V_0_3.abba` (SHA-256 `e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06`). The vendored files are package contents directly under `vendor/abba_python_0_11_0`; they are provenance/reference source, not a reason to search user profiles for ABBA.

ABBA 0.11.0 initializes PyImageJ with the Maven coordinates recorded in `Abba.get_java_dependencies()`. It constructs BrainGlobe fixed atlases through `AbbaAtlas`/`AbbaMap`, imports moving sources with `ImportSliceFromSourcesCommand`, loads states using `ABBAStateLoadCommand`, and exposes registered data using `ExportResampledSlicesToBDVSourceCommand`. These names come from the vendored source.

Runtime data belong only under `data/native_abba_runtime` (Java, JGO, Maven, ImageJ, downloads, and temporary files); reports belong under `reports/native_abba`. No global Fiji, ABBA, Maven/JGO cache, or historical QuPath path is allowed. The old TIFF stack is comparison-only and is never a fallback. The Python TPS implementation is diagnostic-only and cannot install Ch03.

The uploaded package contains no license or distribution metadata for the vendored Python files. They are retained as uploaded provenance; redistribution rights must not be inferred. Upstream license/provenance must be added before distributing those files outside this repository.

A native test installation records `visual_parity_status: pending` and `release_eligible: false`. Only a separately confirmed visual comparison may change these to `passed` and `true`.
