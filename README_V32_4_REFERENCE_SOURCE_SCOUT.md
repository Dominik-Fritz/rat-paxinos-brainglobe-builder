# V32.4 Reference Source Scout

This package starts the next step after V32.2/V32.3:

- keep the validated Paxinos orientation
- do **not** depend on ABBA as a data source
- discover local anatomical reference candidates from raw data and BrainGlobe cache folders
- write reports only
- do not modify or install any atlas

## Run

Extract this ZIP and run:

```bat
RUN_V32_4_REFERENCE_SOURCE_SCOUT.bat
```

The BAT prints progress, opens the report folder, and opens the summary in Notepad.

## Outputs

```text
reports/v32_4_reference_source_scout/v32_4_reference_source_scout_summary.txt
reports/v32_4_reference_source_scout/candidate_reference_sources.csv
reports/v32_4_reference_source_scout/local_brainglobe_atlases.csv
reports/v32_4_reference_source_scout/v32_4_reference_source_scout.json
```

Upload those files for the next step.

## Why this exists

Using an ABBA-installed atlas as the required upstream source would make the pipeline brittle. The scout reads public/raw files and BrainGlobe cache folders if present, but does not require ABBA.

The next real reference solution should use a real anatomical source plus registration/QC, not direct affine resampling dressed up as progress.
