# cli-version

The command line for the Image Mutation Tool. Everything the web interface does,
without a browser.

## Why it produces the same results

It posts to the same HTTP API the interface uses. One engine, one catalogue, one
set of validation rules — a second implementation of the mutations here would be
a second thing to keep in step, and this project has produced six defects of
exactly that shape.

That leaves one place the two could still diverge: **how each builds its
request**. `tests/test_parity.py` drives the real interface in headless Chrome,
intercepts the request it would send, and compares it against what the CLI
builds for the same choices — then applies both and compares the SHA-256 of the
returned bytes.

That test found a real divergence on its first run: the interface sent
`chop {"value": 40, "type": "horizontal"}` while the CLI sent `{"value": 40}`
and relied on the server's default. Same result that day, different the moment a
default moved. The CLI now states every parameter, as the interface does.

## If you are not fluent in flags

Run it with no arguments and it asks questions instead:

```bash
./imt.py
```

It asks which image or folder, whether you want one filter or all of them,
shows the **continuous** filters (which take a value) separately from the
**discrete** ones (which do not), checks each value against its range before
sending it, and then prints the command that would have done the same thing:

```
Done — 1 file(s) in ./results

The command that does this directly:
  ./imt.py apply photo.png gamma --set gamma=2.5 -o ./results
```

That last line is the point: the first run is guided, the second is typed, the
third is in a script. Reach it explicitly with `./imt.py guided`.

## Usage

```bash
imt health                                   what the engine can do
imt list [-v] [--filter TEXT]                the catalogue
imt apply IMAGE MUTATION [--set n=v ...]     one mutation
imt apply photo.png blur --set sigma=5 --channel red
imt augment PATH -o DIR                      the whole grid
imt augment PATH -o DIR --dry-run            list without running
imt compare IMAGE MUTATION --set n=v         print the request, do not send it
```

Run it as `../imt.py <command>` from the tool root, or `./imt.sh` from here.
Options work before or after the subcommand.

| Option | Meaning |
|---|---|
| `--mode beginner\|intermediate\|advanced` | mirror an interface mode (default advanced) |
| `--base URL` | where the tool is running (default `http://127.0.0.1:5000`) |
| `--log-level` | console verbosity; the file always records everything |
| `--log-file` | where to write the log (default `cli-version/logs/imt.log`) |
| `-q`, `--quiet` | no console output, no progress bar |

## Modes

Read from the interface's own `MODES`, not copied, so they cannot drift.

| Mode | Filters | Tuning | Channel | Augment | Image cap |
|---|---|---|---|---|---|
| Beginner | 8 common ones | no | no | no | 40 |
| Intermediate | all 76 | yes | no | no | 40 |
| Advanced | all 76 | yes | yes | yes | 500 |

A request a mode does not allow is refused with a reason, never quietly widened.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | the request was sound but could not be performed |
| 2 | the command line itself was wrong |
| 3 | no tool is running at `--base` |
| 4 | a batch finished with some failures |

## Logs

`logs/imt.log`, rotating at 5 MB × 3. The file records every request at DEBUG;
the console shows warnings and errors only, so a command whose output is a file
path prints just that path.

## Progress

`tqdm` is used when installed and is listed in `requirements.txt` as optional.
It is not a hard requirement — a built-in bar takes over when it is absent, and
both are silent when output is not a terminal. The test suite exercises **both**
implementations, blocking the `tqdm` import so the fallback is covered even on a
machine that has it.

## Tests

```bash
tests/run.sh          # needs the tool running: ../../run.sh
```

| Suite | Checks |
|---|---|
| `test_parity.py` | 24 — the CLI sends what the interface sends, byte-identical output |
| `test_modes.py` | 19 — each mode's filters, caps and refusals match the interface |
| `test_errors.py` | 17 — every failure path and its exit code |
| `test_logs.py` | 15 — the log file, rotation, levels, and both progress bars |
| `test_guided.py` | 23 — guided mode end to end, with scripted answers |
