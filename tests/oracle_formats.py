"""Every input format must survive every class of filter.

Formats are the place the tool is most likely to quietly change behaviour: a
missing delegate routes a format to the Pillow fallback, which only approximates
the ImageMagick operators. This checks each format actually round-trips, that
the output opens, and that it differs from the input.
"""
import hashlib, io, json, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')


def build_corpus(d):
    """One shape image, written out in every format the tool accepts, plus the
    awkward cases: greyscale, alpha, 3000x2250 and a single pixel."""
    im = Image.new('RGB', (400, 300), (250, 250, 250))
    dr = ImageDraw.Draw(im)
    dr.rectangle([30, 30, 180, 170], fill=(225, 29, 72))
    dr.ellipse([210, 30, 360, 170], fill=(45, 212, 191))
    dr.polygon([(120, 200), (210, 280), (30, 280)], fill=(250, 204, 21))
    im.save(f'{d}/t.jpg', quality=92)
    im.save(f'{d}/t.png')
    im.save(f'{d}/t.bmp')
    im.save(f'{d}/t.webp')
    im.save(f'{d}/t.tif')
    im.convert('P', palette=Image.ADAPTIVE).save(f'{d}/t.gif')
    im.convert('L').save(f'{d}/t_gray.png')
    im.convert('RGBA').save(f'{d}/t_alpha.png')
    im.resize((3000, 2250)).save(f'{d}/t_large.jpg', quality=90)
    Image.new('RGB', (1, 1), (12, 34, 56)).save(f'{d}/t_1px.png')


D = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='imt-fmt-')
if len(sys.argv) <= 1:
    build_corpus(D)

FILES = ['t.jpg', 't.png', 't.gif', 't.webp', 't.bmp', 't.tif',
         't_gray.png', 't_alpha.png', 't_large.jpg', 't_1px.png']
CASES = [('blur',       {'sigma': 3}),
         ('grayscale',  {'method': 'Rec709Luminance'}),
         ('colorspace', {'colorspace': 'Lab'}),
         ('rotate',     {'degrees': 90}),
         ('posterize',  {'levels': 4}),
         ('profile',    {'profile': 'AdobeRGB1998'})]

# Rotation is the one case where the output is expected to change shape.
SHAPE_CHANGING = {'rotate'}

def post(path, mut, params):
    out = subprocess.run(
        ['curl', '-s', '-m', '120', '-w', '\n<<%{http_code}>>', '-X', 'POST',
         BASE + '/api/mutate', '-F', 'image=@' + path, '-F', 'mutation=' + mut,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    body, code = out.rsplit('<<', 1)[0], out.rsplit('<<', 1)[1].strip('>>\n ')
    return code, body

rows, problems = [], []
for fn in FILES:
    path = os.path.join(D, fn)
    src = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    try:
        si = Image.open(path); sfmt, ssize, smode = si.format, si.size, si.mode
    except Exception as e:
        problems.append((fn, 'input', str(e))); continue

    results = []
    for mut, params in CASES:
        code, body = post(path, mut, params)
        if code != '200':
            try:   msg = json.loads(body).get('error', body)[:60]
            except Exception: msg = body[:60]
            results.append(f'{mut}:HTTP{code}')
            problems.append((fn, mut, msg))
            continue
        name = json.loads(body)['download_filename']
        stored = json.loads(body)['result_url'].rsplit('/', 1)[-1]
        raw = subprocess.run(['curl', '-s', BASE + '/api/image/' + stored],
                             capture_output=True).stdout
        # the bytes we get back must be a readable image, not an error page
        try:
            oi = Image.open(io.BytesIO(raw)); oi.load()
        except Exception as e:
            results.append(f'{mut}:UNREADABLE')
            problems.append((fn, mut, 'output will not open: ' + str(e)[:50]))
            continue
        if hashlib.sha256(raw).hexdigest() == src:
            results.append(f'{mut}:UNCHANGED')
            problems.append((fn, mut, 'output identical to input'))
        else:
            results.append(f'{mut}:ok({oi.format} {oi.size[0]}x{oi.size[1]})')
    rows.append((fn, f'{sfmt} {ssize[0]}x{ssize[1]} {smode}', results))

w = max(len(r[0]) for r in rows)
print(f'{"file".ljust(w)}  {"decoded as".ljust(22)}  results')
print('-' * 118)
for fn, meta, res in rows:
    flat = '  '.join(r.split(':', 1)[0] + '=' + r.split(':', 1)[1] for r in res)
    print(f'{fn.ljust(w)}  {meta.ljust(22)}  {flat[:88]}')

print()
print(f'{len(rows)} inputs x {len(CASES)} filters = {len(rows)*len(CASES)} calls')
if problems:
    print(f'PROBLEMS ({len(problems)}):')
    for fn, mut, msg in problems:
        print(f'  {fn} / {mut}: {msg}')
else:
    print('no problems: every format decoded, every output opened, every output changed')
sys.exit(1 if problems else 0)
