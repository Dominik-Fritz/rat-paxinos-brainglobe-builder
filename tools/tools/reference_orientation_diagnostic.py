from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Dict, Tuple, List

import numpy as np

try:
    import nibabel as nib
except Exception as e:
    print('ERROR: nibabel is required:', e)
    sys.exit(1)

try:
    import tifffile
except Exception as e:
    print('ERROR: tifffile is required:', e)
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except Exception as e:
    print('ERROR: matplotlib is required:', e)
    sys.exit(1)

try:
    from scipy.ndimage import zoom
except Exception:
    zoom = None

PROJECT_ROOT = Path(r'G:\rat-paxinos-brainglobe-builder')
RAW_DIR = PROJECT_ROOT / 'data' / 'raw' / 'bluebrainheadmodels'
INSTALLED_ATLAS = Path(r'C:\Users\49152\.brainglobe\paxinos_watson_rat_40um_v1.0')
OUT_DIR = PROJECT_ROOT / 'reports' / 'reference_orientation_diagnostic'

CANDIDATES = {
    'paxinos_raw_annotation': RAW_DIR / 'Paxinos_Watson_Atlas.nii.gz',
    'neurorat_mri': RAW_DIR / 'NeuroRat_MRI.nii.gz',
    'sigma_anatomical': RAW_DIR / 'SIGMA_Anatomical_Brain_Atlas.nii',
    'waxholm_mri': RAW_DIR / 'Waxholm_Atlas_MRI.nii.gz',
}

INSTALLED = {
    'installed_reference_tiff': INSTALLED_ATLAS / 'reference.tiff',
    'installed_annotation_tiff': INSTALLED_ATLAS / 'annotation.tiff',
    'installed_hemispheres_tiff': INSTALLED_ATLAS / 'hemispheres.tiff',
    'metadata_json': INSTALLED_ATLAS / 'metadata.json',
    'structures_json': INSTALLED_ATLAS / 'structures.json',
}


def ensure_out():
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def finite_stats(arr: np.ndarray) -> Dict[str, object]:
    arr = np.asarray(arr)
    finite = np.isfinite(arr)
    if not finite.any():
        return {'shape': tuple(arr.shape), 'dtype': str(arr.dtype), 'finite': False}
    vals = arr[finite]
    nz = np.count_nonzero(vals)
    return {
        'shape': tuple(int(x) for x in arr.shape),
        'dtype': str(arr.dtype),
        'min': float(np.min(vals)),
        'max': float(np.max(vals)),
        'mean': float(np.mean(vals)),
        'nonzero_voxels': int(nz),
        'nonzero_fraction': float(nz / vals.size),
    }


def nifti_summary(path: Path) -> Dict[str, object]:
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj)
    return {
        'path': str(path),
        'shape': tuple(int(x) for x in img.shape),
        'dtype': str(data.dtype),
        'zooms': tuple(float(x) for x in img.header.get_zooms()[:3]),
        'affine': img.affine.tolist(),
        'orientation_codes': ''.join(nib.aff2axcodes(img.affine)),
        'stats': finite_stats(data),
    }


def tiff_summary(path: Path) -> Dict[str, object]:
    arr = tifffile.imread(str(path))
    return {'path': str(path), 'stats': finite_stats(arr)}


def world_bbox(img: nib.Nifti1Image) -> Tuple[np.ndarray, np.ndarray]:
    shape = img.shape[:3]
    corners = np.array([
        [0, 0, 0],
        [shape[0]-1, 0, 0],
        [0, shape[1]-1, 0],
        [0, 0, shape[2]-1],
        [shape[0]-1, shape[1]-1, 0],
        [shape[0]-1, 0, shape[2]-1],
        [0, shape[1]-1, shape[2]-1],
        [shape[0]-1, shape[1]-1, shape[2]-1],
    ], dtype=float)
    homo = np.c_[corners, np.ones(len(corners))]
    world = (img.affine @ homo.T).T[:, :3]
    return world.min(axis=0), world.max(axis=0)


def bbox_overlap(a_min, a_max, b_min, b_max):
    lo = np.maximum(a_min, b_min)
    hi = np.minimum(a_max, b_max)
    size = np.maximum(0, hi - lo)
    vol = float(np.prod(size))
    avol = float(np.prod(np.maximum(0, a_max - a_min)))
    bvol = float(np.prod(np.maximum(0, b_max - b_min)))
    frac_a = vol / avol if avol else 0.0
    frac_b = vol / bvol if bvol else 0.0
    return size, vol, frac_a, frac_b


