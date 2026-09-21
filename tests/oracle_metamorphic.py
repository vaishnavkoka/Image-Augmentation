#!/usr/bin/env python3
"""Metamorphic oracle: properties that must hold with no reference implementation.

A differential oracle needs a trusted reference. These relations do not: they
follow from what each operator *means*, so they hold for any correct
implementation and catch classes of error the CLI comparison cannot (for
instance an operator that agrees with the CLI because both are mis-invoked).

    python3 tests/oracle_metamorphic.py
"""
import json, os, subprocess, sys, io

API = os.environ.get('TOOL_API', 'http://localhost:5000/api/mutate')
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'oracle_source.png')


def mutate(path, mutation, params):
    """Apply one mutation, return the resulting PNG bytes."""
    r = subprocess.run(['curl', '-s', '-m', '60', '-X', 'POST', API,
                        '-F', f'image=@{path}', '-F', f'mutation={mutation}',
                        '-F', 'parameters=' + json.dumps(params)],
                       capture_output=True, text=True).stdout
    d = json.loads(r)
    if not d.get('success'):
        raise RuntimeError(f"{mutation} {params}: {d.get('error')}")
    return subprocess.run(['curl', '-s', '-m', '30', d['result_url']],
                          capture_output=True).stdout


def chain(blob_or_path, steps, workdir):
    """Apply a sequence of mutations, feeding each result into the next."""
    cur = blob_or_path
    for i, (m, p) in enumerate(steps):
        if isinstance(cur, bytes):
            tmp = os.path.join(workdir, f'step{i}.png')
            open(tmp, 'wb').write(cur)
            cur = tmp
        cur = mutate(cur, m, p)
    return cur


def arr(blob):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(io.BytesIO(blob)).convert('RGB'), int)


def main():
    from PIL import Image
    import numpy as np
    import tempfile
    if not os.path.exists(SRC):
        print('  run oracle_differential.py first to create the source image'); return 1
    wd = tempfile.mkdtemp(prefix='meta_')
    base = np.asarray(Image.open(SRC).convert('RGB'), int)
    base_dims = Image.open(SRC).size
    results = []

    def check(name, ok, detail=''):
        results.append((name, ok, detail))

    def near(a, b, tol=1):
        return int(np.abs(a - b).max()) <= tol

    # --- involutions: applying twice returns the original -------------------
    for op in ['flip', 'flop', 'negate']:
        out = arr(chain(SRC, [(op, {}), (op, {})], wd))
        d = int(np.abs(out - base).max())
        check(f'{op}({op}(x)) == x', d == 0, f'max diff {d}')

    # --- rotation is cyclic --------------------------------------------------
    out = arr(chain(SRC, [('rotate', {'degrees': 90})] * 4, wd))
    d = int(np.abs(out - base).max())
    check('rotate90 applied 4x == x', d == 0, f'max diff {d}')

    # --- identity parameters leave the image alone --------------------------
    for op, p in [('blur', {'sigma': 0}), ('gamma', {'gamma': 1.0}),
                  ('implode', {'factor': 0}), ('black_threshold', {'percentage': 0}),
                  ('rotate', {'degrees': 360})]:
        out = arr(mutate(SRC, op, p))
        d = int(np.abs(out - base).max())
        check(f'{op}{tuple(p.values())} is identity', near(out, base), f'max diff {d}')

    # --- structural guarantees ----------------------------------------------
    out = Image.open(io.BytesIO(mutate(SRC, 'border', {'pixels': 12})))
    check('border(12) adds 24px each axis',
          out.size == (base_dims[0] + 24, base_dims[1] + 24), f'{base_dims} -> {out.size}')

    out = Image.open(io.BytesIO(mutate(SRC, 'chop', {'pixels': 20})))
    check('chop(20) removes 40px width',
          out.size[0] == base_dims[0] - 40, f'{base_dims} -> {out.size}')

    # A square source would make this pass trivially, so rotate a rectangle.
    rect = os.path.join(wd, 'rect.png')
    Image.open(SRC).crop((0, 0, 240, 150)).save(rect)
    out = Image.open(io.BytesIO(mutate(rect, 'rotate', {'degrees': 90})))
    check('rotate90 swaps dimensions (240x150)',
          out.size == (150, 240), f'(240, 150) -> {out.size}')

    # --- content guarantees ----------------------------------------
    g = arr(mutate(SRC, 'grayscale', {'method': 'Rec709Luma'}))
    check('grayscale output has R==G==B',
          int(np.abs(g[..., 0] - g[..., 1]).max() + np.abs(g[..., 1] - g[..., 2]).max()) == 0)

    m = arr(mutate(SRC, 'monochrome', {}))
    check('monochrome has <= 2 distinct levels',
          len(np.unique(m[..., 0])) <= 2, f'{len(np.unique(m[...,0]))} levels')

    c = arr(mutate(SRC, 'colors', {'colors': 8, 'dither': False}))
    ncol = len(np.unique(c.reshape(-1, 3), axis=0))
    check('colors(8, no dither) yields <= 8 colours', ncol <= 8, f'{ncol} colours')

    p4 = arr(mutate(SRC, 'posterize', {'levels': 4, 'dither': False}))
    lv = max(len(np.unique(p4[..., ch])) for ch in range(3))
    check('posterize(4, no dither) <= 4 levels/channel', lv <= 4, f'{lv} levels')

    bt = arr(mutate(SRC, 'black_threshold', {'percentage': 100}))
    check('black_threshold(100%) blacks everything', int(bt.max()) == 0, f'max {int(bt.max())}')

    st = arr(mutate(SRC, 'profile', {'profile': 'Strip'}))
    check('profile Strip leaves pixels alone', near(st, base), f'max diff {int(np.abs(st-base).max())}')

    # --- monotonicity: more blur means less local variance ------------------
    var = []
    for s in [1, 4, 10, 20]:
        a = arr(mutate(SRC, 'blur', {'sigma': s}))
        var.append(float(np.var(np.diff(a[..., 0].astype(float), axis=1))))
    check('blur variance decreases with sigma',
          all(var[i] > var[i+1] for i in range(len(var)-1)),
          ' > '.join(f'{v:.0f}' for v in var))

    # --- determinism ---------------------------------------------------------
    # Deliberately straddle a second boundary. ImageMagick used to stamp
    # date:create / date:modify / date:timestamp and a PNG tIME chunk into every
    # output, so two runs matched only when they landed in the same second --
    # which made this relation pass about 60% of the time and hid the fact that
    # identical input produced byte-different files.
    import time as _time
    a1 = mutate(SRC, 'paint', {'radius': 4})
    _time.sleep(1.1)
    a2 = mutate(SRC, 'paint', {'radius': 4})
    check('same input+params is deterministic (across a second boundary)',
          a1 == a2,
          'identical bytes' if a1 == a2 else f'{len(a1)} vs {len(a2)} bytes')

    # --- report --------------------------------------------------------------
    print(f"{'relation':46s} {'result':7s} detail")
    print('-' * 92)
    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        print(f"{name:46s} {'PASS' if ok else 'FAIL':7s} {detail}")
    print()
    print(f"  {len(results)} metamorphic relations, {len(results)-failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
