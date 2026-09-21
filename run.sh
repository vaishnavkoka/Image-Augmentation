#!/usr/bin/env bash
# Start the Image Mutation Tool (backend API + web UI).
#
#   ./run.sh                 both servers, localhost only
#   ./run.sh --host 0.0.0.0  reachable from other machines on the network
#   ./run.sh --allow-degraded  start even when ImageMagick is missing delegates
#
# Without those delegates the tool still runs, but PNG, TIFF and WebP are handled
# by PIL, which only approximates the ImageMagick operators -- and roughly two
# thirds of the catalogue is unavailable. For research that is not a warning, it
# is a different instrument, so startup stops unless you say otherwise.
#
# Ports come from src/backend/.env (FLASK_PORT, FRONTEND_PORT) or the environment,
# and default to 5000 and 3000. Port 5000 is worth overriding on macOS, where
# AirPlay Receiver holds it:
#
#   FLASK_PORT=5050 ./run.sh
#
# Ctrl+C stops both.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

BIND="127.0.0.1"
ALLOW_DEGRADED="${ALLOW_DEGRADED:-0}"
for arg in "$@"; do
    case "$arg" in
        --allow-degraded) ALLOW_DEGRADED=1 ;;
    esac
done
[ "${1:-}" = "--host" ] && BIND="${2:-0.0.0.0}"

. "$HERE/scripts/.venvpath.sh"
VENV_DIR="$(resolve_venv "$HERE")"
PY="$VENV_DIR/bin/python"
[ -x "$PY" ] || { echo "ERROR: no virtualenv. Run ./setup.sh first."; exit 1; }

# Pick up MAGICK_HOME from src/backend/.env so Wand binds to the intended
# ImageMagick build. Must be exported before the Python processes start.
if [ -f src/backend/.env ]; then
    MH=$(grep -E '^\s*MAGICK_HOME=' src/backend/.env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)
    if [ -n "${MH:-}" ]; then
        export MAGICK_HOME="${MH/#\~/$HOME}"
        export LD_LIBRARY_PATH="$MAGICK_HOME/lib:${LD_LIBRARY_PATH:-}"
    fi
fi

# Ports: environment wins, then src/backend/.env, then the defaults.
env_val() {
    [ -f src/backend/.env ] || return 0
    grep -E "^\s*$1=" src/backend/.env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true
}
API_PORT="${FLASK_PORT:-$(env_val FLASK_PORT)}";       API_PORT="${API_PORT:-5000}"
UI_PORT="${FRONTEND_PORT:-$(env_val FRONTEND_PORT)}";  UI_PORT="${UI_PORT:-3000}"
export FLASK_PORT="$API_PORT" FRONTEND_PORT="$UI_PORT"

