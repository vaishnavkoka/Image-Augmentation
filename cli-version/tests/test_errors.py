#!/usr/bin/env python3
"""Every failure path: the right exit code, and a message worth reading.

A script driving this needs to tell "the server is not running" from "that
operator does not exist" from "one image of four hundred failed". One exit code
for everything makes that impossible, so each failure has its own and this
suite checks them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(CLI_ROOT)
SRC = os.path.join(ROOT, 'tests', 'oracle_source.png')
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')


def run(args, timeout=600):
    env = dict(os.environ, PYTHONPATH=CLI_ROOT)
    p = subprocess.run([sys.executable, '-m', 'imt.cli'] + args,
                       capture_output=True, text=True, cwd=CLI_ROOT, env=env, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    # every failure must carry a code the user can look up
    def has_code(text, code):
        return f'[{code}]' in text

    tmp = tempfile.mkdtemp(prefix='errtest-')
    try:
        # 0 — success
        code, out, _ = run(['--base', BASE, 'apply', SRC, 'blur', '-o', tmp])
        check('success exits 0', code == 0)

        # 2 — the caller's mistake
        code, _, err = run(['--base', BASE, 'apply', SRC, 'nosuchoperator', '-o', tmp])
        check('unknown mutation exits 2 with E201',
              code == 2 and 'no such mutation' in err and has_code(err, 'E201'))
        check('it suggests what to do', 'imt list' in err or 'did you mean' in err)

        code, _, err = run(['--base', BASE, 'apply', SRC, 'blur',
                            '--set', 'sigma', '-o', tmp])
        check('a malformed --set exits 2 with E202',
              code == 2 and 'name=value' in err and has_code(err, 'E202'))

        code, _, err = run(['--base', BASE, 'apply', SRC, 'blur',
                            '--channel', 'purple', '-o', tmp])
        check('an unknown channel is refused', code != 0 and 'channel' in err.lower())

        code, _, err = run(['--base', BASE, 'apply', SRC, 'posterize',
                            '--set', 'levels=4', '--channel', 'green', '-o', tmp])
        check('a channel the operator ignores is refused',
              code != 0 and 'does not honour' in err)

        # 1 — the request was sound but could not be performed
        code, _, err = run(['--base', BASE, 'apply',
                            os.path.join(tmp, 'absent.png'), 'blur', '-o', tmp])
        check('a missing image exits 1 with E102',
              code == 1 and 'no such file' in err and has_code(err, 'E102'))

        broken = os.path.join(tmp, 'broken.png')
        open(broken, 'wb').write(b'\x89PNG\r\n\x1a\n' + b'garbage' * 4)
        code, _, err = run(['--base', BASE, 'apply', broken, 'blur', '-o', tmp])
        check('an unreadable image exits 1', code == 1, err.strip()[:44])

        # 3 — nothing listening
        code, _, err = run(['--base', 'http://127.0.0.1:59998', 'health'])
        check('no server exits 3 with E301',
              code == 3 and 'no tool at' in err and has_code(err, 'E301'))
        check('it says how to start one', 'run.sh' in err)

        code, _, err = run(['--base', 'http://127.0.0.1:59998', 'apply', SRC, 'blur', '-o', tmp])
        check('no server exits 3 for apply too', code == 3)

        # the directory cases
        code, _, err = run(['--base', BASE, 'augment',
                            os.path.join(tmp, 'nowhere'), '-o', tmp])
        check('a missing input directory exits 2 with E204',
              code == 2 and 'no such' in err and has_code(err, 'E204'))

        empty = os.path.join(tmp, 'empty')
        os.makedirs(empty, exist_ok=True)
        code, _, err = run(['--base', BASE, 'augment', empty, '-o', tmp])
        check('a directory with no images exits 2 with E204',
              code == 2 and 'no images' in err and has_code(err, 'E204'))

        # an unwritable output directory must fail clearly, not silently
        locked = os.path.join(tmp, 'locked')
        os.makedirs(locked, exist_ok=True)
        os.chmod(locked, 0o500)
        code, _, err = run(['--base', BASE, 'apply', SRC, 'blur', '-o', locked])
        check('an unwritable output directory fails', code != 0, err.strip()[:44])
        os.chmod(locked, 0o700)

        # usage errors from argparse itself
        code, _, err = run(['--base', BASE, 'apply'])
        check('missing arguments exit 2', code == 2)
        code, _, err = run(['--base', BASE, 'nosuchcommand'])
        check('an unknown command exits 2', code == 2)
        code, _, err = run(['--base', BASE, '--mode', 'expert', 'list'])
        check('an unknown mode exits 2', code == 2 and 'expert' in err)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

        # A reader closing the pipe is not an error. `imt list --json | head`
        # printed a BrokenPipeError traceback, and closing stdout to fix it
        # traded that for a ValueError at shutdown.
        env = dict(os.environ, PYTHONPATH=CLI_ROOT)
        pipe = subprocess.run(
            f'{sys.executable} -m imt.cli --base {BASE} list --json | head -3',
            shell=True, capture_output=True, text=True, cwd=CLI_ROOT, env=env)
        check('piping into head prints no traceback',
              'Traceback' not in pipe.stderr and 'ValueError' not in pipe.stderr,
              pipe.stderr.strip().splitlines()[0][:40] if pipe.stderr.strip() else '')

        # --quiet must silence the command's own reporting, not just the log
        qout = os.path.join(tmp, 'qgrid')
        p = subprocess.run([sys.executable, '-m', 'imt.cli', '--base', BASE, '-q',
                            'augment', SRC, '-o', qout],
                           capture_output=True, text=True, cwd=CLI_ROOT, env=env, timeout=900)
        check('--quiet prints nothing on success', p.stdout.strip() == '',
              p.stdout.strip()[:40])
        check('--quiet still does the work',
              os.path.isdir(qout) and len(os.listdir(qout)) > 150)

        # every code in the table is reachable documentation
        p = subprocess.run([sys.executable, '-m', 'imt.cli', 'codes'],
                           capture_output=True, text=True, cwd=CLI_ROOT, env=env)
        check('imt codes lists every code',
              p.returncode == 0 and 'E101' in p.stdout and 'E402' in p.stdout)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail[:44]}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} error checks FAILED')
        return 1
    print(f'  all {len(checks)} error checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
