# Nissl registration package for 0.3.0

Add exactly these required files to this directory before running the builder:

- `final_for_V_0_3.abba`

Keep the tracked `registration_manifest.json` unchanged for the validation run.
Optional provenance files may also be added:

- The old registered TIFF/BDV export is comparison-only and is deliberately
  not shipped or accepted as an input by the v0.3.1 builder.
- `project.qpproj`
- `project.qpproj.backup`

The builder never follows the historical `G:\nissl_registration` project path;
outside this package it reads only its normal generated atlas files and the
separately validated BrainGlobe Waxholm cache.

The independently versioned Waxholm image volume is downloaded automatically
through BrainGlobe AtlasAPI on the first build and subsequently read from the
validated BrainGlobe cache; it is intentionally not duplicated in this folder.
