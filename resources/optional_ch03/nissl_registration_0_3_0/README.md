# Nissl registration package for 0.3.0

Add exactly these required files to this directory before running the builder:

- `final_for_V_0_3.abba`

Keep the tracked `registration_manifest.json` unchanged for the validation run.
Optional provenance files may also be added:

- The old registered TIFF/BDV export is comparison-only and is deliberately
  not shipped or accepted as an input by the v0.3.1 builder.
- `project.qpproj`
- `project.qpproj.backup`

The builder does not read any path outside this package and does not use
`G:\nissl_registration`.
