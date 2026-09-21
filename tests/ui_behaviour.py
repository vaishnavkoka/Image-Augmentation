#!/usr/bin/env python3
"""Interface behaviour that only a browser can check.

The other suites talk to the API, and the wiring suite reads the markup. Neither
can tell whether the mode caps actually stop an upload, whether the numeric boxes
refuse what they promise to refuse, or whether the theme toggle repaints
anything. Those run in a real page, so this suite drives one: headless Chrome
loads the interface, a script exercises it, and the results come back in the DOM.

Written after the Apply-path defects, where the API was correct throughout and
every fault lived in the interface.
"""
import html as htmlmod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, '..', 'src', 'ui', 'advanced-index.html')
PROBE = os.path.join(HERE, 'ui_behaviour.js')

CHROME = next((c for c in ('google-chrome', 'chromium', 'chromium-browser')
               if shutil.which(c)), None)


def main():
    if not CHROME:
        print('  no Chrome or Chromium on PATH, skipping browser behaviour suite')
        return 0

    html = open(UI).read()
    probe = open(PROBE).read()
    page = html.replace('</body>', '<script>' + probe + '</script></body>')

    tmp = tempfile.mkdtemp(prefix='uibehaviour-')
    try:
        path = os.path.join(tmp, 'page.html')
        with open(path, 'w') as fh:
            fh.write(page)

        dom = subprocess.run(
            [CHROME, '--headless', '--disable-gpu', '--no-sandbox',
             '--window-size=1440,900', '--virtual-time-budget=25000',
             '--dump-dom', 'file://' + path],
            capture_output=True, text=True, timeout=180).stdout

        # take the last match: the probe's own source is in the page too
        found = re.findall(r'<pre id="uiresults">(.*?)</pre>', dom, re.S)
        m = found[-1] if found else None
        if m is None:
            print('  the probe did not report — the page may have thrown before finishing')
            title = re.search(r'<title>(.*?)</title>', dom, re.S)
            if title:
                print('   page title was:', title.group(1)[:120])
            return 1

        results = json.loads(htmlmod.unescape(m))
        width = max(len(r['name']) for r in results)
        failed = [r for r in results if not r['ok']]
        for r in results:
            print(f'  {r["name"]:{width}}  {"PASS" if r["ok"] else "FAIL"}  {r["detail"][:60]}')
        print()
        if failed:
            print(f'  {len(failed)} of {len(results)} behaviour checks FAILED')
            return 1
        print(f'  all {len(results)} behaviour checks passed')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
