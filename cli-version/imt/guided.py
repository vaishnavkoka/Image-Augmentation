"""Guided mode — the command line for someone who is not fluent in flags.

The web interface is easy and the one-shot command line is not: running `imt`
with no arguments used to print an argparse usage error, which helps nobody who
does not already know the commands.

This is deliberately not a REPL. A REPL is a faster loop for someone who
already knows what to type. This asks plain questions, does the work, and then
prints the command that would have done the same thing -- so the first run is
guided, the second is typed, and the third is in a script. It teaches its way
out of a job.

It adds no logic of its own: the catalogue, the grid and the modes come from the
same modules the rest of the command line uses, so there is nothing here that
can drift from the interface.
"""
import os
import sys

from .errors import EXIT_OK, ImtError
from .grid import build as build_grid
from .modes import load as load_modes
from .progress import bar

IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp')


# ---- asking ----------------------------------------------------------------

def ask(question, default=None, options=None):
    while True:
        suffix = f' [{default}]' if default is not None else ''
        try:
            answer = input(f'{question}{suffix}: ').strip()
        except EOFError:
            raise KeyboardInterrupt
        if not answer and default is not None:
            answer = str(default)
        if not answer:
            print('  a value is needed')
            continue
        if options and answer.lower() not in [o.lower() for o in options]:
            print(f'  choose one of: {", ".join(options)}')
            continue
        return answer


def yes_no(question, default='yes'):
    return ask(question + ' (yes/no)', default=default).lower() in ('y', 'yes')


