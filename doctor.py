#!/usr/bin/env python3
"""Preflight check for the Image Mutation Tool.

Reports whether this machine can run the tool at full fidelity, and says
exactly what to do about anything that is missing.

    python3 doctor.py
"""

import os
import shutil
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
def _resolve_venv_py():
    """Same order as scripts/.venvpath.sh: $VENV, then venv312, then venv."""
    if os.environ.get('VENV'):
        return os.path.join(os.environ['VENV'], 'bin', 'python')
    for name in ('venv312', 'venv'):
        cand = os.path.join(HERE, 'src', 'backend', name, 'bin', 'python')
        if os.access(cand, os.X_OK):
            return cand
    return os.path.join(HERE, 'src', 'backend', 'venv', 'bin', 'python')


VENV_PY = _resolve_venv_py()

OK, WARN, BAD = '  [ok]   ', '  [warn] ', '  [FAIL] '
problems, warnings = [], []


def head(title):
    print('\n' + title)
    print('-' * len(title))


def check_python():
    head('Python')
    print(f'{OK}interpreter {sys.version.split()[0]}')
    if os.path.exists(VENV_PY):
        ver = subprocess.run([VENV_PY, '-c', 'import sys; print(".".join(map(str, sys.version_info[:3])))'],
                             capture_output=True, text=True).stdout.strip()
        rel = os.path.relpath(os.path.dirname(os.path.dirname(VENV_PY)), HERE)
        print(f'{OK}backend virtualenv present: {rel} (Python {ver})')
        if tuple(int(x) for x in ver.split('.')[:2]) < (3, 10):
            print(f'{BAD}that virtualenv is below Python 3.10 -> rebuild with ./setup.sh')
            problems.append('python version')
    else:
        print(f'{BAD}backend virtualenv missing -> run ./setup.sh')
        problems.append('virtualenv')


def check_packages():
    head('Python packages')
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    for mod, label in [('flask', 'Flask'), ('flask_cors', 'Flask-CORS'),
                       ('wand', 'Wand'), ('PIL', 'Pillow'),
                       ('numpy', 'numpy'), ('dotenv', 'python-dotenv')]:
        r = subprocess.run([py, '-c', f'import {mod}'], capture_output=True)
        if r.returncode == 0:
            print(f'{OK}{label}')
        else:
            print(f'{BAD}{label} missing -> run ./setup.sh')
            problems.append(label)


def check_imagemagick():
    head('ImageMagick')
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    # Probe in a subprocess that loads src/backend/.env exactly the way app.py
    # does, so this reports the ImageMagick the tool will really bind to --
    # not whatever happens to be set in the current shell.
    probe = (
        'import os\n'
        'from dotenv import load_dotenv; load_dotenv(os.path.join(%r, "src", "backend", ".env"))\n'
        'from wand.version import MAGICK_VERSION, configure_options\n'
        'print(MAGICK_VERSION)\n'
        'print(configure_options("DELEGATES").get("DELEGATES", ""))\n'
        'print(os.environ.get("MAGICK_HOME", ""))\n'
    ) % HERE
    r = subprocess.run([py, '-c', probe], capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        print(f'{BAD}could not load ImageMagick through Wand')
        print('        ' + (r.stderr.strip().splitlines() or ['?'])[-1])
        print()
        print('        The backend cannot start without it -- Wand resolves the')
        print('        MagickWand shared library at import time. Fix it with:')
        print('            ./install-imagemagick.sh')
        problems.append('imagemagick')
        return

    lines = (r.stdout.strip().splitlines() + ['', '', ''])[:3]
    version, delegates, magick_home = lines
    delegates = set(delegates.split())
    print(f'{OK}{version}')
    print(f'         MAGICK_HOME = {magick_home or "(unset, using system default)"}')

    required = {'jpeg': 'JPEG', 'png': 'PNG', 'tiff': 'TIFF', 'webp': 'WebP',
                'freetype': 'text rendering (annotate)'}
    missing = {d: label for d, label in required.items() if d not in delegates}
    for d, label in required.items():
        if d in delegates:
            print(f'{OK}{label} delegate')
        else:
            print(f'{WARN}{label} delegate MISSING')
    if missing:
        warnings.append('delegates')
        print()
        print('         Formats without a delegate fall back to PIL, which only')
        print('         approximates the ImageMagick operators. For research use')
        print('         where the ImageMagick pipeline is the point, install a')
        print('         build with these delegates:')
        print('             ./install-imagemagick.sh')
        print('         which prints the options for this machine and can do the')
        print('         no-root source build itself. Background in DOCUMENTATION.md')
        print('         section 3.')


def check_fonts():
    head('Fonts (needed only by the annotate API)')
    magick = os.path.join(os.environ.get('MAGICK_HOME', ''), 'bin', 'magick')
    if not os.path.exists(magick):
        magick = shutil.which('magick') or shutil.which('convert')
    if not magick:
        print(f'{WARN}no magick binary on PATH; skipping font check')
        return
    r = subprocess.run([magick, '-list', 'font'], capture_output=True, text=True)
    n = r.stdout.count('Font:')
    if n > 1:
        print(f'{OK}{n} fonts available')
    else:
        print(f'{WARN}{n} font(s) - annotate will fail with "unable to read font"')
        warnings.append('fonts')


def _configured_ports():
    """Same precedence as run.sh: environment, then src/backend/.env, then defaults."""
    env = {}
    path = os.path.join(HERE, 'src', 'backend', '.env')
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip().strip('\'"')

    def pick(name, default):
        raw = os.environ.get(name) or env.get(name) or default
        try:
            return int(raw)
        except ValueError:
            print(f'{WARN}{name}={raw!r} is not a number; assuming {default}')
            return int(default)

    return pick('FLASK_PORT', 5000), pick('FRONTEND_PORT', 3000)


def check_ports():
    head('Ports')
    api_port, ui_port = _configured_ports()
    for port, what in [(api_port, 'backend API'), (ui_port, 'frontend')]:
        s = socket.socket()
        s.settimeout(0.4)
        busy = s.connect_ex(('127.0.0.1', port)) == 0
        s.close()
        if busy:
            print(f'{WARN}{port} ({what}) already in use - the tool may already be '
                  'running, or another process holds the port')
            warnings.append('ports in use')
        else:
            print(f'{OK}{port} ({what}) free')


def main():
    print('Image Mutation Tool - preflight check')
    check_python()
    check_packages()
    check_imagemagick()
    check_fonts()
    check_ports()

    head('Summary')
    if problems:
        print(f'{BAD}not ready: {", ".join(sorted(set(problems)))}')
        return 1
    if warnings:
        fidelity = sorted(set(warnings) - {'ports in use'})
        if fidelity:
            print(f'{WARN}runnable, with reduced fidelity: {", ".join(fidelity)}')
        if 'ports in use' in warnings:
            print(f'{WARN}ports in use - nothing wrong with the install. If that is')
            print(f'         the tool itself, open http://localhost:{_configured_ports()[1]}')
        return 0
    print(f'{OK}all checks passed - run ./run.sh')
    return 0


if __name__ == '__main__':
    sys.exit(main())
