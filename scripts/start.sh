#!/usr/bin/env bash
# Kept as a familiar entry point. The real launcher is run.sh.
#
# This used to run `npm start` in frontend/, which serves the unused React
# starter page on port 3000 -- the same port as the actual UI. The tool's UI is
# src/ui/advanced-index.html, served by src/ui/frontend_server.py. See DOCUMENTATION.md.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../run.sh" "$@"
