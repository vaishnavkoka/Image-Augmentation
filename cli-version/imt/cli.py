"""imt — the command line for the Image Mutation Tool.

Everything the web interface does, without a browser, against the same API:
one engine, one catalogue, one set of rules.

    imt health
    imt list [-v] [--filter TEXT]
    imt apply IMAGE MUTATION [--set name=value ...] [--channel red] [-o DIR]
    imt augment PATH -o DIR [--mode advanced] [--dry-run]
    imt compare IMAGE MUTATION --set ...      what the interface would send

Modes mirror the interface: Beginner runs filters at their defaults and takes
40 images, Advanced allows tuning, channels and augmentation up to 500.
"""
import argparse
import json
import os
import sys

from . import logs
from .client import Client
from .errors import (CODES, EXIT_OK, EXIT_PARTIAL, BadParameter, ImtError, ModeRefused,
                     MutationFailed, NoInput, PartialFailure, UnknownMutation)
from .grid import build as build_grid
from .guided import run as run_guided
from .modes import check_apply, check_augment, check_batch, load as load_modes
from .progress import bar

IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp')
DEFAULT_BASE = os.environ.get('TOOL_BASE', 'http://127.0.0.1:5000')


def parse_sets(pairs):
    params = {}
    for item in pairs or []:
        if '=' not in item:
            raise BadParameter(f'--set expects name=value, got {item!r}',
                               hint='for example --set sigma=5')
        k, v = item.split('=', 1)
        try:
            params[k] = int(v) if v.lstrip('-').isdigit() else float(v)
        except ValueError:
            params[k] = v
    return params


def images_under(path):
    if os.path.isfile(path):
        return [path]
    if not os.path.isdir(path):
        raise NoInput(f'no such file or directory: {path}')
    found = []
    for root, _d, files in os.walk(path):
        for f in sorted(files):
            if f.lower().endswith(IMAGE_SUFFIXES):
                found.append(os.path.join(root, f))
    return found


# ---- commands --------------------------------------------------------------

def cmd_guided(a, client, log):
    """Ask questions instead of requiring flags."""
    return run_guided(client, getattr(a, 'mode', 'advanced'), getattr(a, 'quiet', False))


def cmd_codes(a, client, log):
    """Every error code and what it means, so a message can be looked up."""
    groups = {'1': 'the request could not be performed',
              '2': 'the command line itself was wrong',
              '3': 'nothing to talk to',
              '4': 'finished, but not cleanly'}
    exit_for = {'1': 1, '2': 2, '3': 3, '4': 4}
    for digit, title in groups.items():
        print(f'\n{title}   (exit code {exit_for[digit]})')
        for code, meaning in sorted(CODES.items()):
            if code[1] == digit:
                print(f'  {code}  {meaning}')
    print()
    return EXIT_OK


def cmd_health(a, client, log):
    d = client.health()
    im = d['imagemagick']
    print(f"  status      : {d['status']}")
    print(f"  ImageMagick : {im['version'].split('https')[0].strip()}")
    print(f"  delegates   : {' '.join(im['delegates'])}")
    print(f"  native      : {' '.join(im['native_formats'])}")
    print(f"  PIL fallback: {' '.join(im['pil_fallback_formats']) or '(none)'}")
    print(f"  ICC profiles: {len(im['icc_profiles'])}")
    return EXIT_OK


def cmd_list(a, client, log):
    cat, raw = client.catalogue()
    if a.json:
        print(json.dumps(raw, indent=2))
        return EXIT_OK
    modes = load_modes()
    mode = modes[a.mode]
    print(f'{len(raw["continuous"])} continuous, {len(raw["discrete"])} discrete, '
          f'{len(cat)} operators   (mode: {a.mode})\n')
    for group in ('continuous', 'discrete'):
        offered = mode.get(group) or []
        print(f'{group}:')
        for name in sorted(raw[group]):
            if a.filter and a.filter.lower() not in name.lower():
                continue
            if offered and name not in offered:
                continue
            spec = raw[group][name]
            print(f"  {name:22}{spec.get('name', '')}")
            if a.verbose:
                print(f"      {spec.get('description', '')}")
                for key, p in (spec.get('parameters') or {}).items():
                    if p.get('type') == 'choice':
                        opts = p.get('options') or []
                        more = f' … {len(opts)} total' if len(opts) > 6 else ''
                        print(f"      {key}: {', '.join(map(str, opts[:6]))}{more}"
                              f"  (default {p.get('default')})")
                    else:
                        print(f"      {key}: {p.get('min')}..{p.get('max')} "
                              f"(default {p.get('default')})")
        print()
    return EXIT_OK


