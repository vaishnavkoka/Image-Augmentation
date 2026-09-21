"""Every value of every discrete sub-option, not just the first.

smoke_all_mutations.py fires each filter once with its default parameters, so
for colorspace / grayscale / profile it only ever exercises one choice out of
31, 9 and 38. This drives all of them.
"""
import hashlib, io, json, os, subprocess, sys
from PIL import Image

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
IMG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'oracle_source.png')
SRC = hashlib.sha256(open(IMG, 'rb').read()).hexdigest()

health = json.loads(subprocess.run(['curl', '-s', BASE + '/api/health'],
                                   capture_output=True, text=True).stdout)['imagemagick']

# The dither maps are not in /api/health, so take them from the catalogue --
# otherwise they are silently never exercised.
_cat = json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                 capture_output=True, text=True).stdout)
_dither = (_cat['discrete'].get('ordered_dither', {})
           .get('parameters', {}).get('threshold_map', {}).get('options', []))

GROUPS = [('colorspace', 'colorspace', health['colorspaces']),
          ('grayscale',  'method',     health['grayscale_methods']),
          ('profile',    'profile',    health['icc_profiles'])]
if _dither:
    GROUPS.append(('ordered_dither', 'threshold_map', _dither))

fail, unchanged, ok = [], [], 0
for mut, key, values in GROUPS:
    print(f'\n{mut}: {len(values)} values')
    line = []
    for v in values:
        out = subprocess.run(
            ['curl', '-s', '-m', '60', '-w', '\n<<%{http_code}>>', '-X', 'POST',
             BASE + '/api/mutate', '-F', 'image=@' + IMG, '-F', 'mutation=' + mut,
             '-F', 'parameters=' + json.dumps({key: v})],
            capture_output=True, text=True).stdout
        body, code = out.rsplit('<<', 1)[0], out.rsplit('<<', 1)[1].strip('>>\n ')
        if code != '200':
            try:   msg = json.loads(body).get('error', '')[:55]
            except Exception: msg = body[:55]
            fail.append((mut, v, code, msg)); line.append(f'{v}:HTTP{code}'); continue
        name = json.loads(body)['result_url'].rsplit('/', 1)[-1]
        raw = subprocess.run(['curl', '-s', BASE + '/api/image/' + name],
                             capture_output=True).stdout
        try:
            im = Image.open(io.BytesIO(raw)); im.load()
        except Exception as e:
            fail.append((mut, v, code, 'unreadable: ' + str(e)[:40])); line.append(f'{v}:BAD'); continue
        if hashlib.sha256(raw).hexdigest() == SRC:
            unchanged.append((mut, v)); line.append(f'{v}:same')
        else:
            ok += 1; line.append(v)
    # wrap the value list
    buf = '  '
    for t in line:
        if len(buf) + len(t) > 96:
            print(buf); buf = '  '
        buf += t + ' '
    print(buf)

total = sum(len(v) for _, _, v in GROUPS)
print(f'\n{total} sub-option values: {ok} produced a changed image, '
      f'{len(unchanged)} unchanged, {len(fail)} failed')
if fail:
    print('FAILURES:')
    for m, v, c, msg in fail:
        print(f'  {m}.{v}  HTTP {c}  {msg}')
if unchanged:
    print('UNCHANGED (may be legitimate — an identity conversion):')
    for m, v in unchanged:
        print(f'  {m}.{v}')
sys.exit(1 if fail else 0)
