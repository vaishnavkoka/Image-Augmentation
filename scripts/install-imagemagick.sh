#!/usr/bin/env bash
# Install an ImageMagick that this tool can actually use.
#
#   ./install-imagemagick.sh              check what is here, print what to run
#   ./install-imagemagick.sh --system     install via the system package manager (needs sudo)
#   ./install-imagemagick.sh --from-source  build 7.1.1-41 into ~/opt, no root needed
#
# The tool needs ImageMagick with the png, tiff, webp and freetype delegates.
# Without the shared library it will not start at all: Wand resolves
# libMagickWand at import time. With the library but without the delegates it
# starts and quietly routes those formats to Pillow, which only approximates the
# ImageMagick operators -- fine for a preview, wrong for research output.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PREFIX="${PREFIX:-$HOME/opt/imagemagick-7-full}"
IM_VERSION="${IM_VERSION:-7.1.1-41}"
WEBP_VERSION="${WEBP_VERSION:-1.3.2}"
NEEDED=(png tiff webp freetype)

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n%s\n%s\n' "$1" "$(printf '%*s' ${#1} '' | tr ' ' -)"; }

# --- what is here now -------------------------------------------------------
. "$HERE/.venvpath.sh"
VENV_PY="$(resolve_venv "$HERE")/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3 || true)"

# Ask Wand, not the `magick` binary: Wand is what the tool actually binds to,
# and the two can be different builds on the same machine.
probe() {
    "$VENV_PY" - <<'PY' 2>/dev/null || true
import os
try:
    from dotenv import load_dotenv
    load_dotenv('src/backend/.env')
except Exception:
    pass
try:
    from wand.version import MAGICK_VERSION, configure_options
except Exception:
    print('NOLIB')
    raise SystemExit(0)
print('OK')
print(MAGICK_VERSION)
print(configure_options('DELEGATES').get('DELEGATES', ''))
PY
}

head_ "Current ImageMagick"
mapfile -t P < <(probe)
STATE="${P[0]:-NOLIB}"

