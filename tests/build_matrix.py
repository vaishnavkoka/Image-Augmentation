#!/usr/bin/env python3
"""Run the catalogue against more than one ImageMagick build.

Every suite until now ran against one ImageMagick: a purpose-built one with all
delegates, named by MAGICK_HOME. That is not what a new user has. On a stock
build two thirds of the catalogue is unavailable and `median` killed the server
outright -- a one-request denial of service that twelve green suites could not
see, because none of them ever ran on that build.

This starts the backend once per build, asks every operator to run, and reports
what each build supports. Missing operators are a fact about the build and do
not fail the run. A build that takes the server down does fail it, because after
the worker-process change nothing should be able to do that any more.

    tests/build_matrix.py                      configured build + system build
    tests/build_matrix.py --builds a,b,system  specific ones
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, 'oracle_source.png')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def env_magick_home():
    envfile = os.path.join(ROOT, 'src', 'backend', '.env')
    if not os.path.exists(envfile):
        return None
    for line in open(envfile):
        line = line.strip()
        if line.startswith('MAGICK_HOME='):
            return os.path.expanduser(line.split('=', 1)[1].strip().strip('"\''))
    return None


def start_backend(magick_home, port, python):
    env = dict(os.environ)
    env['FLASK_PORT'] = str(port)
    env['ALLOW_DEGRADED'] = '1'
    if magick_home:
        env['MAGICK_HOME'] = magick_home
        env['LD_LIBRARY_PATH'] = os.path.join(magick_home, 'lib') + ':' + env.get('LD_LIBRARY_PATH', '')
    else:
        # Not pop: app.py calls load_dotenv(), which would then supply
        # MAGICK_HOME from src/backend/.env and quietly test the configured build
        # twice. An empty value is already "set", so dotenv leaves it alone.
        env['MAGICK_HOME'] = ''
        env['LD_LIBRARY_PATH'] = ''
    proc = subprocess.Popen([python, 'app.py'], cwd=os.path.join(ROOT, 'src', 'backend'),
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    for _ in range(80):
        if proc.poll() is not None:
            return None, base
        try:
            subprocess.run(['curl', '-fsS', '-m', '1', base + '/api/health'],
                           capture_output=True, check=True)
            return proc, base
        except subprocess.CalledProcessError:
            time.sleep(0.4)
    proc.kill()
    return None, base


def alive(base):
    return subprocess.run(['curl', '-fsS', '-m', '3', base + '/api/health'],
                          capture_output=True).returncode == 0


def probe(base):
    cat = json.loads(subprocess.run(['curl', '-s', '-m', '15', base + '/api/mutations'],
                                    capture_output=True, text=True).stdout)
    operators = {**cat['continuous'], **cat['discrete']}
    ok, unsupported, crashed = [], [], []
    for name, spec in sorted(operators.items()):
        params = {k: v['default'] for k, v in (spec.get('parameters') or {}).items()
                  if 'default' in v}
        out = subprocess.run(
            ['curl', '-s', '-m', '90', '-X', 'POST', base + '/api/mutate',
             '-F', 'image=@' + SRC, '-F', 'mutation=' + name,
             '-F', 'parameters=' + json.dumps(params)],
            capture_output=True, text=True).stdout
        if not alive(base):
            crashed.append(name)
            return ok, unsupported, crashed, len(operators), True
        try:
            reply = json.loads(out)
        except ValueError:
            unsupported.append(name)
            continue
        if 'result_url' in reply:
            ok.append(name)
        else:
            err = str(reply.get('error', ''))
            (crashed if 'killed' in err or 'SIGFPE' in err or 'SIGSEGV' in err
             else unsupported).append(name)
    return ok, unsupported, crashed, len(operators), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--builds', default='')
    a = ap.parse_args()

    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    python = os.path.join(ROOT, 'src', 'backend', 'venv', 'bin', 'python')
    if not os.path.exists(python):
        python = sys.executable

    if a.builds:
        builds = [None if b.strip() == 'system' else os.path.expanduser(b.strip())
                  for b in a.builds.split(',')]
    else:
        builds = [env_magick_home(), None]

    rows, died = [], False
    for mh in builds:
        label = mh if mh else 'system ImageMagick'
        port = free_port()
        proc, base = start_backend(mh, port, python)
        if proc is None:
            print(f'  {label}: backend would not start, skipped')
            continue
        try:
            ok, unsup, crash, total, aborted = probe(base)
            rows.append((label, len(ok), len(unsup), len(crash), total))
            if crash:
                died = died or aborted
                print(f'  {label}: CRASHING OPERATORS -> {", ".join(crash)}'
                      + ('  (server died, probe aborted)' if aborted else '  (contained)'))
        finally:
            proc.kill()
            proc.wait(timeout=10)

    print()
    print(f'  {"build":40} {"works":>6} {"unavailable":>12} {"crashes":>8}')
    for label, nok, nun, ncr, total in rows:
        short = label if len(label) <= 40 else '...' + label[-37:]
        print(f'  {short:40} {nok:>4}/{total:<3} {nun:>12} {ncr:>8}')
    print()
    if died:
        print('  FAIL: a build took the server down. Crash isolation is not working.')
        return 1
    if any(r[3] for r in rows):
        print('  Crashing operators were contained: the server stayed up and the')
        print('  request returned an error naming the operator.')
    print('  No build took the server down.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
