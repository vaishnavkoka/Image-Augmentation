#!/usr/bin/env python3
"""The augmentation grid is the tool's headline number -- verify it, don't assert it.

"160 mutations per image" appears in the paper, the report and the interface, but
nothing executed the grid end to end. Running it found that one of the 160 jobs
had always failed: the entry for `charcoal` passed `factor`, while the operator
takes `radius`. Every oracle passed throughout, because they drive operators
directly and never read the grid.

Running it also showed that the count and the yield are not the same number.
Several sliders default to a value that is the identity -- median's kernel of 1,
`colors` at 256, gamma 1.0 -- so at default positions those configurations return
the input image. That is worth reporting next to the headline figure rather than
hiding: the grid is N configurations, of which a smaller number are distinct
images at default settings.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, '..', 'src', 'ui', 'advanced-index.html')
SRC = os.path.join(HERE, 'oracle_source.png')


def build_jobs(html):
    """Rebuild the job list exactly as the augment button does."""
    m = re.search(r'const AUGMENTATION_SET\s*=\s*\[', html)
    i, depth = m.end() - 1, 0
    for j in range(i, len(html)):
        if html[j] == '[':
            depth += 1
        elif html[j] == ']':
            depth -= 1
            if depth == 0:
                break
    entries = re.findall(r'\{[^{}]*\}', html[i + 1:j])

    def attr(e, k):
        mm = re.search(k + r":\s*'([^']+)'", e)
        return mm.group(1) if mm else None

    jobs = []
    for e in entries:
        mut, param = attr(e, 'mutation'), attr(e, 'param')
        slider, radio = attr(e, 'slider'), attr(e, 'radio')
        expand = 'expand' in e and 'true' in e
        if slider:
            # augValue overrides the slider for augmentation only. Six filters
            # default to their own identity, so without this the grid returned
            # six copies of the input.
            override = re.search(r'augValue:\s*([-\d.]+)', e)
            if override:
                val = override.group(1)
            else:
                sm = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(slider), html)
                val = re.search(r'value="([^"]+)"', sm.group(0)).group(1) if sm else None
            params = {}
            if param and val is not None:
                params[param] = float(val) if '.' in val else int(val)
            jobs.append((mut, params))
        elif radio:
            opts = re.findall(r'<input[^>]*name="%s"[^>]*value="([^"]+)"' % re.escape(radio), html)
            if expand:
                for o in opts:
                    jobs.append((mut, {param: o}))
            else:
                ch = re.search(r'<input[^>]*name="%s"[^>]*value="([^"]+)"[^>]*checked'
                               % re.escape(radio), html)
                jobs.append((mut, {param: ch.group(1) if ch else opts[0]}))
        else:
            jobs.append((mut, {}))
    return jobs


def run(job):
    mut, params = job
    out = subprocess.run(
        ['curl', '-s', '-m', '120', '-X', 'POST', BASE + '/api/mutate',
         '-F', 'image=@' + SRC, '-F', 'mutation=' + mut,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except ValueError:
        return mut, params, 'unreadable reply', None
    if 'result_url' not in d:
        return mut, params, d.get('error', '?'), None
    img = subprocess.run(['curl', '-s', d['result_url']], capture_output=True).stdout
    return mut, params, None, hashlib.sha256(img).hexdigest()


def main():
    html = open(UI).read()
    catalogue = json.loads(subprocess.run(
        ['curl', '-s', '-m', '10', BASE + '/api/mutations'],
        capture_output=True, text=True).stdout)
    catalogue = {**catalogue['continuous'], **catalogue['discrete']}

    jobs = build_jobs(html)
    print(f'  grid builds {len(jobs)} configurations per image')

    # static check first: a wrong parameter name is a bug in the grid, not a
    # backend failure, and saying so precisely is more useful than a 400
    static = []
    for mut, params in jobs:
        spec = catalogue.get(mut)
        if not spec:
            static.append(f'{mut}: not in the catalogue')
            continue
        known = set((spec.get('parameters') or {}).keys())
        for k in params:
            if k not in known:
                static.append(f'{mut}: grid passes "{k}", operator accepts {sorted(known)}')
    for s in static:
        print('    BAD', s)

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(run, jobs))

    # How many configurations return the input untouched? That count is
    # image-dependent -- measured on one synthetic image it read 10, on a photo
    # 16 -- so measuring it on a single input got it wrong. Several inputs, and
    # only the configurations identical on ALL of them are unconditional.
    import numpy as _np
    from PIL import Image as _PILImage
    probes = {'source': SRC}
    _tmp = tempfile.mkdtemp(prefix='gridprobe-')
    try:
        _rng = _np.random.RandomState(7)
        noise = os.path.join(_tmp, 'noise.png')
        _PILImage.fromarray(_rng.randint(0, 256, (160, 160, 3), dtype=_np.uint8)).save(noise)
        probes['noise'] = noise
        flat = os.path.join(_tmp, 'flat.png')
        _PILImage.fromarray((_np.full((160, 160, 3), 128)
                             + _rng.randint(-10, 10, (160, 160, 3))).astype(_np.uint8)).save(flat)
        probes['low contrast'] = flat

        identical_sets = {}
        for label, path in probes.items():
            base = _np.asarray(_PILImage.open(path).convert('RGB'), _np.int32)

            def one(job, _p=path, _b=base):
                mut, params = job
                body = subprocess.run(
                    ['curl', '-s', '-m', '120', '-X', 'POST', BASE + '/api/mutate',
                     '-F', 'image=@' + _p, '-F', 'mutation=' + mut,
                     '-F', 'parameters=' + json.dumps(params)],
                    capture_output=True, text=True).stdout
                try:
                    d = json.loads(body)
                except ValueError:
                    return None
                if 'result_url' not in d:
                    return None
                blob = subprocess.run(['curl', '-s', d['result_url']], capture_output=True).stdout
                # No blanket except here. Swallowing decode errors is exactly
                # how this check first reported "everything changed": io was not
                # imported, every call raised NameError, and the suite passed
                # while measuring nothing.
                arr = _np.asarray(_PILImage.open(io.BytesIO(blob)).convert('RGB'), _np.int32)
                if arr.shape != _b.shape or int(_np.abs(arr - _b).max()) != 0:
                    return None
                return (mut, tuple(sorted(params.items())))

            with ThreadPoolExecutor(max_workers=8) as ex2:
                identical_sets[label] = {r for r in ex2.map(one, jobs) if r}
            print(f'  {label:14}: {len(jobs) - len(identical_sets[label])} of {len(jobs)} '
                  f'changed the image')

        unconditional = set.intersection(*identical_sets.values())
        print(f'  identical on every probe image: {len(unconditional)}')
        for mut, params in sorted(unconditional):
            print(f'      {mut} {dict(params)}')
        # Only the sRGB-inherent ones may remain: colorspace sRGB/Transparent
        # and profile Strip/sRGB do real work on a CMYK or profiled source.
        SRGB_INHERENT = {('colorspace', (('colorspace', 'sRGB'),)),
                         ('colorspace', (('colorspace', 'Transparent'),)),
                         ('profile', (('profile', 'Strip'),)),
                         ('profile', (('profile', 'sRGB'),))}
        unexpected = unconditional - SRGB_INHERENT
        if unexpected:
            static.append(f'{len(unexpected)} configuration(s) return the input '
                          f'unchanged on every image: {sorted(unexpected)[:4]}')
    finally:
        import shutil as _shutil
        _shutil.rmtree(_tmp, ignore_errors=True)

    fails = [r for r in res if r[2]]
    hashes = [r[3] for r in res if r[3]]
    distinct = len(set(hashes))
    print(f'  {len(jobs) - len(fails)} applied, {len(fails)} failed')
    print(f'  {distinct} distinct images from {len(hashes)} successful configurations')

    dupes = [h for h, c in Counter(hashes).items() if c > 1]
    if dupes:
        print(f'  {len(dupes)} output(s) reached by more than one configuration:')
        for h in dupes:
            same = [f'{r[0]}{r[1] or ""}' for r in res if r[3] == h]
            print('      ' + ' == '.join(same)[:160])

    for mut, params, err, _ in fails:
        print(f'    FAIL {mut} {params}: {str(err)[:80]}')

    print()
    if static or fails:
        print(f'  {len(static) + len(fails)} PROBLEM(S) in the augmentation grid')
        return 1
    print('  every configuration in the grid applies cleanly')
    return 0


if __name__ == '__main__':
    sys.exit(main())
