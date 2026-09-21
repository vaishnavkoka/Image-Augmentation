#!/usr/bin/env python3
"""Edge cases: hostile file content, exotic-but-valid images, parameter
boundaries and awkward filenames.

adversarial.py covers the request envelope -- missing fields, malformed JSON,
traversal in a mutation name. This covers the three things it does not:

  A  file *content* that is broken, empty, lying about its type, or hostile
  B  images that are valid but unusual: animated, CMYK, 16-bit, interlaced,
     EXIF-rotated, extreme aspect ratio, very large
  C  every numeric parameter driven to its declared minimum and maximum, and
     one step beyond each, read from the live catalogue rather than hardcoded
  D  filenames with unicode, spaces, quotes, separators and excessive length
  E  multi-frame input: every frame must receive the operator, not just one
  F  the batch-ZIP endpoint: junk payloads, colliding names, poisoned batches
  G  annotate text: unicode, RTL, emoji, quotes, and text wider than the image.
     A 200 is not enough here -- the pixels must actually change, because a
     missing glyph made ImageMagick draw nothing and report success.

The bar is the same throughout: never a 5xx, never a hang, never a response
that claims success while handing back something unreadable.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
TIMEOUT = '75'


def post(path, mutation, params, upload_name=None):
    """Returns (http_code, parsed-or-raw body)."""
    field = f'image=@{path}'
    if upload_name:
        field += f';filename={upload_name}'
    out = subprocess.run(
        ['curl', '-s', '-m', TIMEOUT, '-w', '\n<<%{http_code}>>', '-X', 'POST',
         BASE + '/api/mutate', '-F', field, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    if '<<' not in out:
        return 'NO RESPONSE', out[:80]
    body, code = out.rsplit('<<', 1)[0], out.rsplit('<<', 1)[1].strip('>>\n ')
    try:
        return code, json.loads(body)
    except Exception:
        return code, body[:100]


def fetch_output(payload):
    name = payload['result_url'].rsplit('/', 1)[-1]
    return subprocess.run(['curl', '-s', '-m', '40', BASE + '/api/image/' + name],
                          capture_output=True).stdout


rows, bad = [], []


def record(group, label, code, body, expect):
    """expect is 'ok' (200 + a readable image) or 'reject' (4xx, no crash)."""
    detail = ''
    verdict = 'PASS'

    if str(code).startswith('5') or code == 'NO RESPONSE':
        verdict, detail = 'FAIL', f'server error: {code}'
    elif expect == 'reject':
        if str(code).startswith('4'):
            detail = (body.get('error', '') if isinstance(body, dict) else str(body))[:52]
        else:
            verdict, detail = 'FAIL', f'accepted (HTTP {code}) — expected a 4xx'
    else:  # expect ok
        if code != '200':
            verdict = 'FAIL'
            detail = (body.get('error', '') if isinstance(body, dict) else str(body))[:52]
        else:
            raw = fetch_output(body)
            try:
                im = Image.open(io.BytesIO(raw)); im.load()
                detail = f'{im.format} {im.size[0]}x{im.size[1]} {len(raw)}B'
            except Exception as e:
                verdict, detail = 'FAIL', 'output unreadable: ' + str(e)[:40]

    rows.append((group, label, str(code), verdict, detail))
    if verdict == 'FAIL':
        bad.append(f'{group}/{label}: {detail}')


def main():
    d = tempfile.mkdtemp(prefix='imt-edge-')

    base = Image.new('RGB', (240, 180), (245, 245, 245))
    dr = ImageDraw.Draw(base)
    dr.rectangle([20, 20, 110, 110], fill=(225, 29, 72))
    dr.ellipse([130, 20, 220, 110], fill=(45, 212, 191))
    good = os.path.join(d, 'good.png'); base.save(good)

    # ---- A. broken or lying content ---------------------------------------
    A = []
    p = os.path.join(d, 'empty.png'); open(p, 'wb').close(); A.append(('zero-byte file', p))

    p = os.path.join(d, 'truncated.png')
    with open(good, 'rb') as fh:
        open(p, 'wb').write(fh.read()[:60])
    A.append(('truncated PNG (60 bytes)', p))

    p = os.path.join(d, 'text.png')
    open(p, 'w').write('this is not an image, it is prose\n' * 20)
    A.append(('text file named .png', p))

    p = os.path.join(d, 'page.jpg')
    open(p, 'w').write('<html><body><script>alert(1)</script></body></html>')
    A.append(('HTML named .jpg', p))

    p = os.path.join(d, 'header_only.png')
    open(p, 'wb').write(b'\x89PNG\r\n\x1a\n' + b'\x00' * 40)
    A.append(('PNG magic + garbage', p))

    p = os.path.join(d, 'mislabelled.jpg'); base.save(p, format='PNG')
    A.append(('PNG content, .jpg name', p))

    p = os.path.join(d, 'noext'); base.save(p, format='PNG')
    A.append(('no extension at all', p))

    for label, path in A:
        code, body = post(path, 'blur', {'sigma': 2})
        # A real image with a misleading name should still work: the tool
        # sniffs content. Genuinely broken bytes must be refused, not crash.
        expect = 'ok' if label in ('PNG content, .jpg name', 'no extension at all') else 'reject'
        record('A content', label, code, body, expect)

    # ---- B. valid but unusual ---------------------------------------------
    B = []
    p = os.path.join(d, 'anim.gif')
    frames = []
    for shift in range(4):
        f = Image.new('P', (120, 90))
        fd = ImageDraw.Draw(f)
        fd.rectangle([10 + shift * 12, 10, 50 + shift * 12, 60], fill=1)
        frames.append(f)
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=120, loop=0)
    B.append(('animated GIF, 4 frames', p))

    p = os.path.join(d, 'cmyk.jpg'); base.convert('CMYK').save(p); B.append(('CMYK JPEG', p))

    p = os.path.join(d, 'p16.png')
    Image.fromarray((__import__('numpy').random.RandomState(0)
                     .randint(0, 65535, (120, 160), dtype='uint16')), mode='I;16').save(p)
    B.append(('16-bit greyscale PNG', p))

    p = os.path.join(d, 'prog.jpg'); base.save(p, progressive=True, quality=88)
    B.append(('progressive JPEG', p))

    p = os.path.join(d, 'inter.png'); base.save(p, interlace=1)
    B.append(('interlaced PNG', p))

    p = os.path.join(d, 'pal_alpha.png')
    base.convert('RGBA').convert('P', palette=Image.ADAPTIVE, colors=64).save(p, transparency=0)
    B.append(('palette PNG with transparency', p))

    p = os.path.join(d, 'thin.png'); Image.new('RGB', (1, 4000), (10, 120, 200)).save(p)
    B.append(('extreme aspect 1x4000', p))

    p = os.path.join(d, 'wide.png'); Image.new('RGB', (4000, 1), (200, 60, 10)).save(p)
    B.append(('extreme aspect 4000x1', p))

    p = os.path.join(d, 'big.jpg'); base.resize((5000, 3750)).save(p, quality=85)
    B.append(('5000x3750 JPEG', p))

    p = os.path.join(d, 'exif.jpg')
    base.save(p, quality=88, exif=Image.Exif())
    B.append(('JPEG carrying an EXIF block', p))

    for label, path in B:
        code, body = post(path, 'blur', {'sigma': 2})
        record('B unusual', label, code, body, 'ok')

    # ---- C. every numeric parameter at its declared bounds -----------------
    cat = json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                    capture_output=True, text=True).stdout)
    checked = 0
    for group in cat.values():
        for mut, spec in group.items():
            for pname, ps in (spec.get('parameters') or {}).items():
                if ps.get('type') not in ('int', 'float'):
                    continue
                lo, hi = ps.get('min'), ps.get('max')
                if lo is None or hi is None:
                    continue
                step = ps.get('step') or 1
                for value, expect, tag in [(lo, 'ok', 'min'), (hi, 'ok', 'max'),
                                           (lo - step, 'ok', 'below min'),
                                           (hi + step, 'ok', 'above max')]:
                    code, body = post(good, mut, {pname: value})
                    # Out-of-range must be clamped or refused, never a 5xx and
                    # never a success carrying an unreadable file.
                    if expect == 'ok' and str(code).startswith('4'):
                        expect = 'reject'
                    record('C bounds', f'{mut}.{pname} {tag}={value}', code, body, expect)
                    checked += 1

    # ---- D. awkward filenames ---------------------------------------------
    D = [('unicode name', 'кот-图片-🙂.png'),
         ('spaces and quotes', 'my "photo" (1).png'),
         ('path separators', '../../etc/passwd.png'),
         ('leading dash', '-rf.png'),
         ('255-character name', 'x' * 250 + '.png'),
         ('semicolon and newline', 'a;b.png')]
    for label, name in D:
        code, body = post(good, 'blur', {'sigma': 2}, upload_name=name)
        record('D filenames', label, code, body, 'ok')

    # ---- E. multi-frame: the operator must reach every frame ---------------
    # Wand's methods act on whichever frame the iterator points at, which after
    # load is the last one. This used to leave an animated GIF with only its
    # final frame mutated -- HTTP 200, a file that opens, three frames of
    # untouched data. The CLI applies to all frames; so must the tool.
    from PIL import ImageSequence

    anim = os.path.join(d, 'anim.gif')
    cols = [(230, 40, 60), (40, 180, 200), (240, 190, 40), (120, 90, 220)]
    fr = []
    for i, c in enumerate(cols):
        f = Image.new('RGB', (120, 90), (250, 250, 250))
        fd = ImageDraw.Draw(f)
        fd.rectangle([8 + i * 22, 15, 58 + i * 22, 70], fill=c)
        fd.ellipse([70, 20, 110, 60], fill=(20, 20, 20))
        fr.append(f.convert('P', palette=Image.ADAPTIVE, colors=64))
    fr[0].save(anim, save_all=True, append_images=fr[1:], duration=150, loop=0)

    pages = os.path.join(d, 'multi.tif')
    pg = []
    for i, c in enumerate([(220, 50, 60), (50, 180, 120), (60, 90, 220)]):
        im = Image.new('RGB', (140, 100), (250, 250, 250))
        pd = ImageDraw.Draw(im)
        pd.rectangle([10 + i * 18, 10, 80 + i * 18, 80], fill=c)
        pg.append(im)
    pg[0].save(pages, save_all=True, append_images=pg[1:])

    import numpy as np
    for label, path, mut, params in [
            ('animated GIF, all 4 frames', anim, 'negate', {}),
            ('animated GIF, blur all frames', anim, 'blur', {'sigma': 3}),
            ('multi-page TIFF, all 3 pages', pages, 'negate', {})]:
        code, body = post(path, mut, params)
        if code != '200':
            record('E frames', label, code, body, 'ok')
            continue
        raw = fetch_output(body)
        src = [np.array(f.convert('RGB'))
               for f in ImageSequence.Iterator(Image.open(path))]
        out = [np.array(f.convert('RGB'))
               for f in ImageSequence.Iterator(Image.open(io.BytesIO(raw)))]
        hits = sum(1 for a, b in zip(src, out)
                   if a.shape == b.shape
                   and int(np.abs(a.astype(int) - b.astype(int)).max()) > 8)
        ok = len(out) == len(src) and hits == len(src)
        rows.append(('E frames', label, str(code), 'PASS' if ok else 'FAIL',
                     f'{hits}/{len(src)} frames mutated, {len(out)} kept'))
        if not ok:
            bad.append(f'E frames/{label}: only {hits} of {len(src)} frames mutated')

    # ---- F. the batch-ZIP endpoint -----------------------------------------
    import zipfile

    def fresh_url():
        b = subprocess.run(['curl', '-s', '-X', 'POST', BASE + '/api/mutate',
                            '-F', 'image=@' + good, '-F', 'mutation=negate',
                            '-F', 'parameters={}'], capture_output=True, text=True).stdout
        return json.loads(b)['result_url']

    def zbatch(results, label, expect):
        payload = {} if results == 'MISSING' else {'results': results}
        raw = subprocess.run(['curl', '-s', '-m', '90', '-w', '\n<<%{http_code}>>',
                              '-X', 'POST', BASE + '/api/download-batch',
                              '-H', 'Content-Type: application/json',
                              '-d', json.dumps(payload)], capture_output=True).stdout
        sep = raw.rfind(b'<<')
        bodyb, code = raw[:sep], raw[sep:].decode('latin1').strip('<>\n ')
        verdict, detail = 'PASS', ''
        if code.startswith('5'):
            verdict, detail = 'FAIL', 'server error'
        elif expect == 'zip':
            if code != '200':
                verdict, detail = 'FAIL', bodyb[:60].decode('latin1', 'replace')
            else:
                try:
                    z = zipfile.ZipFile(io.BytesIO(bodyb))
                    names = z.namelist()
                    if z.testzip():
                        verdict, detail = 'FAIL', 'corrupt entry'
                    elif len(names) != len(set(names)):
                        verdict, detail = 'FAIL', 'duplicate names inside the ZIP'
                    else:
                        detail = f'{len(names)} entries, {len(bodyb)}B'
                except Exception as e:
                    verdict, detail = 'FAIL', 'not a zip: ' + str(e)[:36]
        else:
            if code.startswith('4'):
                try:    detail = json.loads(bodyb).get('error', '')[:52]
                except Exception: detail = bodyb[:52].decode('latin1', 'replace')
            else:
                verdict, detail = 'FAIL', f'accepted (HTTP {code}) — expected 4xx'
        rows.append(('F batch-zip', label, code, verdict, detail))
        if verdict == 'FAIL':
            bad.append(f'F batch-zip/{label}: {detail}')

    u = fresh_url()
    poison = {'url': BASE + '/api/image/../src/backend/.env', 'filename': 'x.jpg'}
    zbatch('MISSING', 'no results key', 'reject')
    zbatch([], 'empty list', 'reject')
    zbatch('notalist', 'results is a string', 'reject')
    zbatch([{'url': u, 'filename': 'a.jpg'}], 'single result', 'zip')
    zbatch([{'url': u, 'filename': f'f{i}.jpg'} for i in range(40)], '40 results', 'zip')
    zbatch([{'url': u, 'filename': 'same.jpg'} for _ in range(3)], 'colliding names', 'zip')
    zbatch([None, 1, {}], 'junk entries', 'reject')
    zbatch([{'url': 'http://evil.test/x.jpg', 'filename': 'e.jpg'}], 'foreign host', 'reject')
    zbatch([poison], 'path traversal', 'reject')
    zbatch([{'url': u, 'filename': 'a.jpg'}, poison], 'one good + one traversal', 'zip')

    # ---- G. annotate text --------------------------------------------------
    # The API advertises max_length 100, but centring put the origin at a
    # negative x once the text was wider than the image, and Wand refuses a
    # negative origin -- so a 100-character caption, which validation allows,
    # came back as "x=-882 must be a positive integer".
    for label, text, expect in [
            ('plain ascii', 'Hello world', 'ok'),
            ('latin accents', 'Ünïcödé ñ ß', 'ok'),
            ('CJK', '日本語のテキスト', 'ok'),
            ('RTL arabic', 'مرحبا بالعالم', 'ok'),
            ('emoji', 'emoji 🙂🎨', 'ok'),
            ('100 chars (at the cap)', 'a' * 100, 'ok'),
            ('400 chars (over the cap)', 'a' * 400, 'ok'),
            ('embedded newline', 'line1\nline2', 'ok'),
            ('quotes and backslash', 'quote \' and " and \\', 'ok'),
            ('format specifiers', '%d %s %n', 'ok'),
            ('cyrillic', 'Привет мир', 'ok'),
            ('hebrew', 'שלום עולם', 'ok'),
            ('thai', 'สวัสดี', 'ok'),
            ('mixed scripts', 'Hello 日本 مرحبا', 'ok'),
            ('empty string', '', 'reject')]:
        code, body = post(good, 'annotate',
                          {'text': text, 'fontSize': '36', 'position': 'center'})
        if expect == 'reject' or code != '200':
            record('G annotate', label, code, body, expect)
            continue
        # Success is not enough: confirm glyphs were actually drawn.
        raw = fetch_output(body)
        import numpy as np
        before = np.array(Image.open(good).convert('RGB'))
        after = np.array(Image.open(io.BytesIO(raw)).convert('RGB'))
        changed = int((np.abs(before.astype(int) - after.astype(int)).sum(axis=2) > 10).sum())
        ok = changed > 50
        rows.append(('G annotate', label, code, 'PASS' if ok else 'FAIL',
                     f'{changed} pixels drawn'))
        if not ok:
            bad.append(f'G annotate/{label}: 200 but nothing drawn')

    # ---- report ------------------------------------------------------------
    wl = max(len(r[1]) for r in rows)
    cur = None
    for group, label, code, verdict, detail in rows:
        if group != cur:
            print(f'\n{group}')
            print('-' * (wl + 30))
            cur = group
        print(f'  {label.ljust(wl)}  {code:>4}  {verdict:5}  {detail}')

    print()
    print(f'{len(rows)} edge cases  ({checked} of them parameter bounds)')
    if bad:
        print(f'FAILURES ({len(bad)}):')
        for b in bad:
            print('  ' + b)
    else:
        print('no failures: nothing 5xx, nothing hung, every accepted input '
              'produced a readable image')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
