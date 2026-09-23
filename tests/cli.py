#!/usr/bin/env python3
"""imt — the command line must be able to do what the interface does.

The point of this suite is parity. A second way into the same engine is a
second place for the two to drift, which is the fault shape this codebase keeps
producing: a hand-written list that stops matching the thing it lists.

So the grid is not re-derived here. `imt.py` reads AUGMENTATION_SET out of the
interface, and this suite asserts the two produce byte-for-byte the same job
list. When that check was first written the CLI had its own catalogue-derived
grid, and it differed by six configurations.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMT = os.path.join(ROOT, 'imt.py')
SRC = os.path.join(HERE, 'oracle_source.png')
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
PY_EXE = sys.executable


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(args, expect=0):
    p = subprocess.run([PY_EXE, IMT, '--base', BASE] + args,
                       capture_output=True, text=True, timeout=900)
    return p.returncode, p.stdout, p.stderr


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    # --- the catalogue -----------------------------------------------------
    code, out, err = run(['list', '--json'])
    check('list --json returns the catalogue', code == 0 and out.strip().startswith('{'))
    if code == 0 and out.strip().startswith('{'):
        cat = json.loads(out)
        n = len(cat['continuous']) + len(cat['discrete'])
        live = json.loads(subprocess.run(
            ['curl', '-s', BASE + '/api/mutations'], capture_output=True, text=True).stdout)
        live_n = len(live['continuous']) + len(live['discrete'])
        check('the CLI catalogue is the server catalogue', n == live_n, f'{n} vs {live_n}')

    code, out, _ = run(['list'])
    check('list prints every operator', code == 0 and 'continuous:' in out and 'discrete:' in out)

    code, out, _ = run(['health'])
    check('health reports the engine', code == 0 and 'ImageMagick' in out)

    # --- the grid is the interface's grid, not a second one ----------------
    # imt.py at the root is a launcher now; the implementation is in
    # cli-version/imt/. Load the grid from where it actually lives.
    sys.path.insert(0, os.path.join(ROOT, 'cli-version'))
    from imt.grid import build as build_grid          # noqa: E402
    from imt.client import Client                     # noqa: E402
    ag = load(os.path.join(HERE, 'augmentation_grid.py'), 'ag_mod')
    html = open(os.path.join(ROOT, 'src', 'ui', 'advanced-index.html')).read()

    def norm(jobs):
        return sorted((m, tuple(sorted((k, str(v)) for k, v in p.items()))) for m, p in jobs)

    cat, _raw = Client(BASE).catalogue()
    cli_grid = norm(build_grid(cat))
    ui_grid = norm(ag.build_jobs(html))
    check('the CLI grid is identical to the interface grid',
          cli_grid == ui_grid, f'{len(cli_grid)} vs {len(ui_grid)}')

    # --- applying ----------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix='clitest-')
    try:
        code, out, _ = run(['apply', SRC, 'blur', '--set', 'sigma=5', '-o', tmp])
        produced = out.strip()
        check('apply writes a file', code == 0 and os.path.isfile(produced), produced[-46:])
        check('the filename records the parameter', 'sigma5' in produced)

        code, out, _ = run(['apply', SRC, 'blur', '--set', 'sigma=5',
                            '--channel', 'red', '-o', tmp])
        check('apply honours --channel', code == 0 and 'channelred' in out)

        code, out, _ = run(['apply', SRC, 'morphology', '--set', 'method=Dilate', '-o', tmp])
        check('apply handles a choice parameter', code == 0 and os.path.isfile(out.strip()))

        # the same mutation through the CLI and through the API must agree
        api = subprocess.run(
            ['curl', '-s', '-X', 'POST', BASE + '/api/mutate', '-F', 'image=@' + SRC,
             '-F', 'mutation=blur', '-F', 'parameters={"sigma":5}'],
            capture_output=True, text=True).stdout
        url = json.loads(api)['result_url']
        direct = os.path.join(tmp, 'direct.png')
        subprocess.run(['curl', '-s', '-o', direct, url], check=True)
        cli_file = os.path.join(tmp, 'oracle_source_blur_sigma5.png')
        if os.path.isfile(cli_file):
            import numpy as np
            from PIL import Image
            a = np.asarray(Image.open(cli_file).convert('RGB'), int)
            b = np.asarray(Image.open(direct).convert('RGB'), int)
            check('CLI output equals the API output', a.shape == b.shape
                  and int(abs(a - b).max()) == 0)

        # --- refusals are clear -------------------------------------------
        code, _, err = run(['apply', SRC, 'nosuchfilter', '-o', tmp])
        check('an unknown mutation is refused', code != 0 and 'no such mutation' in err)

        code, _, err = run(['apply', os.path.join(tmp, 'missing.png'), 'blur', '-o', tmp])
        check('a missing file is refused', code != 0 and 'no such file' in err)

        code, _, err = run(['apply', SRC, 'posterize', '--set', 'levels=4',
                            '--channel', 'green', '-o', tmp])
        check('a channel the operator ignores is refused',
              code != 0 and 'does not honour' in err)

        # --- the grid, end to end -----------------------------------------
        code, out, _ = run(['augment', SRC, '-o', os.path.join(tmp, 'dry'), '--dry-run'])
        check('dry-run lists without writing', code == 0
              and not os.path.isdir(os.path.join(tmp, 'dry')))

        outdir = os.path.join(tmp, 'grid')
        code, out, _ = run(['augment', SRC, '-o', outdir, '-q'])
        written = len(os.listdir(outdir)) if os.path.isdir(outdir) else 0
        check('augment writes one file per configuration',
              code == 0 and written == len(cli_grid), f'{written} of {len(cli_grid)}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} CLI checks FAILED')
        return 1
    print(f'  all {len(checks)} CLI checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
