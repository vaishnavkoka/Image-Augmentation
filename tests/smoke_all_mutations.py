"""Smoke test: fire every mutation in the live catalogue at one image with its
default parameters. Every one must return 200 and produce bytes that differ from
the input -- the check that catches a filter that has quietly become a no-op.

    TOOL_BASE=http://127.0.0.1:5000 python3 smoke_all_mutations.py
"""
import json, subprocess, os, hashlib
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
IMG  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'examples', 'input', 'sample.jpg')
src  = hashlib.sha256(open(IMG, 'rb').read()).hexdigest()

cat = json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                capture_output=True, text=True).stdout)
muts = [(k, v) for group in cat.values() for k, v in group.items()]

def defaults(spec):
    p = {}
    for name, s in (spec.get('parameters') or {}).items():
        if 'default' in s:
            p[name] = s['default']
        elif 'options' in s:
            p[name] = s['options'][0]
        elif 'choices' in s:
            p[name] = s['choices'][0]
        # a text field with no default would otherwise post an empty string
        if s.get('type') in ('str', 'string') and not p.get(name):
            p[name] = 'SAMPLE'
    return p

fails, unchanged = [], []
for name, spec in muts:
    p = defaults(spec)
    out = subprocess.run(
        ['curl', '-s', '-m', '90', '-w', '\n<<%{http_code}>>', '-X', 'POST',
         BASE + '/api/mutate', '-F', 'image=@' + IMG, '-F', 'mutation=' + name,
         '-F', 'parameters=' + json.dumps(p)],
        capture_output=True, text=True).stdout
    body, code = out.rsplit('<<', 1)[0], out.rsplit('<<', 1)[1].strip('>>\n ')
    if code != '200':
        fails.append((name, code, body.strip()[:90])); continue
    j = json.loads(body)
    fn = j.get('download_filename') or j.get('filename')
    if not fn:
        fails.append((name, code, 'no filename in ' + str(list(j))[:60])); continue
    got = subprocess.run(['curl', '-s', BASE + '/api/image/' + fn],
                         capture_output=True).stdout
    if hashlib.sha256(got).hexdigest() == src:
        unchanged.append(name)

print(f'{len(muts)} mutations in catalogue '
      f'({len(cat["continuous"])} continuous + {len(cat["discrete"])} discrete)')
print(f'{len(fails)} failures, {len(unchanged)} unchanged')
for f in fails: print('  FAIL', f)
for u in unchanged: print('  UNCHANGED', u)

raise SystemExit(1 if (fails or unchanged) else 0)
