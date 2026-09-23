#!/usr/bin/env python3
"""imt — launcher for the command-line version.

The implementation lives in cli-version/imt/. This stays at the root so the
usage documented in v1.5.0 keeps working:

    ./imt.py list
    ./imt.py apply photo.jpg blur --set sigma=5
    ./imt.py augment photos/ -o out/

Run `./imt.py --help` for everything, including --mode, --log-level and -q.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cli-version'))

from imt.cli import _run  # noqa: E402

if __name__ == '__main__':
    sys.exit(_run())
