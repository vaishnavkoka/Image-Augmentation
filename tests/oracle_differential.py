#!/usr/bin/env python3
"""Differential oracle: the tool vs the ImageMagick command line.

The tool wraps ImageMagick through Wand and MagickCore. The `magick` CLI is the
reference implementation of the same operators, so for every filter whose
command-line equivalent is unambiguous we can demand pixel equality.

This is the strongest oracle available: it does not ask "did something change?"
but "did exactly the documented operator run?".

    python3 tests/oracle_differential.py
"""
import json, os, subprocess, sys, tempfile

API = os.environ.get('TOOL_API', 'http://localhost:5000/api/mutate')
MAGICK = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
if not os.path.isfile(MAGICK):
    MAGICK = 'magick'
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'oracle_source.png')

# filter -> (tool params, CLI arguments). PNG throughout so the comparison is
# lossless and no JPEG re-encoding noise enters.
CASES = [
    ('negate',          {},                       ['-negate']),
    ('flip',            {},                       ['-flip']),
    ('flop',            {},                       ['-flop']),
    ('normalize',       {},                       ['-normalize']),
    ('monochrome',      {},                       ['-type', 'Bilevel']),
    ('posterize',       {'levels': 4},            ['-posterize', '4']),
    ('colors',          {'colors': 8},            ['-colors', '8']),
    ('gamma',           {'gamma': 2.0},           ['-gamma', '2.0']),
    ('edge',            {'radius': 2},            ['-edge', '2']),
    ('emboss',          {'radius': 2},            ['-emboss', '2x1.0']),
    ('blur',            {'sigma': 5},             ['-blur', '2.0x5.0']),
    ('gaussian_blur',   {'radius': 6},            ['-gaussian-blur', '6.0x3.0']),
    ('median',          {'kernel': 5},            ['-statistic', 'median', '5x5']),
    ('paint',           {'radius': 4},            ['-paint', '4']),
    ('implode',         {'factor': 0.6},          ['-implode', '0.6']),
    ('rotational_blur', {'angle': 15},            ['-rotational-blur', '15']),
    ('black_threshold', {'percentage': 40},       ['-black-threshold', '40%']),
    ('rotate',          {'degrees': 90},          ['-background', 'white', '-rotate', '90']),
    ('border',          {'pixels': 12},           ['-bordercolor', 'gray(200)', '-border', '12']),
    ('grayscale',       {'method': 'Rec709Luma'}, ['-grayscale', 'Rec709Luma']),
    ('grayscale',       {'method': 'RMS'},        ['-grayscale', 'RMS']),
    ('grayscale',       {'method': 'Average'},    ['-grayscale', 'Average']),
    ('colorspace',      {'colorspace': 'Lab'},    ['-colorspace', 'Lab', '-set', 'colorspace', 'sRGB']),
    ('colorspace',      {'colorspace': 'YIQ'},    ['-colorspace', 'YIQ', '-set', 'colorspace', 'sRGB']),
    ('colorspace',      {'colorspace': 'Gray'},   ['-colorspace', 'Gray', '-set', 'colorspace', 'sRGB']),
    # Parameterless operators added from the DALL-E filter survey. Each was
    # checked against the binary first: these five run with no argument and
    # change the image, while -auto-orient, -flatten, -trim and -clamp were
    # rejected for changing nothing.
    ('auto_level',      {},                       ['-auto-level']),
    ('equalize',        {},                       ['-equalize']),
    ('auto_gamma',      {},                       ['-auto-gamma']),
    ('despeckle',       {},                       ['-despeckle']),
    ('enhance',         {},                       ['-enhance']),
    # Tunable operators. The CLI forms here are the ones the mutators implement;
    # -frame in particular needs an explicit -mattecolor, because Wand's default
    # differs from the command line's and put the two 63 quantum levels apart.
    ('sharpen',        {'sigma': 2},          ['-sharpen', '0x2']),
    ('unsharp',        {'sigma': 1.5},        ['-unsharp', '0x1.5+1.0+0.05']),
    ('solarize',       {'threshold': 50},     ['-solarize', '50%']),
    ('sepia_tone',     {'threshold': 80},     ['-sepia-tone', '80%']),
    ('tint',           {'percentage': 50},    ['-fill', 'red', '-tint', '50%']),
    ('swirl',          {'degrees': 90},       ['-swirl', '90']),
    ('wave',           {'amplitude': 10},     ['-wave', '10x60']),
    ('shear',          {'degrees': 10},       ['-background', 'white', '-shear', '10x0']),
    ('frame',          {'pixels': 10},        ['-mattecolor', '#BDBDBD', '-frame', '10x10']),
    ('mode_filter',    {'kernel': 3},         ['-statistic', 'mode', '3x3']),
    ('resize',         {'percentage': 150},   ['-resize', '150%']),
    ('liquid_rescale', {'percentage': 80},    ['-liquid-rescale', '80%']),
    # The 2026-09 batch. Each was checked against its CLI form before being
    # added to the tool; -spread was tested and rejected because it displaces
    # pixels randomly and would break byte-reproducibility.
    ('adaptive_blur',      {'sigma': 3},        ['-adaptive-blur', '0x3']),
    ('adaptive_sharpen',   {'sigma': 3},        ['-adaptive-sharpen', '0x3']),
    ('blue_shift',         {'factor': 1.5},     ['-blue-shift', '1.5']),
    ('brightness_contrast',{'amount': 20},      ['-brightness-contrast', '20x20']),
    ('canny',              {'lower': 10},       ['-canny', '0x1+10%+30%']),
    ('kuwahara',           {'radius': 3},       ['-kuwahara', '3x1.5']),
    ('magnify',            {},                  ['-magnify']),
    ('modulate',           {'brightness': 120}, ['-modulate', '120,100,100']),
    ('motion_blur',        {'sigma': 5},        ['-motion-blur', '0x5+45']),
    ('ordered_dither',     {'threshold_map': 'o4x4'}, ['-ordered-dither', 'o4x4']),
    ('polaroid',           {'angle': 6},        ['-polaroid', '6']),
    ('roll',               {'pixels': 30},      ['-roll', '+30+0']),
    ('selective_blur',     {'sigma': 3},        ['-selective-blur', '0x3+10%']),
    ('shade',              {'azimuth': 120},    ['-shade', '120x30']),
    ('sigmoidal_contrast', {'strength': 5},     ['-sigmoidal-contrast', '5x50%']),
    ('transpose',          {},                  ['-transpose']),
    ('transverse',         {},                  ['-transverse']),
    ('vignette',           {'sigma': 10},       ['-vignette', '0x10+10+10']),
    ('wavelet_denoise',    {'threshold': 5},    ['-wavelet-denoise', '5%']),
    ('white_threshold',    {'percentage': 60},  ['-white-threshold', '60%']),

    # v1.3.0. bilateral_blur states intensity and spatial explicitly on both
    # sides: left to default, Wand and the CLI each compute their own and the
    # outputs differ by up to 5 levels. contrast_stretch and linear_stretch
    # take the clip fraction at BOTH ends -- Wand's white_point counts from the
    # top, as the CLI's second value does, and passing 1-f stretches the wrong
    # way entirely (255 levels out).
    ('bilateral_blur',     {'width': 5},        ['-bilateral-blur', '5x5+5+2.5']),
    ('contrast_stretch',   {'percentage': 5},   ['-contrast-stretch', '5%x5%']),
    ('linear_stretch',     {'percentage': 5},   ['-linear-stretch', '5%x5%']),
    ('level',              {'percentage': 10},  ['-level', '10%,90%']),
    ('threshold',          {'percentage': 50},  ['-threshold', '50%']),
    ('distort',            {'amount': 0.5},     ['-distort', 'Barrel', '0.0 0.0 0.5']),
    ('morphology',         {'method': 'Dilate'},['-morphology', 'Dilate', 'Diamond']),
]


