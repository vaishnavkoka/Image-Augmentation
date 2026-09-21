#!/usr/bin/env python3
"""The documented default and the default the interface sends must be the same.

`/api/mutations` is what a script reads to learn what a parameter defaults to.
The UI carries its own defaults in markup: a slider's `value` attribute and the
`checked` radio in each sub-option group. Nothing kept the two in step, and four
of nineteen had drifted -- so `blur` "at its default" meant sigma 5 to a script
and sigma 3 to anyone clicking Apply.

Grayscale was worse: three routes, three images. The UI sent Rec709Luma, the
catalogue advertised Rec601Luma, and omitting the parameter took a different
operator entirely (colorspace = 'gray'). Maximum pixel differences between the
three were 152, 172 and 27.
"""
import json
import os
import re
import subprocess
import sys

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, '..', 'src', 'ui', 'advanced-index.html')

# markup ids differ from API names for the multi-word filters
ID_FOR = {'rotational_blur': 'rotationalBlur',
          'black_threshold': 'blackThreshold',
          'gaussian_blur': 'gaussianBlur',
          'color_reduce': 'colorReduce',
          # Without these three the comparison silently skipped them: a
          # parameter whose slider id it cannot find is passed over, so a
          # drifted default would never have been reported.
          'sepia_tone': 'sepiaTone',
          'mode_filter': 'modeFilter',
          'liquid_rescale': 'liquidRescale'}


def main():
    cat = json.loads(subprocess.run(['curl', '-s', BASE + '/api/mutations'],
                                    capture_output=True, text=True).stdout)
    html = open(UI, encoding='utf-8').read()

    sliders = dict(re.findall(r'id="(\w+)Slider"[^>]*?value="([^"]*)"', html))
    checked = {}
    for m in re.finditer(r'name="([\w-]+)" value="([^"]+)"([^>]*)>', html):
        group, value, rest = m.groups()
        if 'checked' in rest:
            checked.setdefault(group, value)

    numeric = re.compile(r'^-?[\d.]+$')
    rows, bad, skipped = [], [], []
    for group in cat.values():
        for mut, spec in group.items():
            for pname, ps in (spec.get('parameters') or {}).items():
                api = ps.get('default')
                if api is None:
                    continue
                if ps.get('type') in ('int', 'float'):
                    ui = sliders.get(ID_FOR.get(mut, mut))
                else:
                    ui = next((checked[g] for g in
                               (f'{mut}-method', f'{mut}-type', f'{mut}-name', pname)
                               if g in checked), None)
                if ui is None:
                    skipped.append(f'{mut}.{pname}')
                    continue                     # no UI control for this one
                same = (float(api) == float(ui)
                        if numeric.match(str(api)) and numeric.match(str(ui))
                        else str(api) == str(ui))
                rows.append((f'{mut}.{pname}', str(api), str(ui), same))
                if not same:
                    bad.append(f'{mut}.{pname}: API says {api}, the UI sends {ui}')

    print(f'  {"parameter":30} {"API":>14} {"UI":>14}   agree')
    print('  ' + '-' * 70)
    for name, api, ui, same in sorted(rows):
        print(f'  {name:30} {api:>14} {ui:>14}   {"yes" if same else "NO"}')

    # and the three grayscale routes must land on the same image
    def grayscale(params):
        body = subprocess.run(
            ['curl', '-s', '-X', 'POST', BASE + '/api/mutate',
             '-F', 'image=@' + os.path.join(HERE, 'oracle_source.png'),
             '-F', 'mutation=grayscale', '-F', 'parameters=' + json.dumps(params)],
            capture_output=True, text=True).stdout
        url = json.loads(body)['result_url']
        return subprocess.run(['curl', '-s', url], capture_output=True).stdout

    declared = cat['discrete']['grayscale']['parameters']['method']['default']
    omitted = grayscale({})
    explicit = grayscale({'method': declared})
    routes_agree = omitted == explicit
    print(f'\n  grayscale with no method == grayscale with the declared '
          f'default ({declared}): {"yes" if routes_agree else "NO"}')
    if not routes_agree:
        bad.append('grayscale: omitting the method gives a different image '
                   'from sending the declared default')

    if skipped:
        print(f'\n  not compared (no matching UI control): {len(skipped)}')
        for x in skipped:
            print(f'    {x}')
    print(f'\n  {len(rows)} defaults compared, {len(bad)} mismatched')
    for b in bad:
        print('    ' + b)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
