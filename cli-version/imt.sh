#!/usr/bin/env bash
# Launcher: runs the CLI with the tool's own virtualenv, so it works without
# activating anything first.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
. "$ROOT/scripts/.venvpath.sh"
PY="$(resolve_venv "$ROOT")/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m imt.cli "$@"
