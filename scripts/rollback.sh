#!/usr/bin/env bash
# Switch the interface between the single-mode v1.0 UI and the three-mode v1.1.
#
#   ./rollback.sh              show which one is live
#   ./rollback.sh --to-v1.0    go back to the single-mode UI
#   ./rollback.sh --to-v1.1    return to the three-mode UI
#
# Only advanced-index.html changes. The backend, the filters and every test are
# identical either way -- modes are a UI-side restriction on the same engine, so
# nothing you have already generated is affected by switching.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the tool root
cd "$HERE"

V10="scripts/rollback-versions/advanced-index.html.v1.0-no-modes"
V11="scripts/rollback-versions/advanced-index.html.v1.1-modes"

current() {
    if grep -q 'EXPERIENCE MODES' src/ui/advanced-index.html; then echo "v1.1 (three modes)";
    else echo "v1.0 (single mode)"; fi
}

case "${1:-}" in
--to-v1.0)
    [ -f "$V10" ] || { echo "ERROR: $V10 is missing."; exit 1; }
    cp src/ui/advanced-index.html "$V11"
    cp "$V10" src/ui/advanced-index.html
    echo "Interface is now v1.0 (single mode)."
    echo "Reload the browser. ./run.sh does not need restarting -- the UI is served from disk."
    ;;
--to-v1.1)
    [ -f "$V11" ] || { echo "ERROR: $V11 is missing."; exit 1; }
    cp src/ui/advanced-index.html "$V10"
    cp "$V11" src/ui/advanced-index.html
    echo "Interface is now v1.1 (three modes)."
    echo "Reload the browser."
    ;;
"")
    echo "Live now: $(current)"
    echo "  ./rollback.sh --to-v1.0    single-mode UI"
    echo "  ./rollback.sh --to-v1.1    three-mode UI"
    ;;
*)
    echo "unknown option: $1"; exit 2 ;;
esac
