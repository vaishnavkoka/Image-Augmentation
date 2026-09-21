#!/usr/bin/env python3
"""Corpus-scale benchmark: the full augmentation grid over N images.

Reproduces the numbers a tool paper needs to quote -- grid size, throughput,
scaling behaviour, failure rate, output volume and per-filter cost -- from the
running tool rather than from estimates.

The grid is the same one the interface's "Augment" button applies: every
continuous filter at its current value, every discrete filter, and one variant
per sub-option for the three that carry them. It is built from
`/api/mutations`, so it cannot drift from what the tool actually offers.

    bench/scale_run.py                  # 1, 5, 10, 20 images
    bench/scale_run.py --sizes 1,50     # a specific ladder
    bench/scale_run.py --concurrency 8

Writes bench/results/scale.json and prints a table.
"""
import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))


def catalogue():
    return json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                     capture_output=True, text=True).stdout)


def build_grid(cat, health):
    """Every configuration one augmentation run applies to one image."""
    grid = []
    for name, spec in cat['continuous'].items():
        params = {}
        for pname, ps in (spec.get('parameters') or {}).items():
            if 'default' in ps:
                params[pname] = ps['default']
        grid.append((name, params))

    expand = {'colorspace': ('colorspace', health['colorspaces']),
              'grayscale': ('method', health['grayscale_methods']),
              'profile': ('profile', health['icc_profiles'])}
    for name, spec in cat['discrete'].items():
        if name == 'none':
            continue
        if name in expand:
            key, values = expand[name]
            for v in values:
                grid.append((name, {key: v}))
        elif name == 'annotate':
            grid.append((name, {'text': 'sample', 'fontSize': '36', 'position': 'center'}))
        else:
            params = {}
            for pname, ps in (spec.get('parameters') or {}).items():
                if 'default' in ps:
                    params[pname] = ps['default']
            grid.append((name, params))
    return grid


def corpus(n, d, side=1024):
    """N distinct photographic-ish images, so results are not one cached file."""
    paths = []
    for i in range(n):
        im = Image.new('RGB', (side, side), (250 - i % 40, 248, 246))
        dr = ImageDraw.Draw(im)
        for k in range(14):
            x = (i * 37 + k * 71) % side
            y = (i * 53 + k * 97) % side
            r = 40 + (k * 13 + i) % 120
            dr.ellipse([x, y, x + r, y + r],
                       fill=((i * 29 + k * 40) % 256, (k * 55 + 30) % 256, (i * 17 + 90) % 256))
        p = os.path.join(d, f'img_{i:04d}.jpg')
        im.save(p, quality=88)
        paths.append(p)
    return paths


def one(job):
    path, mutation, params = job
    t0 = time.perf_counter()
    out = subprocess.run(
        ['curl', '-s', '-m', '120', '-w', '\n<<%{http_code}>>', '-X', 'POST',
         BASE + '/api/mutate', '-F', 'image=@' + path, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    dt = time.perf_counter() - t0
    code = out.rsplit('<<', 1)[1].strip('>>\n ') if '<<' in out else 'ERR'
    size = 0
    if code == '200':
        try:
            size = int(json.loads(out.rsplit('<<', 1)[0]).get('result_size') or 0)
        except Exception:
            size = 0
    return mutation, code, dt, size


def run(sizes, concurrency):
    cat = catalogue()
    health = json.loads(subprocess.run(['curl', '-s', BASE + '/api/health'],
                                       capture_output=True, text=True).stdout)['imagemagick']
    grid = build_grid(cat, health)
    print(f'  grid: {len(grid)} configurations per image '
          f'({len(cat["continuous"])} continuous + '
          f'{len(grid) - len(cat["continuous"])} discrete-with-sub-options)')
    print(f'  concurrency: {concurrency}\n')

    rows, per_filter = [], {}
    with tempfile.TemporaryDirectory(prefix='imt-bench-') as d:
        for n in sizes:
            paths = corpus(n, d)
            jobs = [(p, m, prm) for p in paths for m, prm in grid]
            t0 = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(concurrency) as ex:
                results = list(ex.map(one, jobs))
            wall = time.perf_counter() - t0
            fails = [r for r in results if r[1] != '200']
            lat = sorted(r[2] for r in results)
            for m, code, dt, _ in results:
                per_filter.setdefault(m, []).append(dt)
            rows.append({
                'images': n,
                'mutations': len(jobs),
                'seconds': round(wall, 2),
                'per_second': round(len(jobs) / wall, 1),
                'failures': len(fails),
                'p50_ms': round(statistics.median(lat) * 1000, 1),
                'p95_ms': round(lat[int(len(lat) * 0.95)] * 1000, 1),
                'max_ms': round(lat[-1] * 1000, 1),
            })
            print(f'  {n:4} images  {len(jobs):6} mutations  {wall:7.1f}s  '
                  f'{len(jobs)/wall:6.1f}/s  p50 {rows[-1]["p50_ms"]:6.1f}ms  '
                  f'p95 {rows[-1]["p95_ms"]:7.1f}ms  failures {len(fails)}')

    slowest = sorted(((statistics.median(v) * 1000, k) for k, v in per_filter.items()),
                     reverse=True)[:8]
    print('\n  slowest filters (median ms):')
    for ms, name in slowest:
        print(f'    {name:20} {ms:7.1f}')

    os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)
    out = {'grid_size': len(grid), 'concurrency': concurrency, 'scaling': rows,
           'median_ms_by_filter': {k: round(statistics.median(v) * 1000, 1)
                                   for k, v in sorted(per_filter.items())},
           'imagemagick': health['version']}
    with open(os.path.join(HERE, 'results', 'scale.json'), 'w') as fh:
        json.dump(out, fh, indent=2)
    print(f'\n  wrote bench/results/scale.json')
    return 1 if any(r['failures'] for r in rows) else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sizes', default='1,5,10,20')
    ap.add_argument('--concurrency', type=int, default=4)
    a = ap.parse_args()
    sys.exit(run([int(x) for x in a.sizes.split(',')], a.concurrency))
