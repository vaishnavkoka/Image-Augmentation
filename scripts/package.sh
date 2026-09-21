#!/usr/bin/env bash
# Build a deployable archive of the Image Mutation Tool.
#
#   ./package.sh                 -> dist/image-mutation-tool-<date>.tar.gz
#   ./package.sh --zip           -> also a .zip, for Windows targets
#   ./package.sh --with-reports  -> include docs/reports/ (13 MB of PDFs and figures)
#
# What comes out is the source plus setup.sh. It deliberately contains no
# virtualenv: the target machine builds its own, against its own Python and its
# own ImageMagick, which is the only way the Wand/MagickCore binding is correct
# there. Unpack it and run ./setup.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the tool root
cd "$HERE"

WITH_REPORTS=0
MAKE_ZIP=0
for arg in "$@"; do
    case "$arg" in
        --with-reports) WITH_REPORTS=1 ;;
        --zip)          MAKE_ZIP=1 ;;
        -h|--help)      sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

NAME="image-mutation-tool-$(date +%Y%m%d)"
STAGE="$(mktemp -d)"
DEST="$STAGE/$NAME"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$DEST"

# --- what ships -------------------------------------------------------------
# Everything needed to install and run, plus the validation suites, plus the
# filter reference the documentation cites.
FILES=(
    doctor.py
    run.sh
    setup.sh
    requirements.txt
)
DIRS=(src configs docs examples scripts tests bench assets)

for f in "${FILES[@]}"; do
    [ -e "$f" ] || { echo "ERROR: $f is missing — refusing to build an incomplete package."; exit 1; }
    cp -a "$f" "$DEST/"
done
for d in "${DIRS[@]}"; do
    [ -d "$d" ] || { echo "ERROR: $d/ is missing — refusing to build an incomplete package."; exit 1; }
    cp -a "$d" "$DEST/"
done

# The reference table the docs point at, name and all.
cp -a Master\ File\ -\ Magick\ Image\ filters*.csv "$DEST/" 2>/dev/null || true
cp -a Discrete\ and\ Continuous\ Filters.xlsx "$DEST/" 2>/dev/null || true

# --- backend, minus anything machine-specific -------------------------------
# src/ was copied wholesale above, so every backend module travels with it and
# there is no hand-written file list to fall out of step -- that list is how
# mutation_worker.py and worker_pool.py once shipped missing, leaving the target
# machine with no crash isolation at all. Prune what must not travel:
#   .env      carries this machine's MAGICK_HOME and DEBUG setting
#   venv      111 MB, and setup.sh rebuilds it on the target
rm -f  "$DEST/src/backend/.env"
rm -rf "$DEST/src/backend/venv" "$DEST/src/backend/venv312"
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# The two interface versions rollback.sh switches between.
# scripts/ carries rollback-versions/ already; keep only the v1.0 snapshot
find "$DEST/scripts/rollback-versions" -name '*v1.1*' -delete 2>/dev/null || true

# Restore points are this machine's history, not part of the product, and they
# are ~26 MB each -- they pushed the package from 34 MB to 60 MB.
rm -rf "$DEST/scripts/restore-points" 2>/dev/null || true

# docs/ travels with src/ now, but the reports are 18 MB of PDFs and figures
# and stay opt-in, as they were before they moved under docs/.
[ "$WITH_REPORTS" -eq 1 ] || rm -rf "$DEST/docs/reports"

# --- directories the tool writes into ---------------------------------------
# logs/ is not created: nothing in the tool writes to it. Logging goes to the
# terminal running run.sh, at the level LOG_LEVEL sets.
for d in uploads outputs; do
    mkdir -p "$DEST/$d"
    : > "$DEST/$d/.gitkeep"
done

# --- scrub ------------------------------------------------------------------
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name '*.pyc' -delete 2>/dev/null || true
find "$DEST" -name '.DS_Store' -delete 2>/dev/null || true

# Fail loudly rather than shipping a secret. This is the check that matters:
# an earlier version of the tool leaked src/backend/.env through the download API,
# and a package that carries it would leak it by construction.
if find "$DEST" -name '.env' -o -name 'venv' -o -name 'venv312' | grep -q .; then
    echo "ERROR: the staged package contains a .env or a virtualenv. Aborting."
    find "$DEST" -name '.env' -o -name 'venv' -o -name 'venv312'
    exit 1
fi

chmod +x "$DEST"/*.sh "$DEST"/doctor.py "$DEST"/tests/*.sh 2>/dev/null || true

# --- archives ---------------------------------------------------------------
mkdir -p dist
TAR="dist/$NAME.tar.gz"
tar -czf "$TAR" -C "$STAGE" "$NAME"
echo "  $TAR  ($(du -h "$TAR" | cut -f1))"

if [ "$MAKE_ZIP" -eq 1 ]; then
    ZIP="dist/$NAME.zip"
    rm -f "$ZIP"
    (cd "$STAGE" && zip -qr "$OLDPWD/$ZIP" "$NAME")
    echo "  $ZIP  ($(du -h "$ZIP" | cut -f1))"
fi

echo
echo "On the target machine:"
echo "  tar xzf $NAME.tar.gz && cd $NAME"
echo "  ./setup.sh          # builds the virtualenv, checks ImageMagick"
echo "  ./run.sh            # then open http://localhost:3000"
