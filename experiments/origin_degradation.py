#!/usr/bin/env python3
"""How much does each mutation destroy the signal an origin classifier uses?

This is the experiment the tool exists to serve. It is deliberately a *miniature*
of camera-origin classification rather than the real thing, because no
camera-origin corpus is available here -- and saying so plainly is better than
implying otherwise.

  provenance A   images written through one pipeline (JPEG q92, 4:4:4)
  provenance B   the same images through another (JPEG q78, 4:2:0)

Both classes hold the *same scene content*, so a classifier separating them can
only be using processing traces -- quantisation artefacts, chroma subsampling,
noise residual statistics. That is exactly the kind of signal camera-origin work
relies on, which makes "does this mutation erase it?" the right question.

Features are the classic forensic ones rather than a CNN: they are interpretable,
run on CPU in seconds, and make the result about the mutations rather than about
a particular architecture.

    experiments/origin_degradation.py --images 120
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')


def scenes(n, side=192, seed=11):
    """Varied synthetic scenes. Same content pool for both provenances."""
    rng = np.random.RandomState(seed)
    out = []
    for i in range(n):
        a = np.zeros((side, side, 3), np.float64)
        for _ in range(rng.randint(3, 7)):
            cx, cy = rng.randint(0, side, 2)
            r = rng.randint(20, 70)
            y, x = np.mgrid[0:side, 0:side]
            m = ((x - cx) ** 2 + (y - cy) ** 2) < r * r
            a[m] = rng.randint(30, 226, 3)
        a += rng.normal(0, 6, a.shape)                    # sensor-like noise
        g = np.linspace(0, 40, side)
        a += g[None, :, None]
        out.append(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    return out


def write_provenance(img, path, which):
    if which == 'A':
        img.save(path, 'JPEG', quality=92, subsampling=0)      # 4:4:4
    else:
        img.save(path, 'JPEG', quality=78, subsampling=2)      # 4:2:0


def features(path_or_bytes):
    """Forensic features: noise-residual and block-boundary statistics."""
    im = (Image.open(path_or_bytes) if not isinstance(path_or_bytes, bytes)
          else Image.open(io.BytesIO(path_or_bytes)))
    a = np.asarray(im.convert('L'), np.float64)
    if a.shape[0] < 16 or a.shape[1] < 16:
        a = np.pad(a, ((0, 16), (0, 16)), mode='edge')

    # high-pass residual: what survives after a light blur is mostly processing trace
    k = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float64) / 16.0
    sm = np.zeros_like(a)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            sm += k[dy + 1, dx + 1] * np.roll(np.roll(a, dy, 0), dx, 1)
    res = a - sm

    f = [res.std(), np.abs(res).mean(), float(np.mean(res ** 4) / (res.var() ** 2 + 1e-9))]

    # 8x8 block-boundary energy: JPEG leaves a grid, quality changes its strength
    dh = np.abs(np.diff(a, axis=1))
    dv = np.abs(np.diff(a, axis=0))
    bh = dh[:, 7::8].mean() if dh.shape[1] > 8 else 0.0
    bv = dv[7::8, :].mean() if dv.shape[0] > 8 else 0.0
    f += [bh, bv, bh / (dh.mean() + 1e-9), bv / (dv.mean() + 1e-9)]

    # chroma behaviour separates 4:4:4 from 4:2:0
    c = np.asarray(im.convert('YCbCr'), np.float64)
    if c.ndim == 3 and c.shape[2] == 3:
        f += [c[..., 1].std(), c[..., 2].std(),
              np.abs(np.diff(c[..., 1], axis=1)).mean()]
    else:
        f += [0.0, 0.0, 0.0]
    return np.array(f, np.float64)


def mutate(path, mutation, params):
    body = subprocess.run(
        ['curl', '-s', '-m', '120', '-X', 'POST', BASE + '/api/mutate',
         '-F', 'image=@' + path, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    try:
        url = json.loads(body)['result_url']
    except Exception:
        return None
    return subprocess.run(['curl', '-s', url], capture_output=True).stdout


def is_identity(path, mutation, params):
    """Did this mutation actually change any pixel?

    Two results in an earlier run were artifacts of no-op parameters rather
    than findings, so no row is reported now without proving the operator
    moved the image. Compared in pixel space, not bytes: a re-encode changes
    the file while leaving the image identical.
    """
    raw = mutate(path, mutation, params)
    if raw is None:
        return False
    try:
        before = np.asarray(Image.open(path).convert('RGB'), np.int16)
        after = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'), np.int16)
    except Exception:
        return False
    if before.shape != after.shape:
        return False
    return float(np.abs(before - after).mean()) == 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--images', type=int, default=120)
    ap.add_argument('--out', default=os.path.join(HERE, 'results'))
    a = ap.parse_args()

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split

    d = tempfile.mkdtemp(prefix='imt-origin-')
    print(f'  building {a.images} scenes in two provenances')
    imgs = scenes(a.images)
    paths, y = [], []
    for i, im in enumerate(imgs):
        for cls, lab in (('A', 0), ('B', 1)):
            p = os.path.join(d, f'{i:04d}_{cls}.jpg')
            write_provenance(im, p, cls)
            paths.append(p); y.append(lab)
    y = np.array(y)
    X = np.array([features(p) for p in paths])

    Xtr, Xte, ytr, yte, _, pte = train_test_split(
        X, y, paths, test_size=0.4, random_state=7, stratify=y)
    sc = StandardScaler().fit(Xtr)
    clf = LogisticRegression(max_iter=2000).fit(sc.transform(Xtr), ytr)
    base = clf.score(sc.transform(Xte), yte)
    print(f'  provenance classifier trained on {len(ytr)} images, tested on {len(yte)}')
    print(f'  baseline accuracy on unmutated images: {base*100:.1f}%\n')
    if base < 0.75:
        print('  baseline too weak to measure degradation against -- stopping'); return 1

    cat = json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                    capture_output=True, text=True).stdout)
    FAMILIES = {
        'compression / palette': ['colors', 'posterize', 'ordered_dither'],
        'blur': ['blur', 'gaussian_blur', 'motion_blur', 'selective_blur', 'adaptive_blur'],
        'sharpen': ['sharpen', 'unsharp', 'adaptive_sharpen'],
        'geometry': ['rotate', 'resize', 'shear', 'roll', 'transpose'],
        'tone / contrast': ['gamma', 'equalize', 'auto_level', 'sigmoidal_contrast',
                            'brightness_contrast', 'modulate'],
        'colour space': ['grayscale', 'colorspace', 'profile'],
        'noise / denoise': ['despeckle', 'enhance', 'wavelet_denoise', 'kuwahara', 'median'],
        'stylise': ['emboss', 'charcoal', 'paint', 'solarize', 'sepia_tone', 'vignette'],
    }
    rows = []
    skipped = []
    for fam, muts in FAMILIES.items():
        for m in muts:
            spec = cat['continuous'].get(m) or cat['discrete'].get(m)
            if not spec:
                continue
            # Not the UI default: several defaults are deliberately gentle and
            # some are outright no-ops -- median's kernel is 1 (a 1x1 median is
            # the identity) and colors' is 256 (no reduction on 8-bit input).
            # Using them made two filters look like they preserved the signal
            # when in fact they had not touched the image.
            #
            # A plain midpoint is not the fix either: for a symmetric range like
            # shear's [-45, 45] the midpoint is 0, which is again the identity.
            # Take a value 75% along the range instead, which is off the identity
            # point for both symmetric and one-sided ranges, and record it.
            params = {}
            for k, v in (spec.get('parameters') or {}).items():
                if v.get('type') in ('int', 'float') and 'min' in v and 'max' in v:
                    lo, hi = float(v['min']), float(v['max'])
                    val = lo + 0.75 * (hi - lo)
                    params[k] = int(round(val)) if v['type'] == 'int' else round(val, 2)
                elif 'default' in v:
                    params[k] = v['default']
            if is_identity(pte[0], m, params):
                print(f'  {fam:22} {m:18} {str(params)[:28]:28}   SKIPPED - no-op at '
                      f'these parameters, it changed no pixel')
                skipped.append((fam, m, params))
                continue
            feats, kept = [], []
            for p, lab in zip(pte, yte):
                raw = mutate(p, m, params)
                if raw is None:
                    continue
                try:
                    feats.append(features(raw)); kept.append(lab)
                except Exception:
                    continue
            if len(kept) < 20:
                continue
            acc = clf.score(sc.transform(np.array(feats)), np.array(kept))
            rows.append((fam, m, acc, base - acc, params))
            pstr = ','.join(f'{k}={v}' for k, v in params.items())[:26]
            print(f'  {fam:22} {m:18} {pstr:28} {acc*100:5.1f}%  drop {(base-acc)*100:+5.1f}')

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'origin_degradation.json'), 'w') as fh:
        json.dump({'baseline': base,
                   'rows': [{'family': f, 'mutation': m, 'accuracy': ac,
                             'drop': dr, 'params': pr}
                            for f, m, ac, dr, pr in rows],
                   'skipped_as_noop': [{'family': f, 'mutation': m, 'params': pr}
                                       for f, m, pr in skipped]}, fh, indent=2)
    print(f'\n  {len(rows)} mutations measured, {len(skipped)} skipped as no-ops, wrote results/origin_degradation.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