cleanup() {
    echo
    echo "Stopping ..."
    [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# True when something is already listening on the port.
port_busy() {
    "$PY" - "$1" <<'PYEOF'
import socket, sys
s = socket.socket(); s.settimeout(0.4)
busy = s.connect_ex(('127.0.0.1', int(sys.argv[1]))) == 0
s.close()
sys.exit(0 if busy else 1)
PYEOF
}

# Refuse to start on an occupied port. Without this the readiness poll below
# would answer from whatever already owns the port and report "ready", while
# this script's own server had died with "Address already in use".
for port in "$API_PORT" "$UI_PORT"; do
    if port_busy "$port"; then
        echo "ERROR: port $port is already in use — the tool is probably running."
        echo
        echo "  Already running?   open http://localhost:$UI_PORT"
        echo "  Find the process:  lsof -i :$port    (or: ss -ltnp | grep :$port)"
        echo "  Stop it:           pkill -f 'app.py|frontend_server.py'"
        echo "  Or move the tool:  FLASK_PORT=5050 FRONTEND_PORT=3001 ./run.sh"
        exit 1
    fi
done

# Fidelity gate. A build without these delegates silently swaps PIL
# approximations in for the ImageMagick operators the catalogue names, which is
# the kind of thing that reaches a paper unnoticed.
MISSING="$("$PY" - <<'PYEOF'
import os, subprocess, sys
# Ask the build the tool will actually bind to. `magick` on PATH is often a
# different, thinner ImageMagick than the one MAGICK_HOME points at -- checking
# PATH here would have blocked startup on a machine whose configured build is
# complete.
home = os.environ.get('MAGICK_HOME', '')
binary = os.path.join(home, 'bin', 'magick') if home else ''
if not (binary and os.path.exists(binary)):
    binary = 'magick'
try:
    out = subprocess.run([binary, '-version'], capture_output=True, text=True,
                         timeout=15).stdout
except Exception:
    print('imagemagick'); sys.exit(0)
line = ''
for ln in out.splitlines():
    if ln.startswith('Delegates'):
        line = ln.split(':', 1)[1]
have = set(line.split())
need = {'png': 'png', 'tiff': 'tiff', 'webp': 'webp', 'freetype': 'freetype'}
print(' '.join(n for n, d in need.items() if d not in have))
PYEOF
)"

if [ -n "${MISSING// /}" ] && [ "$ALLOW_DEGRADED" != "1" ]; then
    echo "ERROR: this ImageMagick is missing delegates:$MISSING"
    echo
    echo "  Without them the tool falls back to PIL, which only approximates the"
    echo "  ImageMagick operators, and most of the catalogue becomes unavailable."
    echo "  Results produced this way are not the operators the documentation names."
    echo
    echo "  Fix it (no root needed):   ./scripts/install-imagemagick.sh"
    echo "  Already have a build?      set MAGICK_HOME in src/backend/.env"
    echo "  Understand and accept it:  ./run.sh --allow-degraded"
    exit 1
fi

echo "Image Mutation Tool"
echo "  ImageMagick: ${MAGICK_HOME:-system default}"
if [ -n "${MISSING// /}" ]; then
    echo "  DEGRADED:    missing delegates:$MISSING — PNG/TIFF/WebP are approximated by PIL"
    echo "               and much of the catalogue is unavailable. Results are NOT"
    echo "               the ImageMagick operators the documentation names."
fi
echo "  Ports:       API $API_PORT, UI $UI_PORT"
echo

# The backend binds to whatever BIND_HOST says; default is loopback only.
BIND_HOST="$BIND" bash -c 'cd src/backend && exec '"$PY"' app.py' &
BACKEND_PID=$!

# Wait for the API rather than sleeping a fixed amount. Also check our own
# child is still alive: a reachable port alone does not prove it is ours.
for _ in $(seq 1 40); do
    kill -0 "$BACKEND_PID" 2>/dev/null || break
    curl -fsS -m 1 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1 && break
    sleep 0.5
done

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "ERROR: the backend exited during startup. Run ./doctor.py to diagnose."
    cleanup
fi
if ! curl -fsS -m 2 "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then
    echo "ERROR: backend failed to start. Run ./doctor.py to diagnose."
    cleanup
fi
echo "  backend  ready on http://127.0.0.1:$API_PORT"

FRONTEND_BIND="$BIND" "$PY" src/ui/frontend_server.py &
FRONTEND_PID=$!

for _ in $(seq 1 20); do
    kill -0 "$FRONTEND_PID" 2>/dev/null || break
    curl -fsS -m 1 "http://127.0.0.1:$UI_PORT/" >/dev/null 2>&1 && break
    sleep 0.5
done

if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "ERROR: the frontend exited during startup (port $UI_PORT taken?)."
    cleanup
fi
if ! curl -fsS -m 2 "http://127.0.0.1:$UI_PORT/" >/dev/null 2>&1; then
    echo "ERROR: frontend failed to start (is port $UI_PORT already in use?)."
    echo "       lsof -i :$UI_PORT   to find out what holds it."
    cleanup
fi
echo "  frontend ready on http://127.0.0.1:$UI_PORT"
echo
echo "Open http://localhost:$UI_PORT  (Ctrl+C to stop)"

wait
