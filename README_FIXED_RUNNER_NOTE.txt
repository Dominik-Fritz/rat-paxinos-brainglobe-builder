V32.14 FIXED runner note
========================

This package fixes the Windows self-copy failure that occurs when the ZIP is extracted directly into G:\rat-paxinos-brainglobe-builder.

If the V32.14 script already exists at:
G:\rat-paxinos-brainglobe-builder\src\experiments\v32_14_annotation_tiff_display_proxy.py

the runner now skips copying or continues safely instead of stopping with:
ERROR: Could not copy V32.14 script into project.

Recommended runner:
RUN_V32_14_CACHE_ANNOTATION_TIFF_BORDER_ONLY.bat
