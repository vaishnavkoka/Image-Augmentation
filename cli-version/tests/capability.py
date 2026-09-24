"""What can this ImageMagick actually do?

The suites assert things that hold on a complete ImageMagick: that
bilateral_blur exists, that a channel restriction works, that the grid produces
173 files. On a stock build none of those are true -- roughly half the catalogue
is missing and the Pillow fallback has no channel mask -- and a CI runner has a
stock build.

Asserting them there turns an environment fact into a red badge, which teaches
everyone to ignore the badge. So each suite asks these questions first and skips
what this build cannot do, while still failing on anything that is genuinely
wrong. The same distinction ui_wiring.py already makes.
"""
import json
import subprocess


def health(base):
    out = subprocess.run(['curl', '-s', '-m', '10', base + '/api/health'],
                         capture_output=True, text=True).stdout
    try:
        return json.load(__import__('io').StringIO(out))
    except ValueError:
        return {}


def is_degraded(base):
    """True when formats are falling back to Pillow, i.e. delegates are missing."""
    return bool(health(base).get('imagemagick', {}).get('pil_fallback_formats'))


def supports(base, image, mutation, params=None):
    """Can this build perform this mutation at all?"""
    out = subprocess.run(
        ['curl', '-s', '-m', '60', '-X', 'POST', base + '/api/mutate',
         '-F', 'image=@' + image, '-F', 'mutation=' + mutation,
         '-F', 'parameters=' + json.dumps(params or {})],
        capture_output=True, text=True).stdout
    try:
        return 'result_url' in json.loads(out)
    except ValueError:
        return False


def supports_channel(base, image):
    """Can this build restrict a mutation to one channel?

    The Pillow fallback has no channel mask, so on a build without the PNG
    delegate every channel request is refused however capable the operator is.
    """
    return supports(base, image, 'blur', {'sigma': 3, 'channel': 'red'})


def grid_size(base, image, jobs):
    """How many of these configurations this build can actually perform.

    The count is 173 on a complete ImageMagick and about 76 on a stock one, so a
    suite that asserts 173 is asserting the build, not the tool.
    """
    return sum(1 for mutation, params in jobs if supports(base, image, mutation, params))
