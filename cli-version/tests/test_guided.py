#!/usr/bin/env python3
"""Guided mode: can someone who does not know the flags actually use it?

The web interface is easy and the command line was not -- bare `imt` printed an
argparse usage error. This suite checks the guided path end to end by feeding it
scripted answers, and checks the things that make it usable rather than merely
present: the two kinds of filter are separated, values are range-checked before
they reach the server, and every run ends by printing the command that would
have done the same thing.
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
sys.path.insert(0, HERE)
import capability as cap  # noqa: E402


def guided(answers, args=None, timeout=900):
    """Run guided mode with scripted answers on stdin."""
    env = dict(os.environ, PYTHONPATH=CLI_ROOT)
    return subprocess.run(
        [sys.executable, '-m', 'imt.cli', '--base', BASE] + (args or []),
        input=''.join(a + '\n' for a in answers),
        capture_output=True, text=True, cwd=CLI_ROOT, env=env, timeout=timeout)


def main():
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix='guided-')
    try:
        out = os.path.join(tmp, 'one')

        # bare `imt` must start the conversation, not print a usage error
        p = guided([SRC, '1', out, '1', 'gamma', '2.5', 'no'])
        check('bare imt starts guided mode',
              'guided mode' in p.stdout and 'error:' not in p.stderr,
              p.stderr.strip().splitlines()[0][:40] if p.stderr.strip() else '')
        check('it applied the filter', p.returncode == 0
              and any(f.startswith('oracle_source_gamma') for f in os.listdir(out)),
              ', '.join(os.listdir(out))[:40] if os.path.isdir(out) else 'no output')

        # the lesson: it prints the command that does the same thing
        check('it prints the equivalent command',
              'The command that does this directly' in p.stdout and 'imt.py apply' in p.stdout)
        check('the printed command carries the value chosen',
              '--set gamma=2.5' in p.stdout,
              [l for l in p.stdout.splitlines() if 'imt.py apply' in l][:1])

        # continuous and discrete are separated, as the interface separates them
        check('it separates continuous from discrete',
              'Continuous' in p.stdout and 'Discrete' in p.stdout)
        check('the counts match the catalogue',
              '55 filters' in p.stdout and '21 filters' in p.stdout,
              [l.strip() for l in p.stdout.splitlines() if 'filters' in l][:2])

        # a discrete filter has nothing to tune, and should say so
        out2 = os.path.join(tmp, 'discrete')
        p = guided([SRC, '1', out2, '2', 'negate', 'no'])
        check('a discrete filter runs without asking for a value',
              p.returncode == 0 and any('negate' in f for f in os.listdir(out2)),
              ', '.join(os.listdir(out2))[:36] if os.path.isdir(out2) else 'no output')

        # values are checked before they reach the server
        out3 = os.path.join(tmp, 'range')
        p = guided([SRC, '1', out3, '1', 'gamma', '999', '0.01', 'abc', '2.0', 'no'])
        check('a value above the maximum is refused', 'above the maximum' in p.stdout)
        check('a value below the minimum is refused', 'below the minimum' in p.stdout)
        check('a non-number is refused', 'that is not a number' in p.stdout)
        check('after three bad answers it still succeeds',
              p.returncode == 0 and os.path.isdir(out3) and len(os.listdir(out3)) == 1)

        # an ambiguous name narrows rather than restarting
        out4 = os.path.join(tmp, 'ambiguous')
        p = guided([SRC, '1', out4, '1', 'blur', '3', '4', 'no'])
        check('an ambiguous name narrows to the matches',
              'contain "blur"' in p.stdout)
        check('picking from the narrowed list works',
              p.returncode == 0 and os.path.isdir(out4) and len(os.listdir(out4)) == 1,
              ', '.join(os.listdir(out4))[:36] if os.path.isdir(out4) else 'no output')

        # a folder is recognised and every image processed
        folder = os.path.join(tmp, 'in')
        os.makedirs(folder, exist_ok=True)
        for i in range(3):
            shutil.copy(SRC, os.path.join(folder, f'{i}.png'))
        out5 = os.path.join(tmp, 'folder')
        p = guided([folder, '1', out5, '2', 'flip', 'no'])
        check('a folder is recognised', '3 image(s)' in p.stdout)
        check('every image in the folder is processed',
              os.path.isdir(out5) and len(os.listdir(out5)) == 3,
              f'{len(os.listdir(out5)) if os.path.isdir(out5) else 0} files')

        # the grid, with the count confirmed before it runs
        out6 = os.path.join(tmp, 'grid')
        p = guided([SRC, '2', out6, 'no', 'yes'])
        check('the grid states the total before running',
              'results.' in p.stdout and '173' in p.stdout)
        # 173 on a complete ImageMagick, about 76 on a stock one -- asserting the
        # exact number asserts the build rather than guided mode.
        produced = len(os.listdir(out6)) if os.path.isdir(out6) else 0
        check('the grid writes what this build supports', produced > 50,
              f'{produced} files')
        check('the grid prints its equivalent command', 'imt.py augment' in p.stdout)

        # declining does nothing
        out7 = os.path.join(tmp, 'declined')
        p = guided([SRC, '2', out7, 'no', 'no'])
        check('declining writes nothing',
              'nothing written' in p.stdout
              and (not os.path.isdir(out7) or not os.listdir(out7)))

        # a channel restriction, and the skip count it produces
        out8 = os.path.join(tmp, 'chan')
        p = guided([SRC, '2', out8, 'yes', '1', 'yes'])
        check('the grid can be restricted to a channel', 'red channel' in p.stdout)
        if cap.supports_channel(BASE, SRC):
            check('unsupported filters are skipped, not failed',
                  'skipped' in p.stdout and 'failed' not in p.stdout.split('Done')[-1],
                  p.stdout.split('Done')[-1].strip().splitlines()[0][:44]
                  if 'Done' in p.stdout else '')
        else:
            # With no channel mask on this build every configuration is refused,
            # so there is no skip/fail split to assert. That the question was
            # asked and the run completed is what matters here.
            print('  unsupported filters are skipped, not failed        SKIP  '
                  'this build cannot restrict a channel')

        # a bad path is handled, not crashed on
        p = guided([os.path.join(tmp, 'nope.png'), SRC, '1',
                    os.path.join(tmp, 'x'), '2', 'flip', 'no'])
        check('a missing path re-asks rather than failing',
              'not found' in p.stdout and p.returncode == 0)

        # interrupting cleanly
        p = guided([SRC])
        check('running out of answers exits cleanly',
              p.returncode in (0, 4) and 'Traceback' not in p.stderr,
              p.stderr.strip().splitlines()[-1][:40] if p.stderr.strip() else '')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {str(detail)[:40]}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} guided checks FAILED')
        return 1
    print(f'  all {len(checks)} guided checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
