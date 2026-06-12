from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile

PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
INSTALLED = Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"

VARIANTS = {
    "identity_xyz": ((0,1,2), (False,False,False)),
    "swap_xz_zyx": ((2,1,0), (False,False,False)),
    "swap_xy_yxz": ((1,0,2), (False,False,False)),
    "swap_yz_xzy": ((0,2,1), (False,False,False)),
    "identity_flip_z": ((0,1,2), (False,False,True)),
    "identity_flip_y": ((0,1,2), (False,True,False)),
    "identity_flip_x": ((0,1,2), (True,False,False)),
    "swap_xz_flip_z": ((2,1,0), (False,False,True)),
    "swap_xz_flip_x": ((2,1,0), (True,False,False)),
    "swap_yz_flip_z": ((0,2,1), (False,False,True)),
}

def transform(arr: np.ndarray, perm, flips) -> np.ndarray:
    out = np.transpose(arr, perm)
    for ax, do_flip in enumerate(flips):
        if do_flip:
            out = np.flip(out, axis=ax)
    return np.ascontiguousarray(out)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=sorted(VARIANTS), required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    REPORTS.mkdir(parents=True, exist_ok=True)

    perm, flips = VARIANTS[args.variant]

    ref_path = INSTALLED / "reference.tiff"
    ann_path = INSTALLED / "annotation.tiff"
    hemi_path = INSTALLED / "hemispheres.tiff"
    meta_path = INSTALLED / "metadata.json"

    ref = tifffile.imread(str(ref_path))
    ann = tifffile.imread(str(ann_path))
    hemi = tifffile.imread(str(hemi_path))

    ref_t = transform(ref, perm, flips)
    ann_t = transform(ann, perm, flips)
    hemi_t = transform(hemi, perm, flips)

    result = {
        "variant": args.variant,
        "perm": list(perm),
        "flips": list(flips),
        "apply": args.apply,
        "old_shape": list(ref.shape),
        "new_shape": list(ref_t.shape),
        "annotation_same_shape": list(ann_t.shape) == list(ref_t.shape),
        "hemispheres_same_shape": list(hemi_t.shape) == list(ref_t.shape),
        "warning": "Axis variant only. No anatomical registration.",
    }

    if args.apply:
        backup = INSTALLED / ("_v35_backup_" + args.variant)
        backup.mkdir(exist_ok=True)
        for p in [ref_path, ann_path, hemi_path, meta_path]:
            if p.exists():
                target = backup / p.name
                if not target.exists():
                    target.write_bytes(p.read_bytes())

        tifffile.imwrite(str(ref_path), ref_t.astype(ref.dtype), photometric="minisblack")
        tifffile.imwrite(str(ann_path), ann_t.astype(ann.dtype), photometric="minisblack")
        tifffile.imwrite(str(hemi_path), hemi_t.astype(hemi.dtype), photometric="minisblack")

        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["v35_axis_variant"] = {
                "variant": args.variant,
                "perm": list(perm),
                "flips": list(flips),
                "backup": str(backup),
                "warning": "Array axes were transposed/flipped for ABBA display testing, not validated registration.",
            }
            meta["shape"] = list(ref_t.shape)
            meta["reference_shape"] = list(ref_t.shape)
            meta["annotation_shape"] = list(ann_t.shape)
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        result["backup"] = str(backup)

    (REPORTS / "v35_axis_variant_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (REPORTS / "v35_axis_variant_report.txt").write_text(
        "\n".join([
            "V35 axis variant report",
            "=" * 72,
            f"Variant: {args.variant}",
            f"Perm: {perm}",
            f"Flips: {flips}",
            f"Apply: {args.apply}",
            f"Old shape: {result['old_shape']}",
            f"New shape: {result['new_shape']}",
            f"Annotation same shape: {result['annotation_same_shape']}",
            f"Hemispheres same shape: {result['hemispheres_same_shape']}",
            f"Backup: {result.get('backup', 'not applied')}",
        ]),
        encoding="utf-8",
    )

    print("V35 axis variant report written.")
    print("Variant:", args.variant)
    print("Apply:", args.apply)
    print("New shape:", result["new_shape"])
    if not args.apply:
        print("Dry run only. Add --apply to overwrite installed atlas.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
