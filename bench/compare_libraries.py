#!/usr/bin/env python3
"""Compare this tool against the augmentation libraries a reviewer will name.

The question is not "which is faster". These libraries and this tool solve
different problems, and the comparison that matters for a provenance tool is:

  1. does the library's operator agree with ImageMagick's, pixel for pixel?
  2. is the output reproducible byte-for-byte across runs?
  3. what does it cost to install, and does it still install at all?

Run with the comparison virtualenv, which needs albumentations and torchvision:

    bench/compare_libraries.py --tool-base http://127.0.0.1:5000
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))


def reference(magick, src, args, out):
    """Ground truth: the ImageMagick command line."""
    subprocess.run([magick, src] + args + [out], capture_output=True)
    return np.array(Image.open(out).convert('RGB'), dtype=np.int16)


def from_tool(base, src, mutation, params):
    body = subprocess.run(
        ['curl', '-s', '-X', 'POST', base + '/api/mutate', '-F', 'image=@' + src,
         '-F', 'mutation=' + mutation, '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    url = json.loads(body)['result_url']
    raw = subprocess.run(['curl', '-s', url], capture_output=True).stdout
    return np.array(Image.open(io.BytesIO(raw)).convert('RGB'), dtype=np.int16), raw


def summarise(name, ref, got):
    if got is None:
        return (name, None, None, 'not offered')
    if got.shape != ref.shape:
        return (name, None, None, f'different shape {got.shape} vs {ref.shape}')
    diff = np.abs(ref - got)
    return (name, int(diff.max()), float(diff.mean()), '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tool-base', default=os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000'))
    a = ap.parse_args()

    magick = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
    if not os.path.exists(magick):
        print('  MAGICK_HOME is not set to a build with a magick binary'); return 2

    d = tempfile.mkdtemp(prefix='imt-cmp-')
    # The same source the differential oracle uses, so the two agree.
    src = os.path.join(HERE, '..', 'tests', 'oracle_source.png')
    if not os.path.exists(src):
        print('  tests/oracle_source.png is missing; run tests/run_all.sh once')
        return 2
    arr = np.array(Image.open(src).convert('RGB'))

    import albumentations as A
    import torch, torchvision.transforms.v2.functional as TF

    cases = []

    # ---- Gaussian blur, sigma 5 -------------------------------------------
    # The CLI form must be the one the tool implements, taken from
    # tests/oracle_differential.py -- '-blur 0x5' lets ImageMagick choose the
    # radius and is a different operator, which made an earlier run of this
    # harness report the tool as divergent when it was the harness that was wrong.
    ref = reference(magick, src, ['-blur', '2.0x5.0'], os.path.join(d, 'r1.png'))
    tool, _ = from_tool(a.tool_base, src, 'blur', {'sigma': 5})
    alb = A.GaussianBlur(blur_limit=(0, 0), sigma_limit=(5, 5), p=1.0)(image=arr)['image']
    tv = TF.gaussian_blur(torch.from_numpy(arr).permute(2, 0, 1), kernel_size=21, sigma=5.0)
    tv = tv.permute(1, 2, 0).numpy()
    cases.append(('blur sigma=5', ref, [
        ('this tool', tool.astype(np.int16)),
        ('albumentations', alb.astype(np.int16)),
        ('torchvision', tv.astype(np.int16))]))

    # ---- grayscale ---------------------------------------------------------
    ref = reference(magick, src, ['-grayscale', 'Rec709Luma'], os.path.join(d, 'r2.png'))
    tool, _ = from_tool(a.tool_base, src, 'grayscale', {'method': 'Rec709Luma'})
    alb = A.ToGray(p=1.0)(image=arr)['image']
    if alb.ndim == 2:
        alb = np.stack([alb] * 3, -1)
    tv = TF.rgb_to_grayscale(torch.from_numpy(arr).permute(2, 0, 1), num_output_channels=3)
    tv = tv.permute(1, 2, 0).numpy()
    cases.append(('grayscale Rec709Luma', ref, [
        ('this tool', tool.astype(np.int16)),
        ('albumentations', alb.astype(np.int16)),
        ('torchvision', tv.astype(np.int16))]))

    # ---- rotate 90 ---------------------------------------------------------
    ref = reference(magick, src, ['-background', 'white', '-rotate', '90'],
                    os.path.join(d, 'r3.png'))
    tool, _ = from_tool(a.tool_base, src, 'rotate', {'degrees': 90})
    alb = A.Rotate(limit=(90, 90), p=1.0)(image=arr)['image']
    tv = TF.rotate(torch.from_numpy(arr).permute(2, 0, 1), 90, expand=True)
    tv = tv.permute(1, 2, 0).numpy()
    cases.append(('rotate 90', ref, [
        ('this tool', tool.astype(np.int16)),
        ('albumentations', alb.astype(np.int16)),
        ('torchvision', tv.astype(np.int16))]))

    # ---- report ------------------------------------------------------------
    print(f'  {"operator":22} {"implementation":16} {"max diff":>9} {"mean diff":>10}   verdict')
    print('  ' + '-' * 82)
    for op, ref, impls in cases:
        first = True
        for name, got in impls:
            n, mx, mean, note = summarise(name, ref, got)
            label = op if first else ''
            first = False
            if note:
                print(f'  {label:22} {n:16} {"":>9} {"":>10}   {note}')
            elif mx == 0:
                print(f'  {label:22} {n:16} {mx:>9} {mean:>10.2f}   identical to ImageMagick')
            else:
                print(f'  {label:22} {n:16} {mx:>9} {mean:>10.2f}   DIVERGES')
        print()

    # ---- reproducibility ---------------------------------------------------
    print('  reproducibility: same input, same parameters, run twice')
    print('  ' + '-' * 82)
    _, r1 = from_tool(a.tool_base, src, 'blur', {'sigma': 5})
    _, r2 = from_tool(a.tool_base, src, 'blur', {'sigma': 5})
    same = hashlib.sha256(r1).hexdigest() == hashlib.sha256(r2).hexdigest()
    print(f'    this tool        bytes identical : {same}')

    t1 = A.GaussianBlur(blur_limit=(0, 0), sigma_limit=(3, 7), p=1.0)(image=arr)['image']
    t2 = A.GaussianBlur(blur_limit=(0, 0), sigma_limit=(3, 7), p=1.0)(image=arr)['image']
    print(f'    albumentations   pixels identical: {np.array_equal(t1, t2)}'
          f'   (sigma sampled from a range by design)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