def _request_for(a, client, cat):
    """The mutation and parameters this invocation would send."""
    if a.mutation not in cat:
        near = [n for n in cat if a.mutation in n]
        raise UnknownMutation(f'no such mutation: {a.mutation}',
                              hint=f'did you mean: {", ".join(near[:5])}' if near else
                                   'run "imt list" to see the catalogue')
    params = parse_sets(a.set)
    if getattr(a, 'channel', None):
        params['channel'] = a.channel
    modes = load_modes()
    _, raw = client.catalogue()
    kind = 'continuous' if a.mutation in raw['continuous'] else 'discrete'
    # The mode check runs on what the CALLER asked for, before defaults are
    # filled in -- otherwise Beginner would reject its own defaults as tuning.
    check_apply(modes[a.mode], a.mode, kind, a.mutation, params, cat)

    # State every parameter, as the interface does. The interface reads each
    # control and sends its value whether or not the user touched it, so a CLI
    # that omits them relies on the server's default instead. The two agree
    # today and would part company the moment a default moved -- and the
    # parity harness caught exactly that on chop, where the interface sent
    # type=horizontal and the CLI sent nothing.
    for key, spec in (cat.get(a.mutation, {}).get('parameters') or {}).items():
        if key not in params and 'default' in spec:
            params[key] = spec['default']
    return a.mutation, params


def cmd_apply(a, client, log):
    cat, _ = client.catalogue()
    mutation, params = _request_for(a, client, cat)
    reply = client.mutate(a.image, mutation, params)
    dest = os.path.join(a.out, reply['download_filename'])
    client.download(reply['result_url'], dest)
    logs.record('INFO', 'applied %s %s to %s', mutation,
                json.dumps(params, sort_keys=True), a.image)
    print(dest)
    return EXIT_OK


def cmd_compare(a, client, log):
    """Print the request this invocation would send, for comparison with the UI."""
    cat, _ = client.catalogue()
    mutation, params = _request_for(a, client, cat)
    print(json.dumps({'mutation': mutation, 'parameters': params}, sort_keys=True))
    return EXIT_OK


def cmd_augment(a, client, log):
    modes = load_modes()
    mode = modes[a.mode]
    check_augment(mode, a.mode)
    sources = images_under(a.input)
    if not sources:
        raise NoInput(f'no images under {a.input}')
    check_batch(mode, a.mode, len(sources))

    cat, _ = client.catalogue()
    jobs = build_grid(cat)
    channel = getattr(a, 'channel', None)
    if channel and channel != 'all':
        if not mode['numeric']:
            raise ModeRefused(f'{a.mode} mode does not offer channel restriction',
                              hint='Use --mode advanced')
        jobs = [(m, dict(p, channel=channel)) for m, p in jobs]
    total = len(sources) * len(jobs)
    say = (lambda *_a, **_k: None) if a.quiet else print
    say(f'  {len(sources)} image(s) x {len(jobs)} configurations = {total} mutations'
        + (f'  (channel: {channel})' if channel and channel != 'all' else ''))
    if a.dry_run:
        for mutation, params in jobs:
            say(f'    {mutation} {params if params else ""}')
        return EXIT_OK

    done = failed = skipped = 0
    problems = []
    with bar(total, desc='augmenting', disable=a.quiet) as progress:
        for src in sources:
            stem = os.path.splitext(os.path.basename(src))[0]
            target = os.path.join(a.out, stem) if len(sources) > 1 else a.out
            for mutation, params in jobs:
                try:
                    reply = client.mutate(src, mutation, params)
                    client.download(reply['result_url'],
                                    os.path.join(target, reply['download_filename']))
                    done += 1
                except MutationFailed as e:
                    # With a channel selected, 76 of the 173 operators write to
                    # every plane whatever the mask says and refuse the request.
                    # That is the operator being honest, not a failure of the
                    # run, so it is skipped and counted separately.
                    if 'does not honour a channel' in str(e):
                        skipped += 1
                        logs.record('INFO', 'skipped (no channel support): %s', e)
                    else:
                        failed += 1
                        problems.append(str(e))
                        log.warning('%s', e)
                progress.update(1)

    say(f'  wrote {done} mutations to {a.out}'
        + (f', {skipped} skipped (no channel support)' if skipped else '')
        + (f', {failed} failed' if failed else ''))
    if failed:
        for p in problems[:5]:
            print(f'    {p}', file=sys.stderr)
        if len(problems) > 5:
            print(f'    … {len(problems) - 5} more, see the log', file=sys.stderr)
        raise PartialFailure(f'{failed} of {total} mutations failed')
    logs.record('INFO', 'augment: %d mutations from %d image(s)', done, len(sources))
    return EXIT_OK


