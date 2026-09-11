# Nissl registration package

This directory ships complete. Nothing has to be added before a build:

- `final_for_V_0_3.abba` — the authoritative registration (SHA-256
  `e038741ac9825c35e62c1e88658c3533a5e4da3460ebc9644275c4b6e48e7f06`), tracked
  in the repository at 476 KB;
- `registration_manifest.json` — source range, AP direction, sequence offset and
  edge policy. Keep it unchanged.

The moving images the registration was created against live one level up in
`whs_nissl_slices_paxinos_40um_ap/`, hash-verified per plane on every build. The
builder therefore needs neither a release asset nor a BrainGlobe Waxholm
download; the Waxholm identifiers in the manifest are provenance only.

Deliberately not shipped:

- the old registered TIFF/BDV export — comparison-only, never accepted as a
  build input;
- `project.qpproj` and its backup — the historical QuPath project. The builder
  never follows the `G:\nissl_registration` path recorded inside the saved
  state; it rebinds every source to the pinned planes before ABBA opens them.
