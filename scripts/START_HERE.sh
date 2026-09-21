#!/usr/bin/env bash
# Image Mutation Tool - orientation. Prints what to run; changes nothing.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cat <<EOF

==========================================
   Image Mutation Tool - Start Here
==========================================

The tool is two processes:

  src/backend/app.py         Flask API on :5000, applies mutations via ImageMagick
  src/ui/frontend_server.py  serves the UI on :3000

The UI is the single file src/ui/advanced-index.html (32 filters: 16 continuous sliders
+ 16 discrete choices, which augmentation expands to 107 configurations per
image). It has three experience modes -- Beginner, Intermediate, Advanced. The React project under frontend/ is an unused starter from the first
design pass -- 'npm start' serves that placeholder on the same port and will
look like the tool is broken. Don't use it.

FIRST TIME
==========================================

  cd $HERE
  ./setup.sh          # virtualenv, dependencies, environment check

RUN IT
==========================================

  ./run.sh                    # localhost only
  ./run.sh --host 0.0.0.0     # also reachable from your network

  Then open:  http://localhost:3000
  Ctrl+C stops both servers.

CHECK THE ENVIRONMENT
==========================================

  python3 doctor.py

  Verifies Python, dependencies, ports, and -- importantly -- that ImageMagick
  has the png/tiff/webp/freetype delegates. Without them those formats fall
  back to Pillow, which only approximates the ImageMagick operators. For
  research output that difference matters.

  Same information at runtime:
      curl -s http://localhost:5000/api/health

  'pil_fallback_formats' should be empty.

MANUAL START (if you prefer)
==========================================

  cd $HERE/backend && ../src/backend/venv/bin/python app.py
  cd $HERE && ./src/backend/venv/bin/python src/ui/frontend_server.py

  run.sh also exports MAGICK_HOME from src/backend/.env, which selects the
  ImageMagick build. Starting by hand without it may silently use a different
  one.

READ NEXT
==========================================

  DOCUMENTATION.md              everything: setup, filters, API, troubleshooting
  docs/reports/                      technical report, research paper, repair record
  tests/run_all.sh              the three validation suites
  ../misc/                      everything the tool does not need (see ../misc/README.md)
  rollback.sh                   switch between the single- and three-mode interface

==========================================

EOF
