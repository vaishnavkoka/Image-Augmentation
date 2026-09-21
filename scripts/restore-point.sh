#!/usr/bin/env bash
# Full-tree restore points, for changes that touch more than the interface.
#
#   ./scripts/restore-point.sh create "before worker process"
#   ./scripts/restore-point.sh list
#   ./scripts/restore-point.sh verify <name>     # does the snapshot match the tree?
#   ./scripts/restore-point.sh restore <name>    # go back
#
# rollback.sh swaps advanced-index.html between the single-mode and three-mode
# interface and nothing else. That is not enough once src/backend/app.py, run.sh and
# the suites change together, so this takes the whole source tree.
#
# Excluded, because they are large and regenerable: outputs/ (generated images),
# src/backend/venv (setup.sh rebuilds it), dist/ (package.sh rebuilds it), uploads/
# and __pycache__. Restoring never touches those, so a restore cannot lose
# images you have already generated.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
STORE="scripts/restore-points"
mkdir -p "$STORE"

EXCLUDES=(
    --exclude=./outputs
    --exclude=./uploads
    --exclude=./dist
    --exclude=./src/backend/venv
    --exclude=./src/backend/venv312
    --exclude=./scripts/restore-points
    --exclude=__pycache__
    --exclude='*.pyc'
    --exclude='.DS_Store'
)

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

case "${1:-}" in
create)
    label="$(echo "${2:-snapshot}" | tr ' /' '--' | tr -cd '[:alnum:]-_')"
    name="$(date +%Y%m%d-%H%M%S)-${label}"
    tar czf "$STORE/$name.tar.gz" "${EXCLUDES[@]}" -C "$HERE" . 2>/dev/null
    size="$(du -h "$STORE/$name.tar.gz" | cut -f1)"
    files="$(tar tzf "$STORE/$name.tar.gz" | wc -l)"
    echo "Restore point created:"
    echo "  $STORE/$name.tar.gz  ($size, $files entries)"
    echo
    echo "Go back with:  ./scripts/restore-point.sh restore $name"
    ;;
list)
    if ! ls "$STORE"/*.tar.gz >/dev/null 2>&1; then echo "No restore points yet."; exit 0; fi
    echo "Restore points, newest last:"
    for f in "$STORE"/*.tar.gz; do
        printf '  %-44s %6s  %s\n' "$(basename "$f" .tar.gz)" \
            "$(du -h "$f" | cut -f1)" "$(date -r "$f" '+%Y-%m-%d %H:%M')"
    done
    ;;
verify)
    name="${2:?which restore point? see: $0 list}"
    f="$STORE/${name%.tar.gz}.tar.gz"
    [ -f "$f" ] || { echo "No such restore point: $name"; exit 1; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    tar xzf "$f" -C "$tmp"
    differ=0
    while IFS= read -r rel; do
        if ! cmp -s "$tmp/$rel" "$rel" 2>/dev/null; then
            echo "  differs: $rel"; differ=$((differ+1))
        fi
    done < <(cd "$tmp" && find . -type f | sed 's|^\./||')
    if [ "$differ" -eq 0 ]; then
        echo "Snapshot $name matches the working tree exactly."
    else
        echo "$differ file(s) differ from snapshot $name."
        echo "That is expected if you have changed things since taking it."
    fi
    ;;
restore)
    name="${2:?which restore point? see: $0 list}"
    f="$STORE/${name%.tar.gz}.tar.gz"
    [ -f "$f" ] || { echo "No such restore point: $name"; exit 1; }
    # Never restore over unsaved work without keeping a copy of it first.
    safety="$(date +%Y%m%d-%H%M%S)-before-restore"
    tar czf "$STORE/$safety.tar.gz" "${EXCLUDES[@]}" -C "$HERE" . 2>/dev/null
    echo "Current state saved first as: $safety"
    tar xzf "$f" -C "$HERE"
    echo "Restored $name."
    echo
    echo "Restart the tool:   ./run.sh"
    echo "Undo this restore:  ./scripts/restore-point.sh restore $safety"
    ;;
*)
    usage
    ;;
esac
