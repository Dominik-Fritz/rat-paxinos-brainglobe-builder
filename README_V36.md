# V36 env + orientation stable

V36 fixes the immediate Python-environment problem.

The previous V34 script failed because the normal system Python had nibabel installed, but ABBA uses:

```text
C:\Users\49152\abba-python-0.11.0\python.exe
```

V36 adds:

```text
src\v36_repair_abba_python_env.py
RUN_V36_ENV_AND_ORIENTATION_DIAG.bat
```

Run:

```text
RUN_V36_ENV_AND_ORIENTATION_DIAG.bat
```

It will:

1. install/check nibabel, numpy, matplotlib, tifffile, pandas into ABBA Python
2. verify imports
3. generate V34 orientation previews
4. run a BrainGlobe atlas load check

Then open:

```text
reports\v34_orientation_previews\index.html
```

and report the candidate that looks most plausible.
