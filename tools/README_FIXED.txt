FIXED Rat Paxinos reference/orientation diagnostic runner

This version fixes the earlier path problem.

Recommended use:
1. Extract the ZIP anywhere, for example Desktop.
2. Double-click RUN_REFERENCE_ORIENTATION_DIAGNOSTIC_FIXED.bat.

The BAT will automatically:
- check G:\rat-paxinos-brainglobe-builder
- check G:\rat-paxinos-brainglobe-builder\.venv\Scripts\python.exe
- create G:\rat-paxinos-brainglobe-builder\tools if needed
- copy tools\reference_orientation_diagnostic.py into that tools folder
- run the diagnostic
- open G:\rat-paxinos-brainglobe-builder\reports\reference_orientation_diagnostic

It does not modify the installed atlas or V32 builder files.
