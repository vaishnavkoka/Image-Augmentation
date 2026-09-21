#!/usr/bin/env python3
"""Every control the interface offers must be wired to something real.

This suite exists because of two defects that testing the API alone could never
have found, since the API was correct in both cases and the interface was not.

  1. The apply path named 16 filters explicitly in three hand-maintained tables
     while the interface had grown to 49 radio buttons. Selecting any of the
     other 33 showed no parameter panel and Apply answered "Select a continuous
     filter" -- the message for nothing being selected, when something was.

  2. The ICC profile radios were hardcoded, so when the bundled profile set grew
     they drifted: 13 profiles the backend could apply were unreachable, and one
     appeared twice under two spellings.

Both are the same failure: a second list that has to be updated by hand and was
not. So the assertions below compare the markup against `/api/mutations`, which
is the one list that cannot drift from the backend.
"""
import json
import os
import re
import subprocess
import sys

BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')
HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, '..', 'src', 'ui', 'advanced-index.html')


def camel(value):
    parts = re.split(r'[-_]', value)
    return parts[0] + ''.join(p.capitalize() for p in parts[1:])


def main():
    html = open(UI).read()
    cat = json.loads(subprocess.run(
        ['curl', '-s', '-m', '10', BASE + '/api/mutations'],
        capture_output=True, text=True).stdout)
    catalogue = {**cat['continuous'], **cat['discrete']}

    failures = []
    notes = []

    # --- every continuous filter is wired end to end -----------------------
    radios = re.findall(r'name="continuous-filter" value="([^"]+)"', html)
    print(f'  {len(radios)} continuous filters offered by the interface')
    for value in radios:
        sid = camel(value) + 'Slider'
        m = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(sid), html)
        if not m:
            failures.append(f'{value}: no slider #{sid} in the markup')
            continue
        tag = m.group(0)
        mut = re.search(r'data-mutation="([^"]+)"', tag)
        par = re.search(r'data-param="([^"]+)"', tag)
        if not mut or not par:
            failures.append(f'{value}: slider carries no data-mutation/data-param, '
                            f'so Apply cannot build a request for it')
            continue
        mut, par = mut.group(1), par.group(1)
        spec = catalogue.get(mut)
        if not spec:
            failures.append(f'{value}: data-mutation "{mut}" is not in the catalogue')
            continue
        params = spec.get('parameters') or {}
        if par not in params:
            failures.append(f'{value}: data-param "{par}" is not a parameter of {mut} '
                            f'(it has {sorted(params)})')
            continue
        p = params[par]
        if p.get('type') not in ('int', 'float'):
            failures.append(f'{value}: {mut}.{par} is {p.get("type")}, not numeric')
            continue
        # What matters is not whether the slider agrees with the declared range
        # but whether the values it can actually send are accepted. The declared
        # bounds are advisory: blur declares min 0.1 yet accepts 0, which the
        # metamorphic suite relies on to assert that blur(0) is the identity.
        # So probe the ends rather than compare numbers.
        for which in ('min', 'max'):
            edge = re.search(r'%s="([^"]+)"' % which, tag)
            if not edge:
                continue
            raw = float(edge.group(1))
            send = int(raw) if p['type'] == 'int' else raw
            body = subprocess.run(
                ['curl', '-s', '-m', '60', '-X', 'POST', BASE + '/api/mutate',
                 '-F', 'image=@' + os.path.join(HERE, 'oracle_source.png'),
                 '-F', 'mutation=' + mut,
                 '-F', 'parameters=' + json.dumps({par: send})],
                capture_output=True, text=True).stdout
            try:
                got = json.loads(body)
            except ValueError:
                failures.append(f'{value}: {mut}.{par}={send} gave an unreadable reply')
                continue
            if 'result_url' not in got:
                failures.append(f'{value}: the slider can send {par}={send}, '
                                f'which the backend rejects ({got.get("error", "?")[:60]})')
            declared = p.get(which)
            if declared is not None:
                out = raw < float(declared) if which == 'min' else raw > float(declared)
                if out:
                    notes.append(f'{value}: slider {which} {raw} is outside the declared '
                                 f'{which} {declared}, but the backend accepts it')

    wired = len(radios) - len([f for f in failures if 'data-mutation' in f or 'no slider' in f])
    print(f'  {wired} of {len(radios)} wired to a catalogue mutation and parameter')

    # --- every discrete filter is wired too --------------------------------
    # The continuous tab had 33 unwired filters; the discrete tab had 9, found
    # only after fixing the first. Both are checked here so neither can drift.
    disc = re.findall(r'<input[^>]*name="discrete-filter"[^>]*>', html)
    print(f'  {len(disc)} discrete filters offered by the interface')
    wired_d = 0
    for tag in disc:
        value = re.search(r'value="([^"]+)"', tag)
        value = value.group(1) if value else '?'
        if value == 'none':
            wired_d += 1
            continue
        mut = re.search(r'data-mutation="([^"]+)"', tag)
        if not mut:
            failures.append(f'discrete {value}: carries no data-mutation, so Apply '
                            f'cannot build a request for it')
            continue
        if mut.group(1) not in catalogue:
            failures.append(f'discrete {value}: data-mutation "{mut.group(1)}" '
                            f'is not in the catalogue')
            continue
        # a sub-option reference must name a parameter the operator really has
        radio = re.search(r'data-suboption-radio="([^"]+)"', tag)
        param = re.search(r'data-suboption-param="([^"]+)"', tag)
        if radio or param:
            if not (radio and param):
                failures.append(f'discrete {value}: has only half a sub-option reference')
                continue
            params = catalogue[mut.group(1)].get('parameters') or {}
            if param.group(1) not in params:
                failures.append(f'discrete {value}: sub-option param "{param.group(1)}" '
                                f'is not a parameter of {mut.group(1)}')
                continue
            if not re.search(r'<input[^>]*name="%s"' % re.escape(radio.group(1)), html):
                failures.append(f'discrete {value}: names radio group '
                                f'"{radio.group(1)}", which does not exist')
                continue
        wired_d += 1
    print(f'  {wired_d} of {len(disc)} wired to a catalogue mutation')

    # every discrete operator in the catalogue should be reachable
    offered = {re.search(r'data-mutation="([^"]+)"', t).group(1)
               for t in disc if 'data-mutation' in t}
    absent = [m for m in cat['discrete'] if m not in offered]
    if absent:
        notes.append(f'{len(absent)} catalogue discrete operator(s) not offered on the '
                     f'discrete tab: {absent} (reachable via the continuous tab or API)')

    # --- sub-option radios match the catalogue exactly ---------------------
    for mutation, param, radio in (('profile', 'profile', 'profile-name'),
                                   ('colorspace', 'colorspace', 'colorspace-type'),
                                   ('grayscale', 'method', 'grayscale-method'),
                                   ('ordered_dither', 'threshold_map', 'dither-map')):
        offered = re.findall(r'<input[^>]*name="%s"[^>]*value="([^"]+)"' % radio, html)
        spec = (catalogue.get(mutation, {}).get('parameters') or {}).get(param) or {}
        options = spec.get('options') or []
        # the interface may add a "no change" entry the catalogue has no name for
        extra = [o for o in offered if o.lower() not in {x.lower() for x in options}]
        missing = [o for o in options if o.lower() not in {x.lower() for x in offered}]
        dupes = [o for o in {x.lower() for x in offered}
                 if [x.lower() for x in offered].count(o) > 1]
        print(f'  {radio}: {len(offered)} radios, catalogue offers {len(options)}')
        if missing:
            failures.append(f'{radio}: {len(missing)} catalogue options unreachable '
                            f'from the interface: {missing[:6]}')
        if dupes:
            failures.append(f'{radio}: duplicated option(s) {dupes}')
        for e in extra:
            if e not in ('none', 'None', 'default'):
                failures.append(f'{radio}: offers "{e}", which the backend would reject')

    print()
    if notes:
        print(f'  {len(notes)} note(s), not failures:')
        for n in notes:
            print('    *', n)
        print()
    if failures:
        print(f'  {len(failures)} PROBLEM(S):')
        for f in failures:
            print('    -', f)
        return 1
    print('  every control is wired to a real catalogue entry, nothing unreachable')
    return 0


if __name__ == '__main__':
    sys.exit(main())
