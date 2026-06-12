from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
import tifffile

PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw" / "bluebrainheadmodels"
REPORTS = PROJECT / "reports"
INSTALLED = Path.home() / ".brainglobe" / "paxinos_watson_rat_40um_v1.0"
OUT = REPORTS / "v34_orientation_previews"

CANDIDATES = [
    ("identity_xyz", (0, 1, 2), (False, False, False)),
    ("swap_xz_zyx", (2, 1, 0), (False, False, False)),
    ("swap_xy_yxz", (1, 0, 2), (False, False, False)),
    ("swap_yz_xzy", (0, 2, 1), (False, False, False)),
    ("identity_flip_z", (0, 1, 2), (False, False, True)),
    ("identity_flip_y", (0, 1, 2), (False, True, False)),
    ("identity_flip_x", (0, 1, 2), (True, False, False)),
    ("swap_xz_flip_z", (2, 1, 0), (False, False, True)),
    ("swap_xz_flip_x", (2, 1, 0), (True, False, False)),
    ("swap_yz_flip_z", (0, 2, 1), (False, False, True)),
]

def normalize_slice(s: np.ndarray) -> np.ndarray:
    arr = np.asarray(s, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros_like(arr)
    lo, hi = np.percentile(finite, [1, 99.5])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros_like(arr)
    return np.clip((arr - lo) / (hi - lo), 0, 1)

def transform(arr: np.ndarray, perm, flips) -> np.ndarray:
    out = np.transpose(arr, perm)
    for ax, do_flip in enumerate(flips):
        if do_flip:
            out = np.flip(out, axis=ax)
    return out

def mid_slices(arr: np.ndarray) -> list[np.ndarray]:
    x, y, z = arr.shape
    return [arr[x // 2, :, :], arr[:, y // 2, :], arr[:, :, z // 2]]

def save_candidate(name: str, ref: np.ndarray, ann: np.ndarray, perm, flips) -> dict:
    ref_t = transform(ref, perm, flips)
    ann_t = transform(ann, perm, flips)

    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    labels = ["axis0 mid", "axis1 mid", "axis2 mid"]

    for i, sl in enumerate(mid_slices(ref_t)):
        axes[0, i].imshow(np.rot90(normalize_slice(sl)), cmap="gray")
        axes[0, i].set_title("Reference " + labels[i])
        axes[0, i].axis("off")

    for i, sl in enumerate(mid_slices(ann_t)):
        axes[1, i].imshow(np.rot90(sl > 0), cmap="gray")
        axes[1, i].set_title("Annotation mask " + labels[i])
        axes[1, i].axis("off")

    fig.suptitle(f"{name} | perm={perm} flips={flips}", fontsize=12)
    fig.tight_layout()
    png = OUT / f"{name}.png"
    fig.savefig(png, dpi=140)
    plt.close(fig)

    return {
        "name": name,
        "perm": list(perm),
        "flips": list(flips),
        "reference_shape_after": list(ref_t.shape),
        "annotation_shape_after": list(ann_t.shape),
        "png": str(png),
    }

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    ref_path = INSTALLED / "reference.tiff"
    ann_path = INSTALLED / "annotation.tiff"

    if not ref_path.exists():
        raise FileNotFoundError(ref_path)
    if not ann_path.exists():
        raise FileNotFoundError(ann_path)

    ref = tifffile.imread(str(ref_path))
    ann = tifffile.imread(str(ann_path))

    raw_info = {}
    for filename in ["Paxinos_Watson_Atlas.nii.gz", "NeuroRat_MRI.nii.gz", "Waxholm_Atlas_MRI.nii.gz"]:
        p = RAW / filename
        if p.exists():
            img = nib.load(str(p))
            raw_info[filename] = {
                "shape": list(img.shape),
                "orientation": "".join(nib.aff2axcodes(img.affine)),
                "zooms": [float(x) for x in img.header.get_zooms()[:3]],
                "affine": img.affine.tolist(),
            }

    results = [save_candidate(name, ref, ann, perm, flips) for name, perm, flips in CANDIDATES]

    html = OUT / "index.html"
    html.write_text(
        "<html><body><h1>V34 orientation previews</h1>"
        "<p>Open this file after running V34. Pick the candidate where the views look most anatomically plausible. "
        "This checks axis order/flip, not nonlinear registration.</p>"
        + "".join(
            f"<h2>{r['name']}</h2><p>perm={r['perm']} flips={r['flips']}</p>"
            f"<img src='{Path(r['png']).name}' style='width:95%;border:1px solid #999;'>"
            for r in results
        )
        + "</body></html>",
        encoding="utf-8",
    )

    report = {
        "installed_reference_shape": list(ref.shape),
        "installed_annotation_shape": list(ann.shape),
        "raw_info": raw_info,
        "candidates": results,
        "html": str(html),
    }

    (REPORTS / "v34_orientation_preview_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "V34 orientation preview report",
        "=" * 72,
        f"Installed reference shape: {ref.shape}",
        f"Installed annotation shape: {ann.shape}",
        "",
        "Raw NIfTI info:",
    ]
    for k, v in raw_info.items():
        lines.append(f"- {k}: shape={v['shape']} orientation={v['orientation']} zooms={v['zooms']}")
    lines += ["", f"HTML preview: {html}", "", "Candidates:"]
    for r in results:
        lines.append(f"- {r['name']}: perm={r['perm']} flips={r['flips']} png={r['png']}")
    (REPORTS / "v34_orientation_preview_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print("V34 orientation previews written:")
    print(html)
    print("Open this file in browser.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