def make_source():
    from PIL import Image, ImageDraw
    import numpy as np
    if os.path.exists(SRC):
        return
    w = h = 240
    y, x = np.mgrid[0:h, 0:w]
    a = np.stack([128 + 100*np.sin(x/30.0), 128 + 100*np.cos(y/34.0),
                  128 + 90*np.sin((x+y)/44.0)], -1)
    rng = np.random.default_rng(7)                    # fixed seed: reproducible
    a = np.clip(a + rng.normal(0, 5, a.shape), 0, 255).astype('uint8')
    im = Image.fromarray(a)
    d = ImageDraw.Draw(im)
    d.rectangle([30, 30, 100, 100], fill=(250, 250, 250), outline=(10, 10, 10), width=3)
    d.ellipse([140, 130, 220, 210], fill=(220, 40, 60), outline=(20, 20, 20), width=3)
    im.save(SRC)


def tool_result(mutation, params, out_path):
    r = subprocess.run(['curl', '-s', '-m', '60', '-X', 'POST', API,
                        '-F', f'image=@{SRC}', '-F', f'mutation={mutation}',
                        '-F', 'parameters=' + json.dumps(params)],
                       capture_output=True, text=True).stdout
    d = json.loads(r)
    if not d.get('success'):
        return d.get('error')
    blob = subprocess.run(['curl', '-s', '-m', '30', d['result_url']], capture_output=True).stdout
    open(out_path, 'wb').write(blob)
    return None