if [ "$STATE" = "OK" ]; then
    say "${P[1]}"
    have=" ${P[2]:-} "
    missing=()
    for d in "${NEEDED[@]}"; do [[ "$have" == *" $d "* ]] || missing+=("$d"); done
    if [ ${#missing[@]} -eq 0 ]; then
        say "delegates: png tiff webp freetype all present"
        echo
        say "Nothing to do. The tool has everything it needs."
        exit 0
    fi
    say "delegates MISSING: ${missing[*]}"
    say "the library loads, so the tool will run -- but those formats fall back to Pillow"
else
    say "no MagickWand shared library found -- the backend cannot start at all"
fi

# --- how to fix -------------------------------------------------------------
detect_cmd() {
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "brew install imagemagick"
    elif command -v apt-get >/dev/null; then
        echo "sudo apt-get install -y imagemagick libmagickwand-dev"
    elif command -v dnf >/dev/null; then
        echo "sudo dnf install -y ImageMagick ImageMagick-devel"
    elif command -v pacman >/dev/null; then
        echo "sudo pacman -S --noconfirm imagemagick"
    elif command -v zypper >/dev/null; then
        echo "sudo zypper install -y ImageMagick ImageMagick-devel"
    else
        echo ""
    fi
}
SYS_CMD="$(detect_cmd)"

MODE="${1:-}"
if [ -z "$MODE" ]; then
    head_ "What to run"
    if [ -n "$SYS_CMD" ]; then
        say "System package manager (needs sudo, quickest):"
        say "    $SYS_CMD"
        say "  or:  ./install-imagemagick.sh --system"
        echo
        say "A distribution package usually carries all four delegates, but not"
        say "always -- this machine's own apt build shipped only jpeg/ps/x/xml/zlib,"
        say "which is why the source route exists. Re-run this script afterwards to"
        say "check what you actually got."
    else
        say "No known package manager detected."
    fi
    echo
    say "No root, or the package is incomplete -- build 7.1.1-41 into $PREFIX:"
    say "    ./install-imagemagick.sh --from-source"
    say "  Takes roughly 10-20 minutes. Needs a compiler, make, cmake and curl,"
    say "  plus the png/tiff/freetype development headers. The script checks for"
    say "  them and stops with a list rather than failing halfway through."
    echo
    exit 1
fi

case "$MODE" in
--system)
    [ -n "$SYS_CMD" ] || { say "No known package manager. Use --from-source."; exit 1; }
    head_ "Installing via the system package manager"
    say "$SYS_CMD"
    echo
    $SYS_CMD
    echo
    say "Done. Re-run ./install-imagemagick.sh to check the delegates."
    ;;

--from-source)
    head_ "Checking build prerequisites"
    miss=()
    for t in gcc make cmake curl tar; do
        command -v "$t" >/dev/null || miss+=("$t")
    done
    # Header checks: pkg-config where available, else a compile probe.
    for pair in "libpng:png.h" "libtiff-4:tiffio.h" "freetype2:ft2build.h"; do
        pc="${pair%%:*}"
        if command -v pkg-config >/dev/null && pkg-config --exists "$pc" 2>/dev/null; then
            continue
        fi
        miss+=("$pc development headers")
    done
    if [ ${#miss[@]} -gt 0 ]; then
        say "missing: ${miss[*]}"
        echo
        if command -v apt-get >/dev/null; then
            say "On Debian/Ubuntu:"
            say "    sudo apt-get install -y build-essential cmake curl \\"
            say "        libpng-dev libtiff-dev libfreetype-dev libjpeg-dev libfontconfig-dev"
        elif [ "$(uname -s)" = "Darwin" ]; then
            say "On macOS:  xcode-select --install && brew install cmake libpng libtiff freetype jpeg"
        fi
        echo
        say "These need root. If you cannot get them, use the system package"
        say "manager route instead -- an incomplete ImageMagick still runs the"
        say "tool, with the fidelity caveat reported at startup."
        exit 1
    fi
    say "all present"

    SRC="$HOME/opt/src"
    mkdir -p "$SRC" "$PREFIX"

    head_ "Building libwebp $WEBP_VERSION"
    cd "$SRC"
    [ -d "libwebp-$WEBP_VERSION" ] || {
        curl -fsSL -o libwebp.tar.gz \
          "https://github.com/webmproject/libwebp/archive/refs/tags/v$WEBP_VERSION.tar.gz"
        tar xzf libwebp.tar.gz
    }
    cd "libwebp-$WEBP_VERSION"
    cmake -B build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DBUILD_SHARED_LIBS=ON >/dev/null
    make -C build -j"$(nproc 2>/dev/null || echo 4)" >/dev/null
    make -C build install >/dev/null
    say "installed into $PREFIX"

    head_ "Building ImageMagick $IM_VERSION"
    cd "$SRC"
    [ -d "ImageMagick-$IM_VERSION" ] || {
        curl -fsSL -o ImageMagick.tar.gz \
          "https://github.com/ImageMagick/ImageMagick/archive/refs/tags/$IM_VERSION.tar.gz"
        tar xzf ImageMagick.tar.gz
    }
    cd "ImageMagick-$IM_VERSION"
    # Q16-HDRI on purpose: Q16 and Q16-HDRI round differently, so mixing them
    # changes pixel values for operators such as edge and emboss. Keep this
    # matching whatever build your existing results were produced with.
    PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" \
    LDFLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib" \
    CPPFLAGS="-I$PREFIX/include" \
    ./configure --prefix="$PREFIX" \
        --with-quantum-depth=16 --enable-hdri \
        --with-png=yes --with-tiff=yes --with-webp=yes \
        --with-freetype=yes --with-fontconfig=yes --with-jpeg=yes \
        --without-x --disable-docs >/dev/null
    make -j"$(nproc 2>/dev/null || echo 4)" >/dev/null
    make install >/dev/null

    head_ "Result"
    "$PREFIX/bin/magick" -version | sed 's/^/  /'

    # --- point the tool at it ---------------------------------------------
    [ -f src/backend/.env ] || cp src/backend/.env.example src/backend/.env
    if grep -qE '^\s*MAGICK_HOME=' src/backend/.env; then
        sed -i.bak "s|^\s*MAGICK_HOME=.*|MAGICK_HOME=$PREFIX|" src/backend/.env && rm -f src/backend/.env.bak
    elif grep -qE '^\s*#\s*MAGICK_HOME=' src/backend/.env; then
        sed -i.bak "s|^\s*#\s*MAGICK_HOME=.*|MAGICK_HOME=$PREFIX|" src/backend/.env && rm -f src/backend/.env.bak
    else
        printf '\nMAGICK_HOME=%s\n' "$PREFIX" >> src/backend/.env
    fi
    echo
    say "src/backend/.env now points at $PREFIX"
    say "Check it with:  python3 doctor.py"
    ;;

*)
    say "unknown option: $MODE"
    exit 2
    ;;
esac
