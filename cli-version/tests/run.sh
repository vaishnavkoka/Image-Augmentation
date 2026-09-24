#!/usr/bin/env bash
# The cli-version suites. Requires the tool running (../../run.sh).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI_ROOT="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CLI_ROOT/.." && pwd)"
cd "$CLI_ROOT"
. "$ROOT/scripts/.venvpath.sh"
PY="${PY:-$(resolve_venv "$ROOT")/bin/python}"
# CI installs the dependencies with the system Python and never builds a
# virtualenv, so insisting on the venv path meant the suites did not run at all
# -- the job failed before the first check. imt.sh already had this fallback;
# the runners did not, which is one file disagreeing with another.
[ -x "$PY" ] || PY="$(command -v python3)"
[ -x "$PY" ] || { echo "No usable python found"; exit 1; }
BASE="${TOOL_BASE:-http://localhost:5000}"
export TOOL_BASE="$BASE" PYTHONPATH="$CLI_ROOT"

if ! curl -fsS -m 3 "$BASE/api/health" >/dev/null 2>&1; then
    echo "No tool at $BASE. Start it with ../run.sh"; exit 1
fi

fail=0
for suite in test_parity test_modes test_errors test_logs test_guided; do
    echo "=============================================================="
    case "$suite" in
        test_parity) echo " Parity — the CLI sends what the interface sends" ;;
        test_modes)  echo " Modes — Beginner, Intermediate, Advanced enforced" ;;
        test_errors) echo " Errors — every failure path and its exit code" ;;
        test_logs)   echo " Logging and progress" ;;
        test_guided) echo " Guided mode — usable without knowing the flags" ;;
    esac
    echo "=============================================================="
    "$PY" "tests/$suite.py" || fail=1
    echo
done
[ $fail -eq 0 ] && echo "CLI-VERSION SUITES PASSED" || echo "CLI-VERSION SUITES FAILED"
exit $fail
