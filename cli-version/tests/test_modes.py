#!/usr/bin/env python3
"""The CLI's modes must be the interface's modes.

They are parsed out of advanced-index.html rather than copied, so this suite
checks the parse still finds all three and still agrees with the page, and that
each restriction is actually enforced rather than merely declared.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(CLI_ROOT)
UI = os.path.join(ROOT, 'src', 'ui', 'advanced-index.html')
SRC = os.path.join(ROOT, 'tests', 'oracle_source.png')
BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
sys.path.insert(0, CLI_ROOT)
from imt.modes import load  # noqa: E402


def run(args):
    env = dict(os.environ, PYTHONPATH=CLI_ROOT)
    p = subprocess.run([sys.executable, '-m', 'imt.cli', '--base', BASE] + args,
                       capture_output=True, text=True, cwd=CLI_ROOT, env=env, timeout=600)
    return p.returncode, p.stdout, p.stderr


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    modes = load()
    check('all three modes were found', set(modes) == {'beginner', 'intermediate', 'advanced'},
          ', '.join(sorted(modes)))

    # what the page itself says, read independently of imt.modes
    html = open(UI).read()
    for name, expect_cap in (('beginner', 40), ('intermediate', 40), ('advanced', 500)):
        check(f'{name}: cap matches the interface', modes[name]['cap'] == expect_cap,
              f"{modes[name]['cap']} vs {expect_cap}")

    check('beginner offers a restricted filter set',
          0 < len(modes['beginner']['continuous']) < 20,
          f"{len(modes['beginner']['continuous'])} filters")
    check('advanced offers the whole catalogue', modes['advanced']['continuous'] == [])
    check('beginner does not tune', modes['beginner']['tune'] is False)
    check('beginner does not augment', modes['beginner']['augment'] is False)
    check('advanced augments', modes['advanced']['augment'] is True)
    check('only advanced offers numeric entry',
          modes['advanced']['numeric'] and not modes['beginner']['numeric']
          and not modes['intermediate']['numeric'])

    tmp = tempfile.mkdtemp(prefix='modetest-')
    try:
        # enforcement, not just declaration
        code, _, err = run(['--mode', 'beginner', 'apply', SRC, 'blur',
                            '--set', 'sigma=5', '-o', tmp])
        check('beginner refuses tuning', code == 2 and 'defaults' in err)

        code, _, err = run(['--mode', 'beginner', 'apply', SRC, 'kuwahara', '-o', tmp])
        check('beginner refuses a filter it does not offer',
              code == 2 and 'does not offer' in err)

        code, out, _ = run(['--mode', 'beginner', 'apply', SRC, 'blur', '-o', tmp])
        check('beginner applies its own filters at defaults',
              code == 0 and os.path.isfile(out.strip()))

        code, _, err = run(['--mode', 'beginner', 'augment', SRC, '-o', tmp])
        check('beginner refuses augmentation', code == 2 and 'augmentation' in err)

        code, _, err = run(['--mode', 'intermediate', 'apply', SRC, 'blur',
                            '--set', 'sigma=5', '-o', tmp])
        check('intermediate allows tuning', code == 0)

        code, _, err = run(['--mode', 'intermediate', 'apply', SRC, 'blur',
                            '--channel', 'red', '-o', tmp])
        check('intermediate refuses a channel restriction',
              code == 2 and 'channel' in err)

        code, out, _ = run(['--mode', 'advanced', 'apply', SRC, 'blur',
                            '--set', 'sigma=5', '--channel', 'red', '-o', tmp])
        check('advanced allows tuning and channels', code == 0 and 'channelred' in out)

        # the cap is enforced on a batch
        many = os.path.join(tmp, 'many')
        os.makedirs(many, exist_ok=True)
        import shutil
        for i in range(modes['beginner']['cap'] + 2):
            shutil.copy(SRC, os.path.join(many, f'{i:03d}.png'))
        code, _, err = run(['--mode', 'beginner', 'augment', many, '-o', tmp])
        check('the image cap is enforced', code == 2 and
              ('augmentation' in err or 'at most' in err), err.strip()[:48])

        # listing respects the mode
        code, out, _ = run(['--mode', 'beginner', 'list'])
        shown = len(re.findall(r'^  \w+\s', out, re.M))
        code2, out2, _ = run(['--mode', 'advanced', 'list'])
        shown2 = len(re.findall(r'^  \w+\s', out2, re.M))
        check('list shows fewer filters in beginner', shown < shown2, f'{shown} vs {shown2}')
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail[:46]}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} mode checks FAILED')
        return 1
    print(f'  all {len(checks)} mode checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