def compare(a, b):
    """Return (max abs channel difference, mean) between two images."""
    from PIL import Image
    import numpy as np
    ia, ib = Image.open(a).convert('RGB'), Image.open(b).convert('RGB')
    if ia.size != ib.size:
        return None, f'size {ia.size} vs {ib.size}'
    da = np.asarray(ia, int); db = np.asarray(ib, int)
    diff = np.abs(da - db)
    return (int(diff.max()), float(diff.mean())), None


def main():
    make_source()
    tmp = tempfile.mkdtemp(prefix='oracle_')
    rows, exact, close, bad = [], 0, 0, 0
    for i, (mut, params, cli) in enumerate(CASES):
        tool_png = os.path.join(tmp, f'{i}_tool.png')
        ref_png = os.path.join(tmp, f'{i}_ref.png')
        err = tool_result(mut, params, tool_png)
        if err:
            rows.append((mut, params, 'TOOL ERROR', err[:44])); bad += 1; continue
        p = subprocess.run([MAGICK, SRC] + cli + [ref_png], capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(ref_png):
            rows.append((mut, params, 'CLI ERROR', p.stderr.strip()[:44])); bad += 1; continue
        res, msg = compare(tool_png, ref_png)
        if res is None:
            rows.append((mut, params, 'MISMATCH', msg)); bad += 1; continue
        mx, mean = res
        if mx == 0:
            verdict, note = 'EXACT', 'identical to the CLI'; exact += 1
        elif mx <= 2:
            verdict, note = 'equivalent', f'max {mx}, mean {mean:.3f}'; close += 1
        else:
            verdict, note = 'DIVERGES', f'max {mx}, mean {mean:.2f}'; bad += 1
        rows.append((mut, params, verdict, note))

    label = lambda m, p: m + (' ' + ','.join(f'{k}={v}' for k, v in p.items()) if p else '')
    print(f"{'filter':32s} {'verdict':11s} note")
    print('-' * 88)
    for mut, params, verdict, note in rows:
        print(f"{label(mut,params):32s} {verdict:11s} {note}")
    print()
    print(f"  {len(CASES)} filters compared against the ImageMagick CLI")
    print(f"    exact pixel match : {exact}")
    print(f"    within tolerance  : {close}")
    print(f"    divergent / error : {bad}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
