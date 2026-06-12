# V32.12 LabelAtlas Rebaseline Cleanup

Purpose: remove/quarantine all experimental Paxinos test atlases from the local BrainGlobe cache and project output folders, then restore the single main Paxinos atlas as the active LabelAtlas/label-only baseline.

This package is meant for the current rat Paxinos BrainGlobe/ABBA project after stopping the Waxholm/SIGMA/NeuroRat MRI reference-channel experiments.

## What it does

- Keeps only the main atlas available in BrainGlobe:
  - `paxinos_watson_rat_40um`
- Moves experimental/test atlases out of active BrainGlobe availability:
  - `paxinos_watson_rat_40um_*_test_v1.0`
  - `paxinos_watson_rat_40um_null_reference_debug_v1.0`
  - `paxinos_watson_rat_40um_sigma_reference_test_v1.0`
  - old orientation/debug test atlases with the same prefix
- Removes test atlas entries from `C:\Users\<you>\.brainglobe\last_versions.conf`.
- Restores the main cache folder from:
  - `G:\rat-paxinos-brainglobe-builder\data\output\brainglobe_official_candidate\paxinos_watson_rat_40um`
- Writes reports to:
  - `G:\rat-paxinos-brainglobe-builder\reports\v32_12_label_atlas_rebaseline_cleanup`

## What it does not do

- It does not delete raw atlas data.
- It does not delete project reports.
- It does not promote any Waxholm/SIGMA/NeuroRat reference.
- It does not create a new atlas.
- It does not modify the stable source candidate except copying it into the BrainGlobe cache as the main atlas.

## How to use

Recommended:

1. Close Fiji/ABBA completely.
2. Run:
   - `RUN_V32_12_LABEL_ATLAS_REBASELINE_CLEANUP.bat`
3. Restart Fiji/ABBA.
4. Only this atlas should remain available from the Paxinos line:
   - `paxinos_watson_rat_40um`

Optional safety check first:

- Run `RUN_V32_12_LABEL_ATLAS_REBASELINE_CLEANUP_DRY_RUN.bat`.

## Backup behavior

The script moves removed test atlases into timestamped quarantine folders, instead of permanently deleting them. This keeps the cleanup reversible while removing them from normal BrainGlobe/ABBA availability.

Typical backup locations:

- `C:\Users\<you>\.brainglobe\_paxinos_v32_12_removed_test_atlases_<timestamp>`
- `G:\rat-paxinos-brainglobe-builder\data\output\_v32_12_removed_test_atlases_<timestamp>`
- `C:\Users\<you>\.brainglobe\_paxinos_v32_12_main_cache_backup_<timestamp>`

## Intended project state after cleanup

Main line:

- `paxinos_watson_rat_40um`
- LabelAtlas / label-only Paxinos solution
- orientation: `PIL`
- shape: `[608, 286, 409]`
- no active Waxholm/SIGMA/NeuroRat reference-channel test atlases

Experimental branch status:

- V32.4 to V32.11 reference-channel work is diagnostic only.
- Additional reference channels or MRI backgrounds are postponed to the end of the project.