def norm2d(img: np.ndarray) -> np.ndarray:
    x = np.asarray(img, dtype=float)
    finite = np.isfinite(x)
    if not finite.any():
        return np.zeros_like(x, dtype=float)
    vals = x[finite]
    lo, hi = np.percentile(vals, [1, 99])
    if hi <= lo:
        hi = vals.max()
        lo = vals.min()
    if hi <= lo:
        return np.zeros_like(x, dtype=float)
    x = np.clip((x - lo) / (hi - lo), 0, 1)
    return x


def middle_nonzero_index(arr: np.ndarray, axis: int) -> int:
    mask = arr != 0
    counts = mask.sum(axis=tuple(i for i in range(arr.ndim) if i != axis))
    nz = np.where(counts > 0)[0]
    if len(nz) == 0:
        return arr.shape[axis] // 2
    return int(nz[len(nz)//2])


def slice_at(arr: np.ndarray, axis: int, idx: int) -> np.ndarray:
    if axis == 0:
        return arr[idx, :, :]
    if axis == 1:
        return arr[:, idx, :]
    return arr[:, :, idx]


def save_axis_previews(name: str, arr: np.ndarray, out_path: Path, rotate: bool = False):
    fig = plt.figure(figsize=(15, 5))
    for i, axis in enumerate([0, 1, 2], 1):
        idx = middle_nonzero_index(arr, axis)
        sl = slice_at(arr, axis, idx)
        if rotate:
            sl = np.rot90(sl)
        ax = fig.add_subplot(1, 3, i)
        ax.imshow(norm2d(sl), cmap='gray')
        ax.set_title(f'{name}\naxis {axis}, index {idx}, shape {sl.shape}')
        ax.axis('off')
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def save_candidate_panel(name: str, arr: np.ndarray, out_path: Path):
    fig = plt.figure(figsize=(15, 10))
    positions = []
    for axis in [0, 1, 2]:
        mask = arr != 0
        counts = mask.sum(axis=tuple(i for i in range(arr.ndim) if i != axis))
        nz = np.where(counts > 0)[0]
        if len(nz) == 0:
            idxs = [arr.shape[axis]//4, arr.shape[axis]//2, 3*arr.shape[axis]//4]
        else:
            idxs = [int(nz[len(nz)//4]), int(nz[len(nz)//2]), int(nz[3*len(nz)//4])]
        for idx in idxs:
            positions.append((axis, idx))
    for i, (axis, idx) in enumerate(positions, 1):
        sl = slice_at(arr, axis, idx)
        ax = fig.add_subplot(3, 3, i)
        ax.imshow(norm2d(sl), cmap='gray')
        ax.set_title(f'axis {axis}, index {idx}, shape {sl.shape}')
        ax.axis('off')
    fig.suptitle(name)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    ensure_out()
    report_lines: List[str] = []
    report_lines.append('REFERENCE / ORIENTATION DIAGNOSTIC')
    report_lines.append('=' * 80)
    report_lines.append(f'PROJECT_ROOT: {PROJECT_ROOT}')
    report_lines.append(f'RAW_DIR: {RAW_DIR}')
    report_lines.append(f'INSTALLED_ATLAS: {INSTALLED_ATLAS}')
    report_lines.append(f'OUT_DIR: {OUT_DIR}')
    report_lines.append('')

    report_lines.append('FILE EXISTENCE')
    for k, p in {**CANDIDATES, **INSTALLED}.items():
        report_lines.append(f'{k}: {p} | exists={p.exists()} | size={p.stat().st_size if p.exists() else "MISSING"}')
    report_lines.append('')

    if INSTALLED['metadata_json'].exists():
        meta = json.loads(INSTALLED['metadata_json'].read_text(encoding='utf-8'))
        (OUT_DIR / 'metadata_copy.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        report_lines.append('METADATA KEY FIELDS')
        for key in ['name','shape','reference_shape','annotation_shape','resolution','orientation','reference_strategy','additional_references','root_id','version','status','warning']:
            report_lines.append(f'{key}: {meta.get(key)}')
        report_lines.append('')

    report_lines.append('TIFF SUMMARIES')
    for k in ['installed_reference_tiff', 'installed_annotation_tiff', 'installed_hemispheres_tiff']:
        p = INSTALLED[k]
        if p.exists():
            s = tiff_summary(p)
            report_lines.append(json.dumps({k: s}, indent=2))
    report_lines.append('')

    nifti_infos = {}
    report_lines.append('NIFTI SUMMARIES')
    for k, p in CANDIDATES.items():
        if p.exists():
            try:
                s = nifti_summary(p)
                nifti_infos[k] = s
                report_lines.append(json.dumps({k: s}, indent=2))
            except Exception as e:
                report_lines.append(f'{k}: ERROR {e}')
    report_lines.append('')

    report_lines.append('WORLD BOUNDING BOX OVERLAP AGAINST PAXINOS RAW')
    if CANDIDATES['paxinos_raw_annotation'].exists():
        target = nib.load(str(CANDIDATES['paxinos_raw_annotation']))
        tmin, tmax = world_bbox(target)
        report_lines.append(f'paxinos bbox min={tmin.tolist()} max={tmax.tolist()}')
        for k in ['neurorat_mri','sigma_anatomical','waxholm_mri']:
            p = CANDIDATES[k]
            if p.exists():
                try:
                    img = nib.load(str(p))
                    smin, smax = world_bbox(img)
                    size, vol, fa, fb = bbox_overlap(tmin, tmax, smin, smax)
                    report_lines.append(f'{k}: bbox min={smin.tolist()} max={smax.tolist()}')
                    report_lines.append(f'{k}: overlap_size_mm={size.tolist()} overlap_volume_mm3={vol:.4f} frac_of_paxinos={fa:.6f} frac_of_candidate={fb:.6f}')
                except Exception as e:
                    report_lines.append(f'{k}: bbox ERROR {e}')
    report_lines.append('')

    # previews
    try:
        ref = tifffile.imread(str(INSTALLED['installed_reference_tiff']))
        ann = tifffile.imread(str(INSTALLED['installed_annotation_tiff']))
        save_axis_previews('installed_reference_tiff', ref, OUT_DIR / 'axis_previews_installed_reference.png')
        save_axis_previews('installed_annotation_tiff', ann, OUT_DIR / 'axis_previews_installed_annotation.png')
        save_axis_previews('installed_annotation_tiff_ROT90_preview', ann, OUT_DIR / 'axis_previews_installed_annotation_rot90.png', rotate=True)
        save_candidate_panel('installed_annotation_tiff_multi_slices', ann, OUT_DIR / 'candidate_panel_installed_annotation.png')
    except Exception as e:
        report_lines.append(f'Preview generation for installed TIFFs failed: {e}')

    for k, p in CANDIDATES.items():
        if p.exists():
            try:
                img = nib.load(str(p))
                data = np.asanyarray(img.dataobj)
                save_axis_previews(k, data, OUT_DIR / f'axis_previews_{k}.png')
                save_candidate_panel(k, data, OUT_DIR / f'candidate_panel_{k}.png')
            except Exception as e:
                report_lines.append(f'Preview generation for {k} failed: {e}')

    report_lines.append('PRELIMINARY INTERPRETATION')
    report_lines.append('- If reference_strategy is provisional_label_edge_reference_generated_from_annotation_boundaries, the reference is expected to be label-edge based, not anatomical MRI.')
    report_lines.append('- If ABBA Horizontal visually matches coronal anatomy, the atlas axes/orientation metadata or ABBA axis mapping is likely permuted relative to expectations.')
    report_lines.append('- If candidate MRI overlap fractions are zero or tiny, direct affine resampling to Paxinos will produce empty/all-zero output. Do not merge such a reference into the stable builder.')
    report_lines.append('- Use generated PNGs to identify which array axis corresponds to sagittal/coronal/horizontal anatomy before modifying metadata or export axis order.')

    (OUT_DIR / 'diagnostic_report.txt').write_text('\n'.join(report_lines), encoding='utf-8')
    print('\n'.join(report_lines))
    print('\nDONE')
    print(f'Output folder: {OUT_DIR}')
    print('Open diagnostic_report.txt and PNG previews in that folder.')


if __name__ == '__main__':
    main()
