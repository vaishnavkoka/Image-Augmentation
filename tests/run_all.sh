#!/usr/bin/env bash
# Validation suite. Requires the tool running (../run.sh).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
. "$HERE/../scripts/.venvpath.sh"
PY="${PY:-$(resolve_venv "$HERE/..")/bin/python}"
BASE="${TOOL_BASE:-http://localhost:5000}"
export TOOL_API="$BASE/api/mutate" TOOL_BASE="$BASE"
[ -f ../src/backend/.env ] && export MAGICK_HOME="$(grep -E '^\s*MAGICK_HOME=' ../src/backend/.env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ')"

if ! curl -fsS -m 3 "$BASE/api/health" >/dev/null 2>&1; then
    echo "No backend at $BASE. Start it with ../run.sh"; exit 1
fi

fail=0
echo "=============================================================="
echo " 1. Differential oracle — tool vs the ImageMagick command line"
echo "=============================================================="
$PY oracle_differential.py || fail=1
echo
echo "=============================================================="
echo " 2. Metamorphic oracle — properties with no reference needed"
echo "=============================================================="
$PY oracle_metamorphic.py || fail=1
echo
echo "=============================================================="
echo " 3. Adversarial input"
echo "=============================================================="
$PY adversarial.py || fail=1
echo
echo "=============================================================="
echo " 4. Smoke — every mutation still changes the image"
echo "=============================================================="
$PY smoke_all_mutations.py || fail=1
echo
echo "=============================================================="
echo " 5. Formats — every input format survives every filter class"
echo "=============================================================="
$PY oracle_formats.py || fail=1
echo
echo "=============================================================="
echo " 6. Edge cases — hostile content, exotic images, bounds, frames"
echo "=============================================================="
$PY edge_cases.py || fail=1
echo
echo "=============================================================="
echo " 7. Sub-options — every colorspace, grayscale method, profile, dither"
echo "=============================================================="
$PY oracle_suboptions.py || fail=1
echo
echo "=============================================================="
echo " 8. Defaults — the documented default is the one the UI sends"
echo "=============================================================="
$PY oracle_defaults.py || fail=1
echo
echo "=============================================================="
echo " 9. UI wiring — every control maps to a real catalogue entry"
echo "=============================================================="
$PY ui_wiring.py || fail=1
echo
echo "=============================================================="
echo " 10. Augmentation grid — every configuration, end to end"
echo "=============================================================="
$PY augmentation_grid.py || fail=1
echo
echo "=============================================================="
echo " 11. Download routes — single file, batch ZIP, traversal"
echo "=============================================================="
$PY download_paths.py || fail=1
echo
echo "=============================================================="
echo " 12. Interface behaviour — theme, numeric entry, upload caps"
echo "=============================================================="
$PY ui_behaviour.py || fail=1
echo
echo "=============================================================="
echo " 13. Build matrix — the catalogue on more than one ImageMagick"
echo "=============================================================="
$PY build_matrix.py || fail=1
echo
echo "=============================================================="
echo " 14. Channel restriction — -channel, and refusing to fake it"
echo "=============================================================="
$PY channel_restriction.py || fail=1
echo
echo "=============================================================="
echo " 15. Command line — parity with the interface"
echo "=============================================================="
$PY cli.py || fail=1
echo
echo "=============================================================="
echo " 16. cli-version — parity, modes, errors, logging"
echo "=============================================================="
bash "$HERE/../cli-version/tests/run.sh" || fail=1
echo
[ $fail -eq 0 ] && echo "ALL SUITES PASSED" || echo "SOME SUITES FAILED"
exit $fail
