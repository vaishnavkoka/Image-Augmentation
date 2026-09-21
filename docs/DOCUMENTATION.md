# Advanced Image Mutation Tool v1.0 — Documentation

Everything about this tool in one place. Replaces the 29 separate `.md` files that
accumulated during development; those are kept under `../misc/history/docs-archive/` for history,
but **many of them are wrong now** — six told you to run `npm start`, which serves an
unused React scaffold rather than the tool. Treat this file as the only current
source.

**Web interface:** <http://localhost:3000> · **API:** <http://localhost:5000>

---

## Contents

1. [What it does](#1-what-it-does)
2. [Quick start](#2-quick-start)
3. [Requirements and ImageMagick](#3-requirements-and-imagemagick)
4. [Using the interface](#4-using-the-interface)
5. [Filter catalogue](#5-filter-catalogue)
6. [Output filenames](#6-output-filenames)
7. [API reference](#7-api-reference)
8. [Validation](#8-validation)
9. [Configuration](#9-configuration)
10. [Layout of the project](#10-layout-of-the-project)
11. [Watching what the backend does](#11-watching-what-the-backend-does)
12. [Troubleshooting](#12-troubleshooting)
13. [Known limitations](#13-known-limitations)
14. [Deployment](#14-deployment)
15. [History](#15-history)

---

## 1. What it does

Applies ImageMagick filters to uploaded images through a browser, so mutated image
sets can be generated for classifier evaluation. It implements the future-scope
section of *Evaluation of the Impact of Image Mutations on the Origin Classification
of Digital Images*.

Two processes, no build step:

| Part | Port | What it is |
|---|---|---|
| `backend/app.py` | 5000 | Flask API; applies mutations via ImageMagick |
| `ui/frontend_server.py` | 3000 | serves `ui/advanced-index.html`, a single self-contained file |

**160 filter configurations per image** from a catalogue of **69 operators** (49
continuous + 20 discrete): 63 operators contribute one configuration each, plus 31
colorspaces, 9 grayscale methods, 50 ICC profile options and 7 ordered-dither maps.

---

## 2. Quick start

```bash
cd ~/RE4BDD/imagemagickwebtool
./setup.sh      # once: virtualenv, dependencies, environment check
./run.sh        # start both servers
```

Then open <http://localhost:3000>.

| Command | Purpose |
|---|---|
| `./run.sh` | start both servers (localhost only) |
| `./run.sh --host 0.0.0.0` | also reachable from other machines |
| `./start.sh` | alias for `run.sh` |
| `python3 doctor.py` | check the environment before running |
| `tests/run_all.sh` | run the three validation suites |
| `./START_HERE.sh` | print orientation, change nothing |

`run.sh` refuses to start if port 3000 or 5000 is already taken, and names what to do
about it.

> **Do not run `npm start`.** The `frontend/` directory is an unused React scaffold
> from the first design pass. It serves a placeholder page on the same port and will
> look like the tool is broken.

---

## 3. Requirements and ImageMagick

| | |
|---|---|
| Python | **3.12 recommended**; 3.10 – 3.13 supported. Below 3.10 the pinned numpy, Pillow and python-dotenv do not exist. |
| ImageMagick | 7.x **with the `png`, `tiff`, `webp` and `freetype` delegates** |
| Browser | any modern one |

### What you have to install yourself, and what the tool handles

Two things must already exist on the machine. Everything else the tool sets up.

| | Who installs it | If it is missing |
|---|---|---|
| **Python 3.10–3.13** | you | `setup.sh` stops with one line. Nothing can bootstrap an interpreter that is not there. |
| **ImageMagick** (shared library) | `./scripts/install-imagemagick.sh` will do it | the backend **will not start** — Wand resolves `libMagickWand` at import time and raises `ImportError` |
| The seven Python packages | `setup.sh` | — |
| The virtualenv | `setup.sh` | — |
| `backend/.env` | `setup.sh`, from `.env.example` | — |
| JSZip (the UI's only JS dependency) | already vendored in `vendor/` | — |
| `uploads/`, `outputs/` | created by `scripts/package.sh`, and at runtime | — |

`setup.sh` needs network access once, to download the Python packages from PyPI.
After that the tool runs entirely offline — the UI loads no CDN resources.

ImageMagick is the one real external dependency, and it is not pip-installable:

```bash
./scripts/install-imagemagick.sh                 # report what is here, print the options
./scripts/install-imagemagick.sh --system        # via apt/dnf/pacman/zypper/brew (needs sudo)
./scripts/install-imagemagick.sh --from-source   # build 7.1.1-41 into ~/opt, no root
```

The `--from-source` route checks for a compiler, make, cmake, curl and the
png/tiff/freetype headers **before** starting, and stops with a list rather than
failing twelve minutes in. It writes `MAGICK_HOME` into `backend/.env` when it
finishes. Those build headers themselves need root on most distributions, so a
machine with neither root nor a toolchain cannot get full fidelity — it will still
run, on the Pillow fallback, and say so at startup.

Python dependencies are in `requirements.txt` at the project root; `setup.sh` installs them into
`backend/venv`.

### Which virtualenv the launchers use

`run.sh`, `setup.sh`, `doctor.py` and `tests/run_all.sh` all resolve the same way:

1. `$VENV`, if you set it — `VENV=/path/to/env ./run.sh`
2. `backend/venv312`, if it exists
3. `backend/venv` — what `setup.sh` creates on a fresh machine

Step 2 exists only for this checkout: an old Python 3.10 `backend/venv` is committed
to the repository, so rebuilding it in place would appear as a 6,500-file diff. The
live environment therefore sits in `backend/venv312`. A fresh clone has no
`venv312` and resolves straight to `backend/venv`, with no special case. `doctor.py`
prints which one it picked and its Python version.

### Why the delegates matter

Wand talks to whatever ImageMagick it finds. A build compiled without the
`png`/`tiff`/`webp` delegates loads perfectly well and then refuses those formats at
read time, so the tool quietly routes them to a Pillow fallback that only
*approximates* the ImageMagick operators.

For casual use that is fine. If the mutated images are research data it is not — the
whole point is that the mutations come from ImageMagick. Check with:

```bash
magick -version | grep Delegates
python3 doctor.py
curl -s http://localhost:5000/api/health
```

`pil_fallback_formats` must be **empty**.

### Installing a complete ImageMagick

`./scripts/install-imagemagick.sh` automates everything below and picks the right branch for
the machine. This section is what it does, for when you would rather do it by hand.

**With root:**

```bash
sudo apt install imagemagick libmagickwand-dev   # Debian / Ubuntu
brew install imagemagick                         # macOS
```

**Without root** — this is what was done here, because the system build carried only
`jpeg ps x xml zlib`:

```bash
PREFIX=$HOME/opt/imagemagick-7-full
mkdir -p ~/opt/src && cd ~/opt/src

# libwebp, if libwebp-dev is not installed system-wide
curl -sL -o libwebp.tar.gz \
  https://github.com/webmproject/libwebp/archive/refs/tags/v1.3.2.tar.gz
tar xzf libwebp.tar.gz && cd libwebp-1.3.2
cmake -B build -DCMAKE_INSTALL_PREFIX=$PREFIX -DBUILD_SHARED_LIBS=ON
make -C build -j$(nproc) && make -C build install
cd ..

# ImageMagick itself
curl -sL -o ImageMagick.tar.gz \
  https://github.com/ImageMagick/ImageMagick/archive/refs/tags/7.1.1-41.tar.gz
tar xzf ImageMagick.tar.gz && cd ImageMagick-7.1.1-41

PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig \
LDFLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib" \
CPPFLAGS="-I$PREFIX/include" \
./configure --prefix=$PREFIX \
    --with-quantum-depth=16 --enable-hdri \
    --with-png=yes --with-tiff=yes --with-webp=yes \
    --with-freetype=yes --with-fontconfig=yes --with-jpeg=yes \
    --without-x --disable-docs

make -j$(nproc) && make install
$PREFIX/bin/magick -version | grep Delegates
```

Keep `--with-quantum-depth` and `--enable-hdri` matching whatever build your results
were produced with. Q16-HDRI and Q16 round differently, so mixing them changes pixel
values for operators such as `edge` and `emboss`.

Point the tool at it in `backend/.env`:

```ini
MAGICK_HOME=/home/youruser/opt/imagemagick-7-full
```

`run.sh` exports it before starting Python. **`app.py` loads the `.env` before
importing Wand**, because Wand resolves `MAGICK_HOME` at import time — moving that
import back above `load_dotenv()` silently undoes all of this.

---

## 4. Using the interface

The window is a three-column app shell that fills the viewport — **Sources**,
**Filter**, **Output** — with each column scrolling on its own. The three image panels
each carry their own colour, so they are told apart at a glance: **Uploaded images**
amber, **Originals** cyan, **Results** green. Below 1200px the
columns stack and the page scrolls normally.

1. **Upload** — click or drag. Any number of images; picking more *adds* to the set
   rather than replacing it, and files already loaded are skipped. *Clear all*
   starts over. The ✕ on a thumbnail removes just that image.
2. **Choose a filter** — Continuous (a slider) or Discrete (a fixed transform). Each
   continuous filter has its own colour so the control you are holding is
   identifiable.
3. **Apply Filter** — runs the selected filter over every uploaded image, four
   requests in flight, results in upload order. Progress shows elapsed time,
   throughput and an estimate; **Stop** cancels and keeps what finished.
4. **Augment — Apply All Filters** — runs *every* configuration over *every* image:
   100 variants per image. Ten images is 1000 files.
5. **Download results** — one image downloads directly; several are packed into a ZIP
   built in the browser. Entry names are de-collided if two sources share a name.

### Experience modes

The interface offers three levels of exposure over the same engine. Nothing here
changes what the backend does — a mode only decides which controls it puts in front
of you and how many images it will take at once.

| | Beginner | Intermediate | Advanced |
|---|---|---|---|
| Continuous filters | 8 | 33 (all) | 33 (all) |
| Discrete filters | 8 | 13 | 15 (all) |
| Sliders | fixed at their defaults | live | live |
| Sub-option panels (grayscale, colorspace, profile) | closed | open | open |
| Typed numeric entry | — | — | yes |
| Augmentation | — | — | yes |
| Images at once | 40 | 40 | no limit |

The sets nest — Beginner ⊂ Intermediate ⊂ Advanced — and the mode is remembered in
`localStorage` under `imt-mode`. Switching either way is free.

Moving *down* into a capped mode with more images loaded than it allows is **refused**
with a message naming the number to remove, rather than silently discarding work.
Uploading past the cap while in a capped mode keeps the first 40 and says so. A filter
selection that the destination mode does not offer is cleared, and the user is told.

In Advanced each slider gains a typed box. What it accepts comes from that slider's
own `step`, so it cannot disagree with what the backend validates: `edge` (step 1)
refuses `3.7`, `blur` (step 0.5) accepts `7.5`, `rotate` (step 15) snaps 100 to 105.
Only plain decimals are taken — `0x10`, `0b101`, `1e1`, `Infinity` and `1,5` are all
rejected with a readable reason, because `Number()` would otherwise silently accept
them as 16, 5, 10, ∞ and NaN.

### Going back to the single-mode interface

```bash
./scripts/rollback.sh              # which one is live
./scripts/rollback.sh --to-v1.0    # the single-mode UI
./scripts/rollback.sh --to-v1.1    # the three-mode UI
```

Only `ui/advanced-index.html` changes. The backend, the filters and every test are
identical either way, so nothing you have already generated is affected, and the
round trip is byte-for-byte reversible. `run.sh` does not need restarting — the page
is read from disk on each request.

### Dark and light

The interface is **dark by default**, on every machine. The slide switch at the right
of the header moves between the two — sun on the left, moon on the right, knob over
whichever is inactive — and the choice is kept in `localStorage` under `imt-theme`,
per browser.

Switching plays a short burst of light (or dark) expanding from the switch, with the
palette swapping underneath it 190 ms in, so the change reads as deliberate rather
than as a flicker. The overlay is removed on `animationend` with a 1.2 s timer as a
backstop, and is suppressed entirely under `prefers-reduced-motion`.

This is deliberately *not* wired to `prefers-color-scheme`. A tool whose job is
judging image output should look the same everywhere unless someone asks otherwise —
the surface behind a thumbnail changes how you read the mutation. Dark is the default
for the same reason: mutated images are easier to compare against a dark ground.

Every colour is a CSS custom property defined once per theme, so the two palettes
cannot drift apart. `color-scheme` is set alongside them so native widgets — radios,
slider tracks, scrollbars, focus rings — follow the theme rather than staying light.
The sixteen per-filter slider hues are pitched for the dark surface and darkened by a
single filter rule in light, rather than being maintained twice.

---

## 5. Filter catalogue

The live catalogue is always at `GET /api/mutations`. As configured:


### Continuous filters

| Filter | Label | Parameters |
|---|---|---|
| `black_threshold` | Black Threshold | `percentage`: 0–100, default 25 |
| `blur` | Blur (Gaussian) | `sigma`: 0.1–20, default 5 |
| `border` | Border | `pixels`: 0–100, default 10 |
| `brightness` | Brightness | `percentage`: -100–100, default 0 |
| `charcoal` | Charcoal Effect | `radius`: 0.2–10, default 5 |
| `color_reduce` | Color Palette | `colors`: 2–256, default 16 |
| `colors` | Colors | `colors`: 2–256, default 256 |
| `contrast` | Contrast | `percentage`: -100–100, default 0 |
| `edge` | Edge Detect | `radius`: 1–10, default 1 |
| `emboss` | Emboss | `radius`: 0.5–5, default 1 |
| `gamma` | Gamma | `gamma`: 0.5–5, default 1.0 |
| `gaussian_blur` | Gaussian Blur | `radius`: 1–20, default 5 |
| `implode` | Implode | `factor`: -2–2, default 0.5 |
| `median` | Median Filter | `kernel`: 1–10, default 1 |
| `paint` | Oil Paint | `radius`: 1–10, default 1 |
| `posterize` | Posterize | `levels`: 2–8, default 4 |
| `raise` | Raise | `pixels`: 1–50, default 10 |
| `rotate` | Rotate | `degrees`: 0–360, default 90 |
| `rotation` | Rotation | `degrees`: 0–360, default 0 |
| `rotational_blur` | Rotational Blur | `angle`: 1–90, default 10 |
| `saturation` | Saturation | `percentage`: -100–200, default 0 |

### Discrete filters

| Filter | Label | Parameters |
|---|---|---|
| `annotate` | Text Annotation | `fontSize`: one of 4; `position`: one of 9; `text`: None–None, default  |
| `chop` | Chop/Crop | `type`: one of 3; `value`: 1–200, default 50 |
| `colorize` | Colorize | `tone`: one of 7 |
| `colorspace` | Colorspace Conversion | `colorspace`: one of 31 |
| `flip` | Flip | — |
| `flop` | Flop | — |
| `grayscale` | Grayscale | `method`: one of 9 |
| `monochrome` | Monochrome | — |
| `negate` | Negate | — |
| `normalize` | Normalize | — |
| `profile` | Profile | `profile`: one of 50 |

### Sub-option families

`colorspace`, `grayscale`, `profile` and `ordered_dither` each expand into many
configurations, which is how 69 operators become 160 mutations per image: 63
operators contribute one configuration each, and the four expanding families
contribute the rest.

| Family | Options | Source |
|---|---|---|
| Colorspaces | 31 | `magick -list colorspace` |
| Grayscale intensity methods | 9 | `magick -list intensity` |
| ICC profiles | 49 + `Strip` | bundled in `assets/icc`, no host dependency |
| Ordered-dither threshold maps | 7 | `magick -list threshold` |

Colorspaces: CMY, CMYK, Gray, HCL, HCLp, HSB, HSI, HSL, HSV, Jzazbz, Lab, LCHab,
LCHuv, LMS, Log, OHTA, Oklab, Oklch, Rec601YCbCr, Rec709YCbCr, RGB, scRGB, sRGB,
Transparent, xyY, YCbCr, YCC, YDbDr, YIQ, YPbPr, YUV.

Grayscale methods: Rec601Luma, Rec601Luminance, Rec709Luma, Rec709Luminance,
Brightness, Lightness, Average, MS, RMS.

Some pairs are genuinely equivalent in ImageMagick and produce identical output —
HCL/HCLp, HSB/HSV, RGB/scRGB, YCbCr/YPbPr — and `sRGB`/`Transparent` leave an
already-sRGB source unchanged. That is correct behaviour, not a failure.

---

## 6. Output filenames

Names carry the filter and its settings, so a generated set stays traceable:

```
photo_blur_sigma5.png            photo_rotate_degrees90.png
photo_gamma_1p5.png              photo_colorspace_Oklab.png
photo_grayscale_methodRMS.png    photo_profile_AdobeRGB1998.png
photo_negate.png
```

`p` stands in for the decimal point, since a dot would read as an extension. Where
the parameter name repeats the filter name it is dropped (`gamma_gamma1p5` →
`gamma_1p5`). If a value was clamped, the **clamped** value is what appears, so the
name always states what was actually applied.

---

## 7. API reference

Base: `http://localhost:5000`

### `GET /`
Landing page: version, the web interface URL, and the endpoint list.

### `GET /api/health`
Engine and capability disclosure.

```json
{
  "status": "healthy",
  "imagemagick": {
    "version": "ImageMagick 7.1.1-41 Q16-HDRI ...",
    "delegates": ["png", "tiff", "webp", "freetype", "..."],
    "native_formats": [".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"],
    "pil_fallback_formats": [],
    "core_bound": true,
    "colorspaces": ["CMY", "..."],
    "grayscale_methods": ["Rec601Luma", "..."],
    "icc_profiles": ["Strip", "AdobeRGB1998", "..."]
  }
}
```

`pil_fallback_formats` lists formats ImageMagick cannot handle on this host, which
are approximated by Pillow instead. Empty is what you want.

### `GET /api/mutations`
The full catalogue with parameter types, ranges and defaults.

### `POST /api/mutate`
`multipart/form-data`:

| Field | Required | Meaning |
|---|---|---|
| `image` | yes | the image file |
| `mutation` | yes | filter name, e.g. `blur` |
| `parameters` | no | JSON object, e.g. `{"sigma": 5}` |

```bash
curl -X POST http://localhost:5000/api/mutate \
  -F "image=@photo.png" \
  -F "mutation=blur" \
  -F 'parameters={"sigma":5}'
```

```json
{
  "success": true,
  "mutation": "blur",
  "parameters": {"sigma": 5.0},
  "original_filename": "photo.png",
  "download_filename": "photo_blur_sigma5.png",
  "original_url": "http://localhost:5000/api/image/<id>_original.png",
  "result_url":   "http://localhost:5000/api/image/<id>_result.png",
  "download_url": "http://localhost:5000/api/download/<id>_result.png?filename=...",
  "timestamp": "2026-09-02T12:00:00"
}
```

Errors are **400** for anything the caller controls (unknown filter, bad parameter
type, out-of-set choice, malformed JSON), **404** for a missing file, **500** only for
a genuine server fault. Numeric parameters outside their range are clamped rather
than rejected.

### `GET /api/image/<file>` · `GET /api/download/<file>`
View or download a result. `download` accepts `?filename=` for the saved name. Both
resolve the path and require the target to be a regular file inside `outputs/`.

### `POST /api/download-batch`
`application/json`, packs several results into a ZIP:

```json
{"results": [{"url": "http://localhost:5000/api/image/<id>_result.png",
              "filename": "a.png"}]}
```

---

## 8. Validation

Eight suites, in `tests/`. Start the tool, then `tests/run_all.sh`.

| Suite | What it asserts | Current |
|---|---|---|
| `oracle_differential.py` | pixel equality with the `magick` CLI for every filter with an unambiguous command-line form | **41 / 42 exact, 1 equivalent** |
| `oracle_metamorphic.py` | properties that follow from operator meaning and need no reference | **20 / 20 pass** |
| `adversarial.py` | malformed and hostile input never produces a 5xx, a hang, or a leak | **0 failures** |
| `smoke_all_mutations.py` | every filter in the live catalogue returns 200 and actually changes the image | **49 / 49, 0 unchanged** |
| `oracle_formats.py` | every input format survives every class of filter, and the output opens | **60 / 60 calls** |
| `edge_cases.py` | hostile content, exotic-but-valid images, every parameter bound, awkward filenames, multi-frame input, the batch-ZIP endpoint, annotation text in nine scripts | **187 / 187** |
| `oracle_suboptions.py` | every value of every discrete sub-option: 31 colorspaces, 9 grayscale methods, 50 ICC profiles, 7 ordered-dither maps | **97 / 97 changed** |
| `oracle_defaults.py` | the default `/api/mutations` documents is the one the interface actually sends | **43 / 43 agree** |
| `ui_wiring.py` | every control the interface offers maps to a real catalogue mutation and parameter, and no catalogue option is unreachable | **49 / 49 wired**, sub-option radios match exactly |
| `augmentation_grid.py` | every one of the 160 augmentation configurations applies, and how many distinct images they actually yield | **160 / 160 apply**, 148 distinct at default slider positions |
| `download_paths.py` | the single-file download and the batch ZIP: right file, right name, no traversal, colliding names keep every entry | **17 / 17 checks** |
| `ui_behaviour.py` | behaviour only a browser shows: theme toggle, numeric entry validation, per-mode upload caps, clearing | **21 / 21 checks** in headless Chrome |
| `build_matrix.py` | the catalogue against more than one ImageMagick build, and whether any operator can take the server down | **68/69** on the full build, **31/69** on a stock one, **0 server deaths** |

### Performance

`bench/scale_run.py` runs the full augmentation grid over a synthetic corpus and writes
`bench/results/scale.json`:

```bash
bench/scale_run.py                       # 1, 5, 10, 20 images
bench/scale_run.py --sizes 1,50 --concurrency 8
```

Measured on this machine at concurrency 8, 1024×1024 JPEG sources, when the grid held
**107** configurations per image: **8,132 mutations, 0 failures**, throughput flat
between 31 and 33 mutations/second from 107 to 4,280 mutations, p50 ≈ 215 ms and
p95 ≈ 570 ms unchanged across that range. The grid has since grown to 160, so a run
now costs proportionally more — the linearity is the transferable result, not the
absolute time. Total time is linear in the workload, so a corpus run can be costed
from a small sample. `rotational_blur`, `annotate`, `monochrome` and `colors` dominate.

The interface itself is covered by five browser-driven suites, run headless against
the deployed page: mode behaviour (28 assertions), the image cap (9), augment across
mode transitions with images loaded (9), edge cases (27) and the **actual API payload
each mode produces** (6). That last one intercepts `fetch`: correct on-screen state is
not evidence that the right request goes out.

`run_all.sh` accepts `TOOL_BASE` if the tool is not on the default port:

```bash
TOOL_BASE=http://127.0.0.1:5050 tests/run_all.sh
```

The smoke suite is the cheap guard against a filter quietly becoming a no-op — the
Class A failure mode that cost the most to find the first time, because a no-op still
returns HTTP 200 with a plausible-looking image.

`oracle_formats.py` builds its own corpus: one shape image written out as JPEG, PNG,
GIF, WebP, BMP and TIFF, plus greyscale, alpha, 3000×2250 and a single pixel. Each is
put through a blur, a grayscale, a colorspace conversion, a rotation, a posterise and
a profile conversion — 60 calls. Every format round-trips to itself.

### One default per parameter

`/api/mutations` is what a script reads to learn what a parameter defaults to, and
the interface carries its own defaults in markup. Nothing kept the two in step, and
four of nineteen had drifted — `blur` "at its default" meant sigma 5 to a script and
sigma 3 to anyone clicking Apply.

`grayscale` had three routes and three images: the UI sent `Rec709Luma`, the catalogue
advertised `Rec601Luma`, and omitting the parameter took a different operator entirely
(`colorspace = 'gray'`). Maximum pixel differences between them were 152, 172 and 27.

All four now agree, resolved **towards the values the interface already sent**, so no
output the UI produces has changed — only the documentation of it, and what an API
caller gets when omitting the parameter. `DEFAULT_GRAYSCALE_METHOD` is the single
source for grayscale. `tests/oracle_defaults.py` compares all nineteen on every run.

### Annotation text

`annotate` picks a font that can actually render the text, using
`fc-match :charset=…` over the string's code points. ImageMagick's default font is
Latin-only, and when a glyph is missing it draws **nothing** and reports success — so
CJK, Arabic, Greek, Hebrew and Thai captions returned HTTP 200 with an untouched
image. The draw is now bracketed by a pixel-signature check: if nothing was drawn the
request fails with a message naming the problem, rather than returning the input.
Verified across nine scripts plus mixed-script text.

### Multi-frame input

An animated GIF or a multi-page TIFF gets the operator on **every** frame, as the
ImageMagick command line does. Wand's `Image` methods act on whichever frame the
MagickWand iterator points at, and after loading a file that is the last one — so
before this was fixed, an animated GIF came back with one frame filtered and the rest
untouched, behind an HTTP 200 and a file that opens cleanly. `apply_to_every_frame()`
in `backend/app.py` walks the sequence. Verified against
`magick anim.gif -negate out.gif`: identical, all four frames.

### Output is byte-reproducible

The same input with the same parameters produces **byte-identical** output, whenever
you run it. That is not free: ImageMagick stamps `date:create`, `date:modify` and
`date:timestamp` into every output, and a `tIME` chunk into PNG. Pixels were always
identical, but the bytes changed with the wall clock, so two runs matched only when
they happened to land in the same second.

For this tool that is a correctness problem, not cosmetics — a generated dataset is
verified by reproducing it and de-duplicated by content hash, and both compare bytes.
`strip_timestamps()` in `backend/app.py` removes those fields on write. It removes
**only** the date fields: a blanket `-strip` would also discard the ICC profile, which
the `profile` filter exists to manage. The metamorphic relation now deliberately
sleeps across a second boundary so the guard cannot pass by luck.

The differential oracle is the one that catches wrong *defaults*: it found that
`posterize` and `colors` were not dithering while the CLI does, so the tool was
producing a different mutation from the command it claimed to implement.

Metamorphic relations include involutions (`flip(flip(x)) == x`), identities
(`blur(x, 0) == x`), structural guarantees (`border(n)` grows both axes by exactly
`2n`), range guarantees (grayscale output satisfies R=G=B; `colors(8)` yields at most
8 colours), monotonicity (local variance falls as blur σ rises) and determinism.

---

## 9. Configuration

`backend/.env` (copy from `backend/.env.example`):

| Key | Default | Meaning |
|---|---|---|
| `MAGICK_HOME` | unset | which ImageMagick to bind to; **read at import time** |
| `MAX_FILE_SIZE` | `0` | upload cap in bytes; 0 means no limit |
| `JPEG_QUALITY` | `90` | JPEG output quality, 1–100 |
| `PROFILE_SOURCE` | `sRGB` | profile assumed for images carrying none |
| `UPLOAD_FOLDER` / `OUTPUT_FOLDER` | `../uploads` / `../outputs` | storage |
| `FLASK_PORT` | `5000` | port the backend API listens on |
| `FRONTEND_PORT` | `3000` | port the UI is served on |
| `DEBUG` | `False` | `True` enables the Werkzeug debugger — see section 14 |
| `LOG_LEVEL` | `INFO` | `DEBUG` adds per-filter geometry and frame counts — see §11 |
| `FLASK_ENV` | — | Flask setting |

`run.sh`, `doctor.py` and the UI all read the two port keys, so changing them in
`backend/.env` is enough — nothing else needs editing. The environment overrides the
file, so a one-off move is just:

```bash
FLASK_PORT=5050 FRONTEND_PORT=3001 ./run.sh
```

Worth knowing on macOS, where AirPlay Receiver holds port 5000 and the tool would
otherwise refuse to start with no obvious cause.

The page resolves the API in three steps, most specific first: an explicit
`?api=http://host:port/api` in the URL, then `window.__API_PORT__` (which
`ui/frontend_server.py` rewrites from `FLASK_PORT` as it serves the file), then `5000`.
The first step lets you point a browser at a backend running on a different machine
entirely.

In `ui/advanced-index.html`:

| Constant | Default | Meaning |
|---|---|---|
| `CONCURRENCY` | `4` | mutation requests in flight |
| `LARGE_BATCH_HINT` | `60` | above this, warn that a run will be slow |
| `AUGMENTATION_SET` | — | which control each augmentation value is read from |

### Crash isolation and the fidelity gate

Mutations run in worker processes. ImageMagick can kill the process it runs in
rather than raising — `median` on a stock build raises SIGFPE, which no
`try/except` can catch — so the image work is kept out of the server process. A
crash costs one worker and one request, and the reply names the operator.

| Setting | Default | What it does |
|---|---|---|
| `MUTATION_ISOLATION` | `worker` | `inprocess` restores the old in-process behaviour |
| `MUTATION_WORKERS` | `3` | how many worker processes |
| `MUTATION_TIMEOUT` | `120` | seconds before a stuck mutation is killed |

Cost of isolation, measured on the 160-configuration grid: **62.6 mutations/s
against 72.6 in-process**, about 14%.

`run.sh` refuses to start when ImageMagick is missing the PNG, TIFF, WebP or
freetype delegates, because without them PNG is handled by PIL, which only
approximates the ImageMagick operators, and roughly half the catalogue is
unavailable. Override deliberately with `./run.sh --allow-degraded`, which
prints what is degraded every time it starts.

### Going back

`scripts/restore-point.sh` snapshots the whole source tree, not just the
interface that `rollback.sh` switches.

    ./scripts/restore-point.sh create "before some change"
    ./scripts/restore-point.sh list
    ./scripts/restore-point.sh verify <name>     # does it still match the tree?
    ./scripts/restore-point.sh restore <name>    # go back

A restore saves the current state first, so a restore can itself be undone.
Generated images in `outputs/` are never touched.


---

## 10. Layout of the project

The tool is self-contained in `image-mutation-tool/`. Nothing outside that folder is
needed to install or run it, and `scripts/package.sh` ships exactly its contents.

```
setup.sh                   one-time install: virtualenv, dependencies, checks
run.sh                     start both servers
doctor.py                  preflight: Python, packages, delegates, fonts, ports
requirements.txt           seven runtime dependencies
DOCUMENTATION.md           this file

ui/advanced-index.html     the actual UI -- the only interface
ui/frontend_server.py      serves it on :3000, allowlist only, no directory
ui/vendor/jszip.min.js     bundled so "Download All" works offline

backend/app.py             Flask API on :5000
backend/.env               local config (created by setup.sh from .env.example)

scripts/install-imagemagick.sh   the one external dependency, automated
scripts/package.sh               builds a deployable archive into dist/
scripts/rollback.sh              switch between the single- and three-mode UI
scripts/rollback-versions/       the two interface versions it switches between
scripts/.venvpath.sh             shared virtualenv resolution (see section 3)
scripts/start.sh  start.cmd  START_HERE.sh   alternative entry points

tests/                     eight validation suites + run_all.sh
bench/scale_run.py         corpus-scale benchmark (throughput, latency, scaling)
reports/                   technical report and research paper (md, tex, pdf)
dist/                      output of scripts/package.sh
outputs/  uploads/         runtime artefacts
```

---

## 11. Watching what the backend does

Every applied filter logs one line to the terminal running `./run.sh`:

```
15:00:21 INFO    border           {"pixels": 25}         ImageMagick  jpeg  1ms
15:00:21 INFO    rotate           {"degrees": 37.0}      ImageMagick  jpeg  23ms
15:00:22 INFO    blur             {"sigma": 50.0}        ImageMagick  jpeg  10ms
```

The columns are the resolved filter, the parameters **after validation and
clamping**, which engine ran it, the format, and how long it took. The clamping is
the useful part: the third line above was requested as `sigma=9999` and is recorded
as `50.0`, so you can see the tool refused the value rather than silently obeying it.

`ImageMagick` vs `PIL-fallback` in the third column is the one to watch for research
output — the fallback only approximates the operators.

Set `LOG_LEVEL=DEBUG` in `backend/.env` for a second line per filter:

```
15:00:49 INFO    blur             {"sigma": 4.0}         ImageMagick  gif  6ms
15:00:49 DEBUG      anim2.gif: 120x90 -> 120x90, 4 frame(s)
```

— source name, geometry before and after, and the frame count, which is how you
confirm a multi-frame file kept all its frames.

Deeper than that is ImageMagick's own tracing, which the tool does not wrap. Use the
command line for it:

```bash
magick -debug cache  test.jpg -blur 0x3 out.jpg     # pixel cache
magick -debug coder  test.jpg -blur 0x3 out.jpg     # format decode/encode
magick -debug all    test.jpg -blur 0x3 out.jpg     # everything, very loud
```

`magick -list debug` prints the channels: `Accelerate Annotate Blob Cache Coder
Command Configure Deprecate Draw Exception Locale Module Pixel Policy Resource Trace
Transform User Wand`.

To confirm the tool agrees with the command line for a given filter, run both and
compare — that is exactly what `tests/oracle_differential.py` does for 25 cases.

---

## 12. Troubleshooting

**`Address already in use` / `run.sh` refuses to start** — the tool is already
running. Open <http://localhost:3000>, or `pkill -f 'app.py|frontend_server.py'` to
stop it. Find the holder with `lsof -i :5000`.

**`http://localhost:5000` looks wrong** — that is the API, not the interface. It
returns a JSON landing page. The UI is on **3000**.

**The page looks like a placeholder with no upload box** — you ran `npm start`. Stop
it and use `./run.sh`.

**PNGs come back unchanged, or `colorspace` errors on PNG** — your ImageMagick has no
PNG delegate, so PNGs go through the Pillow fallback. Check `/api/health`; see §3.

**Mutations return `Unknown mutation`** — the UI and API are out of step. Every filter
in `ui/advanced-index.html` needs a matching method and a `MUTATION_SPECS` entry in
`backend/app.py`.

**`annotate` fails with a font error** — no `freetype` delegate or no fonts.
`magick -list font` should show more than one entry.

**"Download All" does nothing offline** — it should work; JSZip is vendored locally
and the page makes no external requests. If it fails, check the browser console.

**A result tile shows "preview unavailable"** — the image exists and downloads fine;
only the preview failed to load. Common with very large batches.

---

## 13. Known limitations

1. **JPEG output is re-encoded**, so it carries compression loss on top of the
   mutation. A pixel-preserving operation on a JPEG still shifts pixels by up to 25
   levels (mean 3.5); the same test on PNG differs by **0**. Set `JPEG_QUALITY=100`,
   or work in PNG, when the output is research data.
2. **`-colorspace` keeps transformed values by default.** Plain ImageMagick converts
   back when writing an sRGB format, which makes 23 of 31 colorspaces no-ops. The
   tool applies `-set colorspace sRGB` afterwards so each yields a distinct image.
   Pass `keep_values=false` for literal CLI behaviour.
3. **ICC conversion assumes an sRGB source** for untagged images, because ImageMagick
   otherwise only tags them without converting. Configurable via `PROFILE_SOURCE`.
4. **8 of 50 profile options are metadata-only** against an sRGB source (`sRGB`,
   `srgb`, `default_rgb`, `Bluish`, three `Gamma*K`, and `Strip` by design).
5. **The Pillow fallback is an approximation.** It no longer returns the input
   silently — unsupported colorspaces raise, ICC conversion goes through LittleCMS,
   and the nine grayscale formulas are implemented — but it is not ImageMagick's code
   path. `/api/health` discloses when it is in use.
6. **Flask's development server** runs the backend. Threaded, and it sustained
   175 req/s here, but it is not a production server.
7. **No authentication.** `run.sh --host 0.0.0.0` exposes the tool with no access
   control.
8. **Augmentation output grows fast** — 100 variants per image.

---

## 14. Deployment

Two different questions, with two different answers.

### Shipping it to another machine — yes, within these limits

```bash
./scripts/package.sh     # -> dist/image-mutation-tool-<date>.tar.gz  (260 KB)
./scripts/package.sh --zip   # also a .zip, for a Windows target
./scripts/package.sh --with-reports
```

The archive is source plus `setup.sh` and the validation suites. It deliberately
carries **no virtualenv**: the target builds its own, against its own Python and its
own ImageMagick, which is the only way the Wand/MagickCore binding is correct there.
It also carries no `backend/.env` — that file holds this machine's `MAGICK_HOME` and
`DEBUG` setting, and `setup.sh` recreates it from `.env.example` on the target.
`scripts/package.sh` aborts rather than build an archive containing a `.env` or a virtualenv.

On the target:

```bash
tar xzf image-mutation-tool-<date>.tar.gz
cd image-mutation-tool-<date>
./setup.sh                # virtualenv, dependencies, environment check
./run.sh                  # then open http://localhost:3000
```

Verified end to end: the archive was unpacked into an empty directory, installed with
`PYTHON=python3.12 ./setup.sh` against a brand-new virtualenv, started on non-default
ports (5090/3090), and put through the full suite from inside the package — 25/25
differential, 20/20 metamorphic, 0 adversarial failures, 32/32 mutations. Before
`MAGICK_HOME` was set it correctly reported the system ImageMagick's missing
png/tiff/webp/freetype delegates rather than pretending they were there.

**What the target host must have:**

| Requirement | If it is missing |
|---|---|
| **Python 3.10 – 3.13** (3.12 recommended) | `setup.sh` stops with one clear line below 3.10, where the pinned numpy, Pillow and python-dotenv have no release at all. Above 3.13 it warns rather than stops. The full suite was run on 3.12.7: 25/25 differential, 20/20 metamorphic, 0 adversarial failures, 32/32 mutations. |
| **Linux or macOS** | the launchers are bash. `start.cmd` exists for Windows but is untested; `run.sh`, `setup.sh` and `tests/run_all.sh` are not. |
| **ImageMagick with png/tiff/webp/freetype** | those formats fall back to Pillow, which approximates the operators. Reported at startup, by `doctor.py`, and in `/api/health`. See section 3. |
| **An ICC profile store** | not required: 49 profiles ship in `assets/icc`, so the `profile` family works on any host. Set `PROFILE_SCAN_SYSTEM=true` to additionally scan the host store. `/api/health` reports the count. |

Neither of the last two stops the tool running; both change what it produces, and
both are disclosed rather than silent. Run `python3 doctor.py` on the target host
before trusting the output.

### Hosting it as a public web application — not as it stands

The tool binds to loopback by default and is built for a trusted, single-user
context. Exposing it as a service needs work it has not had:

| Blocker | Why it matters |
|---|---|
| **Werkzeug debug console at `/console`** | with `DEBUG=True` this is a Python execution endpoint. Never expose it. The backend now refuses to enable debug when bound off-host, but set `DEBUG=False` for any deployment. |
| **Flask's development server** | `app.run()` is explicitly not a production server. Put it behind gunicorn/uWSGI and a reverse proxy. |
| **No authentication** | every endpoint is open. Result URLs are unguessable (128-bit ids) but not access-controlled. |
| **CORS allows every origin** | `CORS(app)` reflects any `Origin`, so any website could drive the API from a visitor's browser. Restrict it. |
| **No upload cap by default** | `MAX_FILE_SIZE=0`. Set a real byte limit before exposing it. |
| **No rate limiting** | one augmentation request is 160 ImageMagick operations. A handful of clients can saturate the host. |
| **`outputs/` grows without bound** | nothing prunes it. Needs a retention policy. |
| **No HTTPS** | terminate TLS at a proxy. |
| **ImageMagick is a large attack surface** | consider a restrictive `policy.xml` and running the workers in a container. |

A reasonable production shape would be: gunicorn behind nginx with TLS and auth,
`DEBUG=False`, an explicit `MAX_FILE_SIZE`, a CORS allow-list, per-IP rate limits, a
cleanup job for `outputs/`, and ImageMagick confined by policy.

### Sharing it on a trusted LAN — workable with care

```bash
./run.sh --host 0.0.0.0
```

This binds both servers to all interfaces. Before doing it: set `DEBUG=False`, set a
real `MAX_FILE_SIZE`, and only do it on a network you trust. There is still no
authentication.

`ui/frontend_server.py` serves an explicit allowlist — `advanced-index.html` and
`vendor/jszip.min.js`, nothing else — so binding it off-host no longer exposes the
project directory. It used to serve the whole folder through
`SimpleHTTPRequestHandler`, which meant `GET /backend/.env` returned the
configuration file and `GET /outputs/` returned a directory listing.

---

## 15. History

Built in a GitHub Copilot Chat session in VS Code, 23–26 March 2026; the transcript is
under `../misc/history/chat-history/`. On 24 March the interface was rewritten to expose 25 filters
from `../misc/reference/Discrete and Continuous Filters.xlsx`, **and the backend was never updated to
match**. From then until 2 September 2026 the UI sent filter names the API did not
implement.

Audited and repaired on 2026-09-02. Of the 25 filters the interface offered, 4 worked;
one more returned HTTP 200 while changing nothing. 39 defects were found and fixed
across six classes — 13 of them producing silently wrong output.

Full accounts:

| Document | What it is |
|---|---|
| `reports/TECHNICAL_REPORT.{md,tex,pdf}` | every defect, root cause and measurement |
| `reports/RESEARCH_PAPER.{md,tex,pdf}` | the general findings on silent failure in mutation tooling |
| `reports/REPAIR_NOTES_2026-09-02.md` | chronological repair record |
| `../misc/history/docs-archive/` | the superseded documentation, kept for history |
