# Nissl registration package for 0.3.0

Add exactly these required files to this directory before running the builder:

- `registered_slices_ImageJ_stack.tif`
- `final_for_V_0_3.abba`

Keep the tracked `registration_manifest.json` unchanged for the validation run.
Optional provenance files may also be added:

- `final_for_V_0_3_bdv_view.json`
- `bdv_export_registered_slices_to_BDV_Json_Dataset`
- `project.qpproj`
- `project.qpproj.backup`

The builder does not read any path outside this package and does not use
`G:\nissl_registration`.
