# V34/V35 Orientation Tools

## Why this exists

The atlas now loads in ABBA, but the display still looks wrong. That means the remaining problem is likely array orientation, axis ordering, or imperfect reference-space alignment.

## V34

Run:

```text
RUN_V34_ORIENTATION_PREVIEWS.bat
```

Then open:

```text
reports/v34_orientation_previews/index.html
```

Pick the candidate where the views look most anatomically plausible.

## V35

First dry-run:

```text
RUN_V35_AXIS_DRY_RUN.bat
```

Then apply one candidate:

```text
RUN_V35_AXIS_APPLY.bat
```

This modifies the installed atlas and creates a backup folder.

## Important

This is not a nonlinear registration. It is an axis/flip diagnostic and correction tool. If no candidate looks anatomically good, the next step is actual registration, not another ABBA patch.
