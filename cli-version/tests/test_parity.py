#!/usr/bin/env python3
"""Does the command line send what the interface sends?

Both front ends post to the same API, so the engine cannot be the source of a
difference. The only place they can diverge is in how each BUILDS its request --
which is exactly where this project has produced bugs: chop sent `pixels` where
the catalogue says `value`, charcoal sent `factor` where the operator takes
`radius`, and the interface's grid once differed from a CLI-derived one by six
configurations.

So this drives the real interface in headless Chrome, intercepts the requests
it would send, and compares them against what the CLI builds for the same
choices. Then it applies both for real and compares the returned bytes.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(CLI_ROOT)
UI = os.path.join(ROOT, 'src', 'ui', 'advanced-index.html')
SRC = os.path.join(ROOT, 'tests', 'oracle_source.png')
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
CHROME = next((c for c in ('google-chrome', 'chromium', 'chromium-browser')
               if shutil.which(c)), None)
sys.path.insert(0, CLI_ROOT)
sys.path.insert(0, HERE)
import capability as cap  # noqa: E402


def ui_request_for(filter_value, slider_value=None, channel=None, discrete=False):
    """What the interface would post for this choice, read from the page."""
    html = open(UI).read()
    probe = open(os.path.join(HERE, 'ui_request_capture.js')).read()
    driver = """
    window.addEventListener('load', function () { setTimeout(function () {
      try {
        // Apply returns immediately when nothing is loaded, so the page must be
        // given an image before it will build any request at all.
        var b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
        var bin = atob(b64), arr = new Uint8Array(bin.length);
        for (var k = 0; k < bin.length; k++) arr[k] = bin.charCodeAt(k);
        var dt = new DataTransfer();
        dt.items.add(new File([arr], 'probe.png', { type: 'image/png' }));
        var input = document.getElementById('imageInput');
        input.files = dt.files;
        input.dispatchEvent(new Event('change', { bubbles: true }));

        var name = %s;
        var isDiscrete = %s;
        if (isDiscrete) {
          var tabs = document.querySelectorAll('input[name="filter-type"]');
          tabs.forEach(function (t) { if (t.value === 'discrete') { t.checked = true;
            t.dispatchEvent(new Event('change', {bubbles:true})); } });
          var r = document.querySelector('input[name="discrete-filter"][value="' + name + '"]');
          r.checked = true; r.dispatchEvent(new Event('change', {bubbles:true}));
        } else {
          var r = document.querySelector('input[name="continuous-filter"][value="' + name + '"]');
          r.checked = true; r.dispatchEvent(new Event('change', {bubbles:true}));
          var sv = %s;
          if (sv !== null) {
            var sl = document.querySelector('input[type=range][data-mutation]');
            var own = document.querySelector('input[type=range][data-mutation="' +
                       (r.dataset.mutation || name.replace(/-/g,'_')) + '"]');
            if (own) { own.value = sv; own.dispatchEvent(new Event('input', {bubbles:true})); }
          }
        }
        var ch = %s;
        if (ch) { var c = document.querySelector('input[name="channel-select"][value="' + ch + '"]');
                  if (c) { c.checked = true; } }
        setTimeout(function () {
          try { document.getElementById('applyBtn').click(); }
          catch (e) { window.__err = 'apply: ' + e.message; }
        }, 500);
      } catch (e) { window.__err = e.message; }
      setTimeout(function () {
        document.title = 'CAP' + JSON.stringify(window.__captured || []) +
                         (window.__err ? ('|ERR:' + window.__err) : '');
      }, 1600);
    }, 700); });
    """ % (json.dumps(filter_value), 'true' if discrete else 'false',
           json.dumps(slider_value) if slider_value is not None else 'null',
           json.dumps(channel) if channel else 'null')

    page = html.replace('</body>', f'<script>{probe}</script><script>{driver}</script></body>')
    tmp = tempfile.mkdtemp(prefix='parity-')
    try:
        path = os.path.join(tmp, 'page.html')
        open(path, 'w').write(page)
        dom = subprocess.run(
            [CHROME, '--headless', '--disable-gpu', '--no-sandbox',
             '--window-size=1440,900', '--virtual-time-budget=15000',
             '--dump-dom', 'file://' + path],
            capture_output=True, text=True, timeout=180).stdout
        m = re.search(r'<title>CAP(.*?)</title>', dom, re.S)
        if not m:
            return None, 'the page did not report'
        import html as htmlmod
        body = htmlmod.unescape(m.group(1))
        err = None
        if '|ERR:' in body:
            body, err = body.split('|ERR:', 1)
        try:
            caught = json.loads(body)
        except ValueError:
            return None, f'unreadable capture: {body[:60]}'
        if not caught:
            return None, err or 'the interface sent nothing'
        first = caught[0]
        return {'mutation': first['mutation'],
                'parameters': json.loads(first['parameters'])}, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cli_request_for(mutation, sets=None, channel=None):
    args = [sys.executable, '-m', 'imt.cli', '--base', BASE, 'compare', SRC, mutation]
    for s in sets or []:
        args += ['--set', s]
    if channel:
        args += ['--channel', channel]
    env = dict(os.environ, PYTHONPATH=CLI_ROOT)
    p = subprocess.run(args, capture_output=True, text=True, cwd=CLI_ROOT, env=env)
    if p.returncode != 0:
        return None, p.stderr.strip()[:80]
    return json.loads(p.stdout), None


def sha_of_apply(mutation, params):
    body = subprocess.run(
        ['curl', '-s', '-X', 'POST', BASE + '/api/mutate', '-F', 'image=@' + SRC,
         '-F', 'mutation=' + mutation, '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    d = json.loads(body)
    if 'result_url' not in d:
        return None
    blob = subprocess.run(['curl', '-s', d['result_url']], capture_output=True).stdout
    return hashlib.sha256(blob).hexdigest()


def main():
    if not CHROME:
        print('  no Chrome on PATH, cannot drive the interface — skipping')
        return 0

    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    # (ui radio value, cli mutation, cli --set list, slider value, channel, discrete)
    cases = [
        ('blur', 'blur', ['sigma=5'], 5, None, False),
        ('edge', 'edge', ['radius=3'], 3, None, False),
        ('chop', 'chop', ['value=40'], 40, None, False),
        ('charcoal', 'charcoal', ['radius=4'], 4, None, False),
        ('bilateral_blur', 'bilateral_blur', ['width=7'], 7, None, False),
        ('blur', 'blur', ['sigma=5'], 5, 'red', False),
        ('negate', 'negate', [], None, None, True),
        ('grayscale', 'grayscale', ['method=Rec709Luma'], None, None, True),
    ]

    degraded = cap.is_degraded(BASE)
    channel_works = cap.supports_channel(BASE, SRC)
    if degraded:
        print(f'  this ImageMagick is missing delegates; cases it cannot perform '
              f'are skipped (channel support: {channel_works})\n')

    for ui_value, mutation, sets, slider, channel, discrete in cases:
        label = f'{mutation}{" +" + channel if channel else ""}'
        # An operator this build cannot perform, or a channel it cannot mask,
        # says nothing about whether the two front ends agree.
        if channel and not channel_works:
            print(f'  {label:38}  SKIP  this build cannot restrict a channel')
            continue
        if not cap.supports(BASE, SRC, mutation, dict(
                (k.split('=')[0], k.split('=')[1]) for k in (sets or []))):
            print(f'  {label:38}  SKIP  unavailable on this ImageMagick')
            continue
        ui_req, ui_err = ui_request_for(ui_value, slider, channel, discrete)
        if ui_err:
            check(f'{label}: interface built a request', False, ui_err)
            continue
        cli_req, cli_err = cli_request_for(mutation, sets, channel)
        if cli_err:
            check(f'{label}: CLI built a request', False, cli_err)
            continue

        same_mutation = ui_req['mutation'] == cli_req['mutation']
        check(f'{label}: same mutation name', same_mutation,
              '' if same_mutation else f"{ui_req['mutation']} vs {cli_req['mutation']}")

        up, cp = ui_req['parameters'], cli_req['parameters']
        norm = lambda d: {k: str(v) for k, v in d.items()}
        same_params = norm(up) == norm(cp)
        check(f'{label}: same parameters', same_params,
              '' if same_params else f'{up} vs {cp}')

        if same_mutation and same_params:
            a = sha_of_apply(ui_req['mutation'], up)
            b = sha_of_apply(cli_req['mutation'], cp)
            check(f'{label}: byte-identical output', a is not None and a == b,
                  (a or '?')[:12] + ' vs ' + (b or '?')[:12])

    width = max(len(c[0]) for c in checks) if checks else 10
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail[:52]}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} parity checks FAILED')
        return 1
    print(f'  all {len(checks)} parity checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
