from __future__ import annotations

from pathlib import Path
import json

import napari
import numpy as np
import tifffile

ATLAS_DIR = Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"

reference_path = ATLAS_DIR / "reference.tiff"
annotation_path = ATLAS_DIR / "annotation.tiff"
hemispheres_path = ATLAS_DIR / "hemispheres.tiff"
metadata_path = ATLAS_DIR / "metadata.json"

print("Atlas folder:", ATLAS_DIR)
for p in [reference_path, annotation_path, hemispheres_path, metadata_path]:
    print(f"{p.name} exists:", p.exists())

reference = tifffile.imread(str(reference_path))
annotation = tifffile.imread(str(annotation_path))

print("reference:", reference.shape, reference.dtype, int(reference.min()), int(reference.max()))
print("annotation:", annotation.shape, annotation.dtype, int(annotation.min()), int(annotation.max()))
print("annotation unique sample:", np.unique(annotation)[:30])

if metadata_path.exists():
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    print("metadata orientation:", metadata.get("orientation"))
    print("metadata shape:", metadata.get("shape"))
    print("reference strategy:", metadata.get("reference_strategy"))
    print("v32_2 orientation:", metadata.get("v32_2_validated_abba_orientation"))
    print("hemispheres_file:", metadata.get("hemispheres_file"))
    print("files keys:", sorted((metadata.get("files") or {}).keys()))

viewer = napari.Viewer()
viewer.add_image(reference, name="reference.tiff")
viewer.add_labels(annotation.astype(np.uint32), name="annotation.tiff")

if hemispheres_path.exists():
    hemispheres = tifffile.imread(str(hemispheres_path))
    print("hemispheres:", hemispheres.shape, hemispheres.dtype, np.unique(hemispheres), "nonzero_fraction", np.count_nonzero(hemispheres) / hemispheres.size)
    viewer.add_labels(hemispheres.astype(np.uint8), name="hemispheres.tiff")

napari.run()