# ---- entry point -----------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog='imt', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default=DEFAULT_BASE, help=f'API base (default {DEFAULT_BASE})')
    ap.add_argument('--mode', default='advanced', choices=('beginner', 'intermediate', 'advanced'),
                    help='mirror an interface mode (default advanced)')
    ap.add_argument('--log-level', default='WARNING',
                    choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'))
    ap.add_argument('--log-file', help='where to write the log')
    ap.add_argument('-q', '--quiet', action='store_true', help='no console output or progress bar')
    # The same options are accepted before or after the subcommand: writing
    # `imt augment x -o y -q` is the natural order, and argparse rejects it
    # unless the subparsers share the flags.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--base', default=argparse.SUPPRESS)
    common.add_argument('--mode', choices=('beginner', 'intermediate', 'advanced'),
                        default=argparse.SUPPRESS)
    common.add_argument('--log-level', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR'),
                        default=argparse.SUPPRESS)
    common.add_argument('--log-file', default=argparse.SUPPRESS)
    common.add_argument('-q', '--quiet', action='store_true', default=argparse.SUPPRESS)

    sub = ap.add_subparsers(dest='command', required=False)

    p = sub.add_parser('health', parents=[common]); p.set_defaults(func=cmd_health)
    p = sub.add_parser('guided', parents=[common],
                       help='answer questions instead of typing flags')
    p.set_defaults(func=cmd_guided)
    p = sub.add_parser('codes', parents=[common],
                       help='what each error code means'); p.set_defaults(func=cmd_codes)

    p = sub.add_parser('list', parents=[common])
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('--json', action='store_true')
    p.add_argument('--filter')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('apply', parents=[common])
    p.add_argument('image'); p.add_argument('mutation')
    p.add_argument('--set', action='append', metavar='NAME=VALUE')
    p.add_argument('--channel')
    p.add_argument('-o', '--out', default='.')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('compare', parents=[common])
    p.add_argument('image'); p.add_argument('mutation')
    p.add_argument('--set', action='append', metavar='NAME=VALUE')
    p.add_argument('--channel')
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser('augment', parents=[common])
    p.add_argument('input'); p.add_argument('-o', '--out', required=True)
    p.add_argument('--channel', help='restrict the whole grid to one colour plane')
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=cmd_augment)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if not getattr(a, 'command', None):
        # Bare `imt` used to print an argparse usage error, which helps nobody
        # who does not already know the commands. Start the guided mode.
        a.command = 'guided'
        a.func = cmd_guided
        for name, default in (('mode', 'advanced'), ('log_level', 'WARNING'),
                              ('log_file', None), ('quiet', False),
                              ('base', DEFAULT_BASE)):
            if not hasattr(a, name):
                setattr(a, name, default)
    log = logs.setup(a.log_level, a.log_file, a.quiet)
    a.quiet = getattr(a, 'quiet', False)
    client = Client(a.base)
    try:
        return a.func(a, client, log)
    except ImtError as e:
        print(e.render(), file=sys.stderr)
        if e.hint:
            print(f'{" " * (len(e.code) + 7)}{e.hint}', file=sys.stderr)
        logs.record('ERROR', '[%s] %s', e.code, e)   # file only: already shown
        return e.exit_code
    except KeyboardInterrupt:
        print('\nimt [E402]: interrupted', file=sys.stderr)
        return EXIT_PARTIAL
    except BrokenPipeError:
        # `imt list --json | head` closes the pipe early. That is the reader's
        # choice, not an error, and a traceback for it is noise -- which is
        # what it produced. Redirect the handle rather than closing it: closing
        # made the interpreter's own shutdown flush raise ValueError instead,
        # trading one traceback for another.
        _silence_stdout()
        return EXIT_OK


def _silence_stdout():
    """Point stdout at devnull so later writes and the shutdown flush are safe."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except Exception:
        pass


def _run():
    """Entry point that survives a closed pipe.

    Python also flushes stdout at shutdown, and if the reader has gone that
    flush raises again after main() returned cleanly. Redirecting the handle to
    devnull first is the documented way to keep `imt ... | head` quiet.
    """
    code = main()
    try:
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        _silence_stdout()
        code = EXIT_OK
    return code


if __name__ == '__main__':
    sys.exit(_run())
