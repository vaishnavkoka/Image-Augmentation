#!/usr/bin/env bash
# One-time setup for the Image Mutation Tool.
#   ./setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "Image Mutation Tool - setup"
echo

# --- python ---------------------------------------------------------------
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "ERROR: $PY not found. Install Python 3.10-3.13."; exit 1; }
echo "Using $($PY --version)"

# The pins in requirements.txt resolve on 3.10-3.13. Below 3.10 they
# do not exist at all. Check here so the failure is one clear line rather than a
# wall of pip resolver output.
"$PY" - <<'PYVER' || exit 1
import sys
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    print(f"ERROR: Python {major}.{minor} is too old — the pinned numpy, Pillow and")
    print("       python-dotenv all require 3.10 or newer.")
    print("       Use 3.12: PYTHON=python3.12 ./setup.sh")
    sys.exit(1)
if (major, minor) > (3, 13):
    print(f"WARNING: Python {major}.{minor} is newer than these pins were verified")
    print("         against (3.10-3.13). Install may fail; if it succeeds, run")
    print("         tests/run_all.sh before trusting the output.")
PYVER

. "$HERE/scripts/.venvpath.sh"
VENV_DIR="$(resolve_venv "$HERE")"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Creating virtualenv in ${VENV_DIR#$HERE/} ..."
    "$PY" -m venv "$VENV_DIR"
else
    echo "Virtualenv already present: ${VENV_DIR#$HERE/} ($("$VENV_DIR/bin/python" -V))"
fi

echo "Installing Python dependencies ..."
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -r requirements.txt
echo "Dependencies installed."

# --- config ---------------------------------------------------------------
if [ ! -f src/backend/.env ]; then
    cp src/backend/.env.example src/backend/.env
    echo "Created src/backend/.env from the example."
fi

# --- imagemagick ----------------------------------------------------------
echo
echo "Checking ImageMagick ..."
# Probe through src/backend/.env exactly as app.py does, so this reports the
# ImageMagick the tool will really bind to -- not whatever is on PATH.
if ! "$VENV_DIR/bin/python" - <<'PY' 2>/dev/null
from dotenv import load_dotenv
load_dotenv('src/backend/.env')   # setup.sh has already cd'd to its own directory
from wand.version import configure_options
d = set(configure_options('DELEGATES').get('DELEGATES', '').split())
raise SystemExit(0 if {'png', 'tiff', 'webp', 'freetype'} <= d else 1)
PY
then
    cat <<'MSG'

  NOTE: your ImageMagick is either missing entirely or missing one or more of
  the png/tiff/webp/freetype delegates.

  Without the library the backend will not start at all. With the library but
  without the delegates it runs, and those formats fall back to Pillow, which
  only approximates the ImageMagick operators -- fine for a preview, wrong if
  the output is research data.

  Run this to see what applies to this machine:

      ./install-imagemagick.sh

  It reports what is here, prints the package-manager command for this OS, and
  can do the no-root source build itself (--from-source).

MSG
fi

echo
"$PY" doctor.py || true
echo
echo "Setup done. Start the tool with:  ./run.sh"