def pick(items, prompt='Number', columns=3):
    """A numbered menu. 76 filters is too many to type blind."""
    for i, name in enumerate(items, 1):
        end = '\n' if i % columns == 0 else ''
        print(f'  {i:3}. {name:26}', end=end)
    if len(items) % columns:
        print()
    while True:
        raw = ask(f'\n{prompt} (1-{len(items)}), or part of a name')
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        matches = [n for n in items if raw.lower() in n.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # "blur" matches seven filters. Re-asking against the whole list is
            # useless; narrowing to what matched is the point of asking at all.
            print(f'\n  {len(matches)} contain "{raw}":')
            for i, name in enumerate(matches, 1):
                print(f'    {i}. {name}')
            choice = ask('  Number')
            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                return matches[int(choice) - 1]
            print('  not a number from that list — starting again')
        else:
            print(f'  nothing matches "{raw}"')


# ---- the parts of the conversation -----------------------------------------

def choose_input():
    while True:
        path = os.path.expanduser(ask('Which image, or which folder of images'))
        if not os.path.exists(path):
            print(f'  not found: {path}')
            continue
        if os.path.isdir(path):
            images = [os.path.join(path, f) for f in sorted(os.listdir(path))
                      if f.lower().endswith(IMAGE_SUFFIXES)]
            if not images:
                print(f'  no images in {path}')
                continue
            print(f'  folder: {len(images)} image(s)')
            return path, images
        print('  file: 1 image')
        return path, [path]


def choose_filter(raw):
    """Continuous and discrete are listed separately, as the interface does.

    A flat list of 76 hides the distinction that decides whether there is
    anything to tune.
    """
    print('\nFilters come in two kinds:')
    print(f'  1. Continuous — {len(raw["continuous"])} filters with a value you can set')
    print(f'  2. Discrete   — {len(raw["discrete"])} filters that either run or do not')
    kind = ask('Choose 1 or 2', default='1', options=['1', '2'])
    group = 'continuous' if kind == '1' else 'discrete'
    names = sorted(raw[group])
    print(f'\n{len(names)} {group} filters:')
    return pick(names), group


def choose_parameters(spec, tunable):
    """Ask for each parameter, showing its range and its default."""
    params = {}
    entries = (spec.get('parameters') or {})
    if not entries:
        print('  this filter takes no settings')
        return params
    if not tunable:
        print('  this mode runs filters at their defaults, so nothing to set')
        return params

    print(f'\n{len(entries)} setting(s) for this filter. '
          f'Press Enter to accept the default shown.')
    for key, p in entries.items():
        description = p.get('description', '')
        if p.get('type') == 'choice':
            options = [str(o) for o in (p.get('options') or [])]
            print(f'\n  {key} — {description}   ({len(options)} options)')
            if len(options) <= 12:
                params[key] = pick(options, prompt='  Number', columns=4)
            else:
                params[key] = pick(options, prompt='  Number', columns=3)
        else:
            lo, hi, default = p.get('min'), p.get('max'), p.get('default')
            print(f'\n  {key} — {description}')
            while True:
                value = ask(f'  Value between {lo} and {hi}', default=default)
                try:
                    number = float(value)
                except ValueError:
                    print('  that is not a number')
                    continue
                if lo is not None and number < float(lo):
                    print(f'  below the minimum ({lo})')
                    continue
                if hi is not None and number > float(hi):
                    print(f'  above the maximum ({hi})')
                    continue
                params[key] = int(number) if float(number).is_integer() else number
                break
    return params


def choose_channel(catalogue, mutation, allowed):
    if not allowed:
        return None
    print('\nRestrict the filter to one colour plane?')
    print('  Most filters can. Some write to every plane whatever you choose,')
    print('  and those will say so rather than pretend.')
    if not yes_no('Restrict to one channel', default='no'):
        return None
    return pick(['red', 'green', 'blue', 'alpha'], prompt='  Number', columns=4)


# ---- running ---------------------------------------------------------------

def run_one(client, sources, mutation, params, out, quiet=False):
    written, failed = 0, []
    with bar(len(sources), desc=f'applying {mutation}', disable=quiet or len(sources) == 1) as p:
        for src in sources:
            try:
                reply = client.mutate(src, mutation, params)
                dest = os.path.join(out, reply['download_filename'])
                client.download(reply['result_url'], dest)
                written += 1
                if len(sources) == 1:
                    print(f'  {dest}')
            except ImtError as e:
                failed.append(f'[{e.code}] {e}')
            p.update(1)
    return written, failed


def run_grid(client, sources, jobs, out, channel=None, quiet=False):
    written = skipped = 0
    failed = []
    total = len(sources) * len(jobs)
    with bar(total, desc='augmenting', disable=quiet) as p:
        for src in sources:
            stem = os.path.splitext(os.path.basename(src))[0]
            target = os.path.join(out, stem) if len(sources) > 1 else out
            for mutation, params in jobs:
                try:
                    reply = client.mutate(src, mutation, params)
                    client.download(reply['result_url'],
                                    os.path.join(target, reply['download_filename']))
                    written += 1
                except ImtError as e:
                    if 'does not honour a channel' in str(e):
                        skipped += 1
                    else:
                        failed.append(f'[{e.code}] {e}')
                p.update(1)
    return written, skipped, failed


# ---- the whole conversation ------------------------------------------------

def run(client, mode_name='advanced', quiet=False):
    print('\nImage Mutation Tool — guided mode')
    print('A few questions, then it does the work and shows you the command')
    print('that would have done the same thing.\n')

    catalogue, raw = client.catalogue()
    modes = load_modes()
    mode = modes.get(mode_name, modes['advanced'])

    path, sources = choose_input()

    print('\nWhat would you like to do?')
    print('  1. Apply one filter')
    print(f'  2. Apply every filter — {len(build_grid(catalogue))} results per image')
    action = ask('Choose 1 or 2', default='1', options=['1', '2'])

    out = os.path.expanduser(ask('Where should the results go', default='./results'))
    os.makedirs(out, exist_ok=True)

    if action == '1':
        mutation, _group = choose_filter(raw)
        spec = catalogue[mutation]
        print(f'\n{mutation} — {spec.get("description", "")}')
        params = choose_parameters(spec, mode['tune'])
        channel = choose_channel(catalogue, mutation, mode['numeric'])
        if channel:
            params['channel'] = channel

        print(f'\nApplying {mutation} to {len(sources)} image(s)…')
        written, failed = run_one(client, sources, mutation, params, out, quiet)
        print(f'\nDone — {written} file(s) in {out}'
              + (f', {len(failed)} failed' if failed else ''))
        for f in failed[:3]:
            print(f'  {f}')
        sets = ' '.join(f'--set {k}={v}' for k, v in params.items() if k != 'channel')
        chan = f' --channel {params["channel"]}' if 'channel' in params else ''
        print('\nThe command that does this directly:')
        print(f'  ./imt.py apply {path} {mutation} {sets}{chan} -o {out}'.replace('  ', ' '))
        return EXIT_OK

    jobs = build_grid(catalogue)
    channel = choose_channel(catalogue, None, mode['numeric'])
    if channel:
        jobs = [(m, dict(p, channel=channel)) for m, p in jobs]
    total = len(sources) * len(jobs)
    print(f'\nThat is {len(jobs)} filters x {len(sources)} image(s) = {total} results.')
    if channel:
        print(f'  Restricted to the {channel} channel. Filters that cannot do that')
        print('  will be skipped and counted.')
    if not yes_no('Go ahead', default='yes'):
        print('  stopped, nothing written')
        return EXIT_OK

    written, skipped, failed = run_grid(client, sources, jobs, out, channel, quiet)
    print(f'\nDone — {written} file(s) in {out}'
          + (f', {skipped} skipped (no channel support)' if skipped else '')
          + (f', {len(failed)} failed' if failed else ''))
    for f in failed[:3]:
        print(f'  {f}')
    chan = f' --channel {channel}' if channel else ''
    print('\nThe command that does this directly:')
    print(f'  ./imt.py augment {path} -o {out}{chan}')
    return EXIT_OK
