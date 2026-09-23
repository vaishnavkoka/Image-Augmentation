# Usage

Three ways into the same engine. They share one catalogue, one set of validation
rules and one grid definition, so they cannot disagree about what a filter does.

| Way in | Best for | Start it with |
|---|---|---|
| **Web interface** | exploring visually, seeing results as you go | `./run.sh`, then <http://localhost:3000> |
| **Guided mode** | batch work without knowing the flags | `./imt.py` |
| **Command line** | scripts, pipelines, reproducible runs in a paper | `./imt.py apply …` |

---

## Before anything

```bash
./setup.sh      # virtualenv, dependencies, environment check
./run.sh        # API on :5000, interface on :3000
```

`run.sh` refuses to start on an ImageMagick missing the PNG, TIFF, WebP or
freetype delegates, because those builds silently substitute Pillow
approximations for the operators the catalogue names. Override deliberately with
`./run.sh --allow-degraded`.

---

## Guided mode

```bash
./imt.py               # no arguments
./imt.py guided        # the same, named
```

It asks which image or folder, whether you want one filter or all of them, shows
**continuous** filters (which take a value) separately from **discrete** ones
(which do not), checks each value against its range, and finishes by printing the
command that would have done the same thing:

```
Done — 1 file(s) in ./results

The command that does this directly:
  ./imt.py apply photo.png gamma --set gamma=2.5 -o ./results
```

The first run is guided, the second is typed, the third is in a script.

---

## Command line

| Command | What it does |
|---|---|
| `./imt.py health` | which ImageMagick is bound, which formats are native |
| `./imt.py list` | the catalogue |
| `./imt.py list -v --filter blur` | with parameters and ranges, names containing "blur" |
| `./imt.py list --json` | the catalogue as JSON |
| `./imt.py apply IMAGE FILTER` | one filter on one image |
| `./imt.py augment PATH -o DIR` | every configuration, on an image or a folder |
| `./imt.py compare IMAGE FILTER` | print the request without sending it |
| `./imt.py codes` | what each error code means |

### Options, and their defaults

These work **before or after** the subcommand.

| Option | Default | Meaning |
|---|---|---|
| `--base URL` | `http://127.0.0.1:5000` | where the tool is running |
| `--mode` | `advanced` | mirror an interface mode |
| `--set NAME=VALUE` | the catalogue default | a filter parameter, repeatable |
| `--channel` | `all` | restrict to one colour plane |
| `-o`, `--out` | `.` for `apply`, required for `augment` | where results go |
| `--log-level` | `WARNING` | console verbosity; the file always records everything |
| `--log-file` | `cli-version/logs/imt.log` | where the run is recorded |
| `-q`, `--quiet` | off | no console output, no progress bar |
| `--dry-run` | off | `augment` only: list without running |

### Examples

```bash
./imt.py apply photo.png blur --set sigma=5 -o out/
./imt.py apply photo.png gamma --set gamma=2.5 -o out/
./imt.py apply photo.png morphology --set method=Erode -o out/
./imt.py apply photo.png chop --set value=60 --set type=vertical -o out/
./imt.py apply photo.png blur --set sigma=5 --channel red -o out/

./imt.py augment photos/ -o out/                  # 173 per image
./imt.py augment photos/ -o out/ --channel red    # only filters that support it
./imt.py augment photo.png -o out/ --dry-run      # list, do not run
```

A folder gets one subdirectory per source image. Accepted inputs: `.jpg`,
`.jpeg`, `.png`, `.gif`, `.bmp`, `.tif`, `.tiff`, `.webp`.

---

## Modes

The same three the interface offers, read from it rather than copied.

| Mode | Filters offered | Set values | Channel | Augment | Image cap |
|---|---|---|---|---|---|
| `beginner` | 8 common ones | no | no | no | **40** |
| `intermediate` | all 76 | yes | no | no | **40** |
| `advanced` *(default)* | all 76 | yes | yes | yes | **500** |

A request a mode does not allow is refused with a reason, never quietly widened:

```
imt [E203]: beginner mode runs filters at their defaults, so ['sigma'] cannot be set
            Use --mode intermediate or advanced to tune
```

---

## Defaults worth knowing

| | Default | Note |
|---|---|---|
| Filter parameters | the catalogue's own | `./imt.py list -v` shows every range and default |
| Configurations per image | **173** | of which 161 are distinct at default settings |
| Channel | `all` | 80 of the 173 support a restriction; the rest are skipped and counted |
| JPEG quality | 90 | set `JPEG_QUALITY=100` in `src/backend/.env` for research output |
| Output format | same as the input | prefer PNG: JPEG is re-encoded and adds loss |

---

## Error codes

The first digit matches the exit code, so a script can branch on either.

| Code | Exit | Meaning |
|---|---|---|
| `E101` | 1 | the mutation could not be performed |
| `E102` | 1 | the input file does not exist |
| `E103` | 1 | the input file is not a readable image |
| `E104` | 1 | the result could not be downloaded or written |
| `E200` | 2 | the command line was wrong |
| `E201` | 2 | no such mutation in the catalogue |
| `E202` | 2 | a parameter was malformed or out of range |
| `E203` | 2 | the chosen mode does not allow this |
| `E204` | 2 | no input images were found |
| `E205` | 2 | the interface file could not be read |
| `E301` | 3 | no tool is running at the given address |
| `E302` | 3 | something answered, but it is not this tool |
| `E401` | 4 | the batch finished with failures |
| `E402` | 4 | interrupted before finishing |

`./imt.py codes` prints the same table.

---

## Checking the two agree

```bash
./imt.py apply photo.png blur --set sigma=5 -o out/
sha256sum out/photo_blur_sigma5.png
```

Do the same in the interface and compare. They should match exactly —
`cli-version/tests/test_parity.py` asserts it on every run by driving the real
interface in a headless browser and comparing the bytes.
