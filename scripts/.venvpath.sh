# Resolves which virtualenv the launchers use. Sourced by run.sh, setup.sh and
# tests/run_all.sh; doctor.py reimplements the same order.
#
#   $VENV                if you set it
#   src/backend/venv312      honoured if present, for anyone who built one by hand
#   src/backend/venv         what setup.sh creates -- the normal case
resolve_venv() {
    local here="$1"
    if [ -n "${VENV:-}" ]; then echo "$VENV"; return; fi
    for c in "$here/src/backend/venv312" "$here/src/backend/venv"; do
        [ -x "$c/bin/python" ] && { echo "$c"; return; }
    done
    echo "$here/src/backend/venv"
}
