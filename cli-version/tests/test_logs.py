#!/usr/bin/env python3
"""Logging and progress: a record worth keeping, and a bar that never breaks a run."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(CLI_ROOT)
SRC = os.path.join(ROOT, 'tests', 'oracle_source.png')
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
sys.path.insert(0, CLI_ROOT)


def run(args, timeout=900):
    env = dict(os.environ, PYTHONPATH=CLI_ROOT)
    return subprocess.run([sys.executable, '-m', 'imt.cli'] + args,
                          capture_output=True, text=True, cwd=CLI_ROOT,
                          env=env, timeout=timeout)


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='logtest-')
    try:
        log = os.path.join(tmp, 'run.log')
        p = run(['--base', BASE, '--log-file', log, 'apply', SRC, 'blur',
                 '--set', 'sigma=4', '-o', tmp])
        check('a log file is written', os.path.isfile(log))
        body = open(log).read() if os.path.isfile(log) else ''
        check('it records the mutation', 'blur' in body)
        check('it records the parameters', 'sigma' in body)
        check('lines are timestamped',
              bool(re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', body)))

        # routine success must not clutter the console: the output is a path
        check('success prints only the path',
              p.stdout.strip().endswith('.png') and len(p.stdout.strip().splitlines()) == 1,
              p.stdout.strip()[-40:])

        # an error appears once on the console, and in the file
        log2 = os.path.join(tmp, 'err.log')
        p = run(['--base', BASE, '--log-file', log2, 'apply', SRC, 'nosuchop', '-o', tmp])
        shown = [l for l in p.stderr.splitlines() if 'no such mutation' in l]
        check('an error prints exactly once', len(shown) == 1, f'{len(shown)} lines')
        check('the error reaches the log file',
              os.path.isfile(log2) and 'no such mutation' in open(log2).read())

        # --quiet silences the console but keeps the record
        log3 = os.path.join(tmp, 'quiet.log')
        p = run(['--base', BASE, '--log-file', log3, '-q', 'apply', SRC, 'blur', '-o', tmp])
        check('--quiet still writes the log',
              os.path.isfile(log3) and 'blur' in open(log3).read())

        # DEBUG opens the console up
        log4 = os.path.join(tmp, 'debug.log')
        p = run(['--base', BASE, '--log-file', log4, '--log-level', 'DEBUG',
                 'apply', SRC, 'blur', '-o', tmp])
        check('DEBUG shows the request on the console', 'POST' in p.stderr or 'POST' in p.stdout)

        # a log directory that cannot be created must not stop the run
        blocked = os.path.join(tmp, 'blocked')
        os.makedirs(blocked, exist_ok=True)
        os.chmod(blocked, 0o500)
        p = run(['--base', BASE, '--log-file', os.path.join(blocked, 'sub', 'x.log'),
                 'apply', SRC, 'blur', '-o', tmp])
        check('an unwritable log does not stop the run', p.returncode == 0,
              p.stderr.strip()[:40])
        os.chmod(blocked, 0o700)

        # the progress bar, both implementations. tqdm is optional, so the
        # fallback must be exercised even on a machine that has tqdm --
        # otherwise the path that runs for everyone without it is never tested.
        from imt.progress import bar, HAVE_TQDM
        b = bar(10, desc='test', disable=True)
        for _ in range(10):
            b.update(1)
        b.close()
        check('the active progress bar runs without error', True,
              'tqdm' if HAVE_TQDM else 'built-in fallback')

        import builtins
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == 'tqdm':
                raise ImportError('blocked for this test')
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            for mod in [m for m in list(sys.modules) if m.startswith('imt.progress')]:
                del sys.modules[mod]
            import imt.progress as fallback_mod
            check('without tqdm the fallback takes over', fallback_mod.HAVE_TQDM is False)
            fb = fallback_mod.bar(5, desc='fallback', disable=True)
            for _ in range(5):
                fb.update(1)
            fb.close()
            with fallback_mod.bar(3, desc='ctx', disable=True) as fbc:
                fbc.update(3)
            check('the fallback bar updates and closes cleanly', True)
        finally:
            builtins.__import__ = real_import
            for mod in [m for m in list(sys.modules) if m.startswith('imt.progress')]:
                del sys.modules[mod]

        # a batch shows progress and still reports a total
        outdir = os.path.join(tmp, 'grid')
        p = run(['--base', BASE, '--log-file', os.path.join(tmp, 'grid.log'),
                 'augment', SRC, '-o', outdir])
        wrote = len(os.listdir(outdir)) if os.path.isdir(outdir) else 0
        # 173 on a complete ImageMagick, about 76 on a stock one. The run may
        # exit non-zero on a build where part of the catalogue is unavailable,
        # which is honest reporting rather than a failure of augment.
        check('augment writes what this build supports', wrote > 50, f'{wrote} files')
        check('augment records the run', 'augment' in open(os.path.join(tmp, 'grid.log')).read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail[:42]}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} logging checks FAILED')
        return 1
    print(f'  all {len(checks)} logging checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
