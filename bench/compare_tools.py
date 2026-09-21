#!/usr/bin/env python3
"""Compare this tool against the *tools* a researcher would otherwise use.

bench/compare_libraries.py compares against augmentation *libraries*, which is a
scoping argument for related work rather than a benchmark: they import into a
training loop and are not what someone reaches for to build a mutated corpus.

The honest comparators are:

  1. a hand-written shell loop around `magick` -- the status quo this replaces
  2. GraphicsMagick -- a separate implementation of the same operator names

Both are measured on the axes a tool paper actually claims: does the operator do
what it says, is the output reproducible, and what does it cost to use.

    bench/compare_tools.py --gm /path/to/gm
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
MAGICK = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
SRC = os.path.join(HERE, '..', 'tests', 'oracle_source.png')

# Operator, our parameters, the ImageMagick CLI form, the GraphicsMagick form.
CASES = [
    ('negate',     {},                ['-negate'],              ['-negate']),
    ('blur',       {'sigma': 5},      ['-blur', '2.0x5.0'],     ['-blur', '2.0x5.0']),
    ('edge',       {'radius': 2},     ['-edge', '2'],           ['-edge', '2']),
    ('sharpen',    {'sigma': 2},      ['-sharpen', '0x2'],      ['-sharpen', '0x2']),
    ('solarize',   {'threshold': 50}, ['-solarize', '50%'],     ['-solarize', '50%']),
    ('equalize',   {},                ['-equalize'],            ['-equalize']),
    ('despeckle',  {},                ['-despeckle'],           ['-despeckle']),
    ('swirl',      {'degrees': 90},   ['-swirl', '90'],         ['-swirl', '90']),
    ('gamma',      {'gamma': 2.0},    ['-gamma', '2.0'],        ['-gamma', '2.0']),
    # GraphicsMagick has no -posterize at all, so this is a missing operator
    # rather than a divergent one. None means 'not offered'.
    ('posterize',  {'levels': 4},     ['-posterize', '4'],      None),
]


def arr(path):
    return np.array(Image.open(path).convert('RGB'), np.int16)


def run_cli(binary, pre, args, out, gm=False):
    cmd = ([binary, 'convert', SRC] if gm else [binary, SRC]) + args + [out]
    r = subprocess.run(cmd, capture_output=True, env=pre)
    return r.returncode == 0


def from_tool(mutation, params):
    body = subprocess.run(
        ['curl', '-s', '-X', 'POST', BASE + '/api/mutate', '-F', 'image=@' + SRC,
         '-F', 'mutation=' + mutation, '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    d = json.loads(body)
    raw = subprocess.run(['curl', '-s', d['result_url']], capture_output=True).stdout
    return np.array(Image.open(io.BytesIO(raw)).convert('RGB'), np.int16), raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gm', default=os.environ.get('GM_BIN', ''))
    a = ap.parse_args()
    if not os.path.exists(MAGICK):
        print('  MAGICK_HOME is not set to a build with a magick binary'); return 2

    env = dict(os.environ)
    gm_env = dict(os.environ)
    if a.gm:
        gm_env['LD_LIBRARY_PATH'] = os.pathsep.join(
            [os.path.join(os.path.dirname(os.path.dirname(a.gm)), 'lib'),
             os.path.join(os.path.dirname(os.path.dirname(a.gm)), 'lib', 'x86_64-linux-gnu'),
             gm_env.get('LD_LIBRARY_PATH', '')])

    d = tempfile.mkdtemp(prefix='imt-tools-')
    print('  Operator fidelity — difference from the ImageMagick command line')
    print(f'  {"operator":12} {"this tool":>12} {"GraphicsMagick":>16}')
    print('  ' + '-' * 46)
    agree_tool = agree_gm = gm_ran = 0
    for name, params, im_args, gm_args in CASES:
        ref_p = os.path.join(d, f'ref_{name}.png')
        if not run_cli(MAGICK, env, im_args, ref_p):
            print(f'  {name:12} {"CLI failed":>12}'); continue
        ref = arr(ref_p)

        ours, _ = from_tool(name, params)
        t = int(np.abs(ref - ours).max()) if ours.shape == ref.shape else None
        if t == 0:
            agree_tool += 1

        g = None
        offered = gm_args is not None
        if a.gm and offered:
            gp = os.path.join(d, f'gm_{name}.png')
            if run_cli(a.gm, gm_env, gm_args, gp, gm=True) and os.path.exists(gp):
                gm_ran += 1
                ga = arr(gp)
                g = int(np.abs(ref - ga).max()) if ga.shape == ref.shape else None
                if g == 0:
                    agree_gm += 1
        def fmt(v, offered=True):
            if not offered:
                return 'not offered'
            return 'identical' if v == 0 else (f'max {v}' if v is not None else 'n/a')
        print(f'  {name:12} {fmt(t):>12} {fmt(g, offered):>16}')

    print()
    offered_n = sum(1 for c in CASES if c[3] is not None)
    print(f'  agreeing with the reference: this tool {agree_tool}/{len(CASES)}, '
          f'GraphicsMagick {agree_gm}/{gm_ran} of the {offered_n} it offers '
          f'({len(CASES) - offered_n} operator(s) it does not have)')

    # ---- reproducibility ---------------------------------------------------
    print()
    print('  Reproducibility — same input and parameters, run twice')
    print('  ' + '-' * 46)
    _, r1 = from_tool('blur', {'sigma': 5}); time.sleep(1.1)
    _, r2 = from_tool('blur', {'sigma': 5})
    print(f'    this tool          {"byte-identical" if r1 == r2 else "DIFFERS"}')

    p1, p2 = os.path.join(d, 's1.png'), os.path.join(d, 's2.png')
    run_cli(MAGICK, env, ['-blur', '2.0x5.0'], p1); time.sleep(1.1)
    run_cli(MAGICK, env, ['-blur', '2.0x5.0'], p2)
    same = open(p1, 'rb').read() == open(p2, 'rb').read()
    print(f'    magick + shell     {"byte-identical" if same else "DIFFERS"}'
          f'{"" if same else "   <- embeds the wall clock"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
