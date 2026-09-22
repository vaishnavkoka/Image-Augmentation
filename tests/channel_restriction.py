#!/usr/bin/env python3
"""-channel: restricting a mutation to one colour plane.

ImageMagick's -channel is a setting, not an operator: it masks which channels
the next operator may write. The tool does the same through
MagickSetImageChannelMask, so no operator needed per-operator plumbing.

The reason this suite exists is that the mask is not universal. `posterize`
quantises all three planes whatever the mask says, diverging from
`-channel G -posterize 4 +channel` by 250 levels. Honouring a request the
operator then ignores would return a whole-image mutation labelled as
channel-restricted, which is the silent-wrong-result shape this codebase has
produced before. So the tool measures support per operator and refuses the
rest, and this suite checks both halves of that.
"""
import io
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'oracle_source.png')
MAGICK = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
if not os.path.exists(MAGICK):
    MAGICK = 'magick'

# Each CLI form must match how the tool implements the operator, not the
# operator's most obvious spelling: blur is radius 0.4*sigma here, so
# `-blur 0x3` is a different operation and comparing against it reports a
# divergence that is the test's fault. That mistake has been made twice.
CASES = [
    ('blur',   {'sigma': 3},   'red',   ['-blur', '1.2x3']),
    ('blur',   {'sigma': 3},   'green', ['-blur', '1.2x3']),
    ('blur',   {'sigma': 3},   'blue',  ['-blur', '1.2x3']),
    ('negate', {},             'red',   ['-negate']),
    ('gamma',  {'gamma': 2.0}, 'blue',  ['-gamma', '2.0']),
    ('edge',   {'radius': 2},  'red',   ['-edge', '2']),
    ('sharpen', {'sigma': 2},  'green', ['-sharpen', '0x2']),
]

CHANNEL_FLAG = {'red': 'R', 'green': 'G', 'blue': 'B'}
PLANE = {'red': 0, 'green': 1, 'blue': 2}


def api(mutation, params):
    out = subprocess.run(
        ['curl', '-s', '-m', '90', '-X', 'POST', BASE + '/api/mutate',
         '-F', 'image=@' + SRC, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params)],
        capture_output=True, text=True).stdout
    try:
        d = json.loads(out)
    except ValueError:
        return None, 'unreadable reply'
    if 'result_url' not in d:
        return None, str(d.get('error', '?'))
    img = subprocess.run(['curl', '-s', d['result_url']], capture_output=True).stdout
    return np.asarray(Image.open(io.BytesIO(img)).convert('RGB'), np.int32), None


def cli(args, channel):
    out = os.path.join(os.path.dirname(SRC), '_chan_cli.png')
    subprocess.run([MAGICK, SRC, '-channel', CHANNEL_FLAG[channel]] + args
                   + ['+channel', out], check=True, capture_output=True)
    a = np.asarray(Image.open(out).convert('RGB'), np.int32)
    os.unlink(out)
    return a


def main():
    source = np.asarray(Image.open(SRC).convert('RGB'), np.int32)
    checks, fails = [], []

    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    for mutation, params, channel, args in CASES:
        got, err = api(mutation, dict(params, channel=channel))
        if err:
            check(f'{mutation} on {channel}', False, err[:60])
            continue
        want = cli(args, channel)
        same = got.shape == want.shape and int(np.abs(got - want).max()) == 0
        check(f'{mutation} on {channel} matches the CLI', same,
              '' if same else f'max diff {int(np.abs(got - want).max())}')

        # the other two planes must be untouched
        untouched = [p for name, p in PLANE.items() if name != channel]
        clean = all((got[:, :, p] == source[:, :, p]).all() for p in untouched)
        check(f'{mutation} on {channel} leaves the other planes alone', clean)

    # an operator that ignores the mask must be refused, not silently widened
    got, err = api('posterize', {'levels': 4, 'channel': 'green'})
    check('posterize refuses a channel restriction', got is None and err is not None,
          (err or 'was accepted')[:60])
    if err:
        check('the refusal explains itself', 'does not honour' in err)

    # an unknown channel name is rejected
    got, err = api('blur', {'sigma': 3, 'channel': 'purple'})
    check('an unknown channel is rejected', got is None, (err or 'accepted')[:50])

    # omitting channel still works, and equals channel=all
    plain, _ = api('blur', {'sigma': 3})
    everything, _ = api('blur', {'sigma': 3, 'channel': 'all'})
    check('channel=all is the same as no channel',
          plain is not None and everything is not None
          and int(np.abs(plain - everything).max()) == 0)

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f'  {name:{width}}  {"PASS" if ok else "FAIL"}  {detail}')
    print()
    if fails:
        print(f'  {len(fails)} of {len(checks)} channel checks FAILED')
        return 1
    print(f'  all {len(checks)} channel checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
