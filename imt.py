#!/usr/bin/env python3
"""imt — the command line for the Image Mutation Tool.

Everything the web interface does, without the browser: list the catalogue,
apply one mutation, or run the whole augmentation grid over a folder of images.

    ./imt.py list                                   what is available
    ./imt.py list --verbose                         with parameters and ranges
    ./imt.py apply photo.jpg blur --set sigma=5     one mutation
    ./imt.py apply photo.jpg blur --set sigma=5 --channel red
    ./imt.py augment photos/ -o out/                the full grid, every image
    ./imt.py augment photo.jpg -o out/ --dry-run    what it would produce

It talks to the same API the interface uses, so the two cannot drift: the
catalogue, the validation, the worker isolation and the channel rules are all
the server's, not a second copy living here.

Start the server first (./run.sh), or point elsewhere with --base.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp')


# ---- talking to the server -------------------------------------------------

def get_json(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=15) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        die(f'no server at {base} ({e.reason}). Start it with ./run.sh, '
            f'or pass --base')


def post_mutation(base, image, mutation, params):
    """One multipart POST. curl is used so there is no extra dependency."""
    out = subprocess.run(
        ['curl', '-s', '-m', '300', '-X', 'POST', base + '/api/mutate',
         '-F', 'image=@' + image,
         '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except ValueError:
        return {'error': f'unreadable reply from {base}'}


def download(url, dest):
    subprocess.run(['curl', '-s', '-o', dest, url], check=True)
    return dest


def die(message, code=2):
    print(f'imt: {message}', file=sys.stderr)
    raise SystemExit(code)


# ---- the catalogue ---------------------------------------------------------

def catalogue(base):
    d = get_json(base, '/api/mutations')
    return {**d['continuous'], **d['discrete']}, d


def describe(name, spec, verbose):
    line = f"  {name:22}{spec.get('name', '')}"
    if not verbose:
        return line
    rows = [line, f"      {spec.get('description', '')}"]
    for key, p in (spec.get('parameters') or {}).items():
        if p.get('type') == 'choice':
            opts = p.get('options') or []
            shown = ', '.join(map(str, opts[:6]))
            more = f' … {len(opts)} total' if len(opts) > 6 else ''
            rows.append(f"      {key}: {shown}{more}  (default {p.get('default')})")
        else:
            rows.append(f"      {key}: {p.get('min')}..{p.get('max')} "
                        f"(default {p.get('default')})")
    return '\n'.join(rows)


def cmd_list(a):
    cat, raw = catalogue(a.base)
    if a.json:
        print(json.dumps(raw, indent=2))
        return 0
    print(f'{len(raw["continuous"])} continuous, {len(raw["discrete"])} discrete, '
          f'{len(cat)} operators\n')
    for group in ('continuous', 'discrete'):
        print(f'{group}:')
        for name in sorted(raw[group]):
            if a.filter and a.filter.lower() not in name.lower():
                continue
            print(describe(name, raw[group][name], a.verbose))
        print()
    return 0


# ---- applying --------------------------------------------------------------

def parse_sets(pairs):
    """--set sigma=5 --set channel=red  ->  {'sigma': 5.0, 'channel': 'red'}"""
    params = {}
    for item in pairs or []:
        if '=' not in item:
            die(f'--set expects name=value, got {item!r}')
        k, v = item.split('=', 1)
        try:
            params[k] = int(v) if v.lstrip('-').isdigit() else float(v)
        except ValueError:
            params[k] = v
    return params


def cmd_apply(a):
    cat, _ = catalogue(a.base)
    if a.mutation not in cat:
        near = [n for n in cat if a.mutation in n]
        die(f'no such mutation {a.mutation!r}' + (f'. Did you mean: {", ".join(near[:5])}' if near else ''))
    if not os.path.isfile(a.image):
        die(f'no such file: {a.image}')

    params = parse_sets(a.set)
    if a.channel:
        params['channel'] = a.channel

    reply = post_mutation(a.base, a.image, a.mutation, params)
    if 'result_url' not in reply:
        die(reply.get('error', 'the server refused the request'), 1)

    os.makedirs(a.out, exist_ok=True)
    dest = os.path.join(a.out, reply['download_filename'])
    download(reply['result_url'], dest)
    print(dest)
    return 0


# ---- the augmentation grid -------------------------------------------------

def build_grid(base):
    """The grid the interface builds, read from the interface itself.

    Deriving it independently here looked cleaner, and produced a grid that
    differed from the interface's by six configurations -- it added annotate
    with empty text, and disagreed about chop and contrast. Two definitions of
    "the grid" is the shape of fault this codebase keeps producing, so there is
    one: AUGMENTATION_SET in advanced-index.html, parsed here, checked by
    tests/augmentation_grid.py. The catalogue supplies the options each
    expanding family covers.
    """
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'src', 'ui', 'advanced-index.html')
    if not os.path.exists(html_path):
        die(f'cannot find the interface at {html_path}, so the grid it defines '
            f'cannot be read')
    html = open(html_path).read()
    cat, _raw = catalogue(base)

    import re as _re
    m = _re.search(r'const AUGMENTATION_SET\s*=\s*\[', html)
    if not m:
        die('AUGMENTATION_SET not found in the interface')
    i, depth = m.end() - 1, 0
    for j in range(i, len(html)):
        if html[j] == '[':
            depth += 1
        elif html[j] == ']':
            depth -= 1
            if depth == 0:
                break
    entries = _re.findall(r'\{[^{}]*\}', html[i + 1:j])

    def attr(entry, key):
        mm = _re.search(key + r":\s*'([^']+)'", entry)
        return mm.group(1) if mm else None

    jobs = []
    for e in entries:
        mutation, param = attr(e, 'mutation'), attr(e, 'param')
        slider, radio = attr(e, 'slider'), attr(e, 'radio')
        expand = 'expand' in e and 'true' in e
        if slider:
            override = _re.search(r'augValue:\s*([-\d.]+)', e)
            if override:
                raw = override.group(1)
            else:
                sm = _re.search(r'<input[^>]*id="%s"[^>]*>' % _re.escape(slider), html)
                got = _re.search(r'value="([^"]+)"', sm.group(0)) if sm else None
                raw = got.group(1) if got else None
            params = {}
            if param and raw is not None:
                params[param] = float(raw) if '.' in raw else int(raw)
            jobs.append((mutation, params))
        elif radio:
            spec = (cat.get(mutation, {}).get('parameters', {}).get(param, {}) or {})
            options = spec.get('options') or []
            if expand:
                for option in options:
                    jobs.append((mutation, {param: option}))
            else:
                jobs.append((mutation, {param: spec.get('default', options[0] if options else None)}))
        else:
            jobs.append((mutation, {}))
    return jobs


def images_under(path):
    if os.path.isfile(path):
        return [path]
    found = []
    for root, _dirs, files in os.walk(path):
        for f in sorted(files):
            if f.lower().endswith(IMAGE_SUFFIXES):
                found.append(os.path.join(root, f))
    return found


def cmd_augment(a):
    sources = images_under(a.input)
    if not sources:
        die(f'no images under {a.input}')
    jobs = build_grid(a.base)
    total = len(sources) * len(jobs)
    print(f'  {len(sources)} image(s) x {len(jobs)} configurations = {total} mutations')
    if a.dry_run:
        for mutation, params in jobs:
            print(f'    {mutation} {params if params else ""}')
        return 0

    os.makedirs(a.out, exist_ok=True)
    done = failed = 0
    for src in sources:
        stem = os.path.splitext(os.path.basename(src))[0]
        target = os.path.join(a.out, stem) if len(sources) > 1 else a.out
        os.makedirs(target, exist_ok=True)
        for mutation, params in jobs:
            reply = post_mutation(a.base, src, mutation, params)
            if 'result_url' not in reply:
                failed += 1
                print(f'    FAILED {mutation} {params}: '
                      f'{str(reply.get("error"))[:60]}', file=sys.stderr)
                continue
            download(reply['result_url'], os.path.join(target, reply['download_filename']))
            done += 1
            if not a.quiet and done % 25 == 0:
                print(f'    {done}/{total}')
    print(f'  wrote {done} mutations to {a.out}' + (f', {failed} failed' if failed else ''))
    return 1 if failed else 0


# ---- health ----------------------------------------------------------------

def cmd_health(a):
    d = get_json(a.base, '/api/health')
    im = d['imagemagick']
    print(f"  status      : {d['status']}")
    print(f"  ImageMagick : {im['version'].split('https')[0].strip()}")
    print(f"  delegates   : {' '.join(im['delegates'])}")
    print(f"  native      : {' '.join(im['native_formats'])}")
    print(f"  PIL fallback: {' '.join(im['pil_fallback_formats']) or '(none)'}")
    print(f"  ICC profiles: {len(im['icc_profiles'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        prog='imt', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default=DEFAULT_BASE,
                    help=f'API base URL (default {DEFAULT_BASE})')
    sub = ap.add_subparsers(dest='command', required=True)

    p = sub.add_parser('list', help='show the catalogue')
    p.add_argument('-v', '--verbose', action='store_true', help='parameters and ranges')
    p.add_argument('--json', action='store_true', help='raw catalogue as JSON')
    p.add_argument('--filter', help='only names containing this')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('apply', help='apply one mutation to one image')
    p.add_argument('image')
    p.add_argument('mutation')
    p.add_argument('--set', action='append', metavar='NAME=VALUE',
                   help='a parameter, repeatable')
    p.add_argument('--channel', help='restrict to one colour plane')
    p.add_argument('-o', '--out', default='.', help='output directory')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('augment', help='the whole grid over an image or folder')
    p.add_argument('input')
    p.add_argument('-o', '--out', required=True, help='output directory')
    p.add_argument('--dry-run', action='store_true', help='list, do not run')
    p.add_argument('-q', '--quiet', action='store_true')
    p.set_defaults(func=cmd_augment)

    p = sub.add_parser('health', help='what the engine can do')
    p.set_defaults(func=cmd_health)

    a = ap.parse_args()
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
