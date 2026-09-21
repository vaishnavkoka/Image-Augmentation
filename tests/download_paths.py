#!/usr/bin/env python3
"""The two download routes, which nothing exercised before.

`/api/download/<file>` is what "Download results" uses and had no test at all.
`/api/download-batch` was touched only by the traversal cases in edge_cases.py,
so the ZIP it produces was never opened and its entries never counted -- even
though a name collision inside that ZIP (two sources called the same thing) had
already been fixed once as defect E12.

Checked here: the file that comes back is the mutated image and not the
original, the suggested filename survives, a name collision keeps every entry,
and neither route can be talked into serving something outside its directory.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'oracle_source.png')


def post(mutation, params, src=SRC):
    out = subprocess.run(
        ['curl', '-s', '-m', '60', '-X', 'POST', BASE + '/api/mutate',
         '-F', 'image=@' + src, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    return json.loads(out)


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    # --- single file ------------------------------------------------------
    r = post('blur', {'sigma': 3})
    body = subprocess.run(['curl', '-s', r['download_url']], capture_output=True).stdout
    orig = subprocess.run(['curl', '-s', r['original_url']], capture_output=True).stdout
    src = open(SRC, 'rb').read()

    check('download returns a PNG', body[:8] == b'\x89PNG\r\n\x1a\n', f'{len(body)} bytes')
    check('download is the mutated image, not the source',
          hashlib.sha256(body).hexdigest() != hashlib.sha256(src).hexdigest())
    # The stored original is re-encoded on upload, so compare pixels, not bytes
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(orig)).convert('RGB'), np.int16)
    b = np.asarray(Image.open(SRC).convert('RGB'), np.int16)
    check('original_url serves the image unchanged, pixel for pixel',
          a.shape == b.shape and int(np.abs(a - b).max()) == 0,
          f'max diff {int(np.abs(a - b).max()) if a.shape == b.shape else "shape"}')

    hdrs = subprocess.run(['curl', '-s', '-D-', '-o', '/dev/null', r['download_url']],
                          capture_output=True, text=True).stdout
    check('Content-Disposition carries the readable filename',
          r['download_filename'] in hdrs, r['download_filename'])
    check('served as an attachment', 'attachment' in hdrs.lower())

    # --- traversal on both routes ----------------------------------------
    for route in ('/api/download/', '/api/image/'):
        for probe in ('../src/backend/.env', '..%2f..%2fetc%2fpasswd', '....//src/backend/app.py'):
            code = subprocess.run(
                ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                 BASE + route + probe], capture_output=True, text=True).stdout
            check(f'{route}{probe[:22]} refused', code not in ('200',), f'HTTP {code}')

    # --- batch ZIP --------------------------------------------------------
    results = [post('blur', {'sigma': 3}), post('edge', {'radius': 2}),
               post('negate', {})]
    names = [x['download_filename'] for x in results]
    # The route extracts the file id from an /api/image/ url, which is
    # result_url -- download_url points at the other route and is skipped.
    payload = json.dumps({'results': [
        {'url': x['result_url'], 'filename': x['download_filename']} for x in results]})
    zip_bytes = subprocess.run(
        ['curl', '-s', '-m', '60', '-X', 'POST', BASE + '/api/download-batch',
         '-H', 'Content-Type: application/json', '-d', payload],
        capture_output=True).stdout

    ok_zip = zip_bytes[:2] == b'PK'
    check('batch returns a ZIP', ok_zip, f'{len(zip_bytes)} bytes')
    if ok_zip:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        entries = zf.namelist()
        check('ZIP holds one entry per result',
              len(entries) == len(results), f'{len(entries)} of {len(results)}')
        check('every entry opens and is a PNG',
              all(zf.read(n)[:8] == b'\x89PNG\r\n\x1a\n' for n in entries))
        check('ZIP reports no corruption', zf.testzip() is None)

        # E12 regression: two results sharing a filename must not lose one
        dup = json.dumps({'results': [
            {'url': results[0]['result_url'], 'filename': 'same.png'},
            {'url': results[1]['result_url'], 'filename': 'same.png'}]})
        z2 = subprocess.run(
            ['curl', '-s', '-m', '60', '-X', 'POST', BASE + '/api/download-batch',
             '-H', 'Content-Type: application/json', '-d', dup],
            capture_output=True).stdout
        if z2[:2] == b'PK':
            zf2 = zipfile.ZipFile(io.BytesIO(z2))
            n2 = zf2.namelist()
            check('colliding filenames keep both entries (E12)',
                  len(n2) == 2, f'{len(n2)} entries: {n2}')
            if len(n2) == 2:
                check('the two entries hold different images',
                      zf2.read(n2[0]) != zf2.read(n2[1]))
        else:
            check('colliding filenames keep both entries (E12)', False, 'not a ZIP')

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} checks FAILED')
        return 1
    print(f'  all {len(checks)} download checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
