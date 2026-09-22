# Changelog

All notable changes to this project are recorded here. Versions follow
[semantic versioning](https://semver.org/).

## [1.4.1] — 2026-09-22

### Added
- **Every operator in the catalogue is now reachable from the interface**, 76 of
  76. `colorize` (7 tones) and `annotate` (free text, 4 sizes, 9 positions) had
  existed only on the API because neither fits the single-slider pattern, and
  `chop` had a size slider but no control for its direction, so it was always
  horizontal whatever the catalogue offered.
- Discrete filters can now carry more than one sub-option. `annotate` needs a
  string, a size and a position, where the existing wiring allowed exactly one
  choice per operator.

### Fixed
- The annotation text field first reused the `num-box` class, which the mode
  logic and the behaviour suite both read as "typed numeric value" -- Beginner
  mode then reported a numeric box it is supposed to hide. Free text has its own
  class now.

## [1.4.0] — 2026-09-22

### Added
- **Channel restriction.** Any mutation can be limited to one colour plane, as
  ImageMagick's `-channel` setting does — `{"sigma":5,"channel":"red"}` on the
  API, a control in Advanced mode in the interface. Implemented through
  `MagickSetImageChannelMask`, which is the same mechanism the command line
  uses, so it needed no per-operator plumbing.
- `tests/channel_restriction.py`, the fourteenth suite: parity with
  `-channel R … +channel` for each supported operator, that the other planes
  come back untouched, and that unsupported operators are refused.

### Notes
- **40 of the 76 operators honour the mask; the other 36 are refused.** The
  mask is not universal: `posterize` quantises all three planes whatever it
  says, diverging from `-channel G -posterize 4 +channel` by 250 levels.
  Accepting such a request would return a whole-image mutation labelled as
  channel-restricted. Support is therefore measured per operator at run time
  rather than kept in a list, so it cannot fall out of date.
- The Pillow fallback has no channel mask, so a channel request on a format
  being handled by the fallback is refused rather than approximated.

## [1.3.0] — 2026-09-22

### Added
- **Seven operators**, taking the catalogue to **76** (55 continuous, 21
  discrete) and the grid to **173 configurations per image**, 161 of them
  distinct at default settings:
  `bilateral_blur`, `contrast_stretch`, `linear_stretch`, `level`, `threshold`,
  `distort` (barrel/pincushion) and `morphology` (7 methods on a diamond
  kernel). Each was verified pixel-identical to its ImageMagick command-line
  form before being accepted.
- `bilateral_blur` is the one that matters most for provenance work: it is an
  edge-preserving denoise, so it erases noise-residual statistics while leaving
  structure intact.

### Fixed
- **The discrete sub-option panels were driven by a hand-written chain that
  ended in `else if (selected === 'ordered_dither')`, and `selected` does not
  exist in that scope.** Choosing any discrete filter outside the first three
  branches threw a `ReferenceError`, so the ordered-dither panel never opened.
  Panels are now named on the radio that owns them and resolved generically.

### Notes on what was rejected
- Wand's `noise` **adds** random noise rather than performing the command
  line's `-noise radius` reduction, so it was excluded on reproducibility
  grounds, as `-spread` and `-sketch` were before it.
- `bilateral_blur` diverged from the command line by up to 5 levels until
  intensity and spatial were stated explicitly on both sides. Left to default,
  Wand and ImageMagick each compute their own.
- `contrast_stretch` and `linear_stretch` take the clip fraction at *both*
  ends: Wand's `white_point` counts from the top, as the command line's second
  value does. Passing `1 - f` stretches the wrong way, by 255 levels.

## [1.2.0] — 2026-09-21

First public release.

### Added
- **20 operators**, taking the catalogue to **69** (49 continuous, 20 discrete):
  `adaptive_blur`, `adaptive_sharpen`, `blue_shift`, `brightness_contrast`,
  `canny`, `kuwahara`, `magnify`, `modulate`, `motion_blur`, `ordered_dither`,
  `polaroid`, `roll`, `selective_blur`, `shade`, `sigmoidal_contrast`,
  `transpose`, `transverse`, `vignette`, `wavelet_denoise`, `white_threshold`.
  Each was verified pixel-identical to its ImageMagick command-line form at the
  catalogue default. `-spread` and `-sketch` were deliberately excluded: both
  are random, which would break reproducibility.
- **Crash isolation.** Mutations run in worker processes. ImageMagick can kill
  the process it runs in rather than raising — `median` does exactly that on
  some builds, with SIGFPE, which no `try`/`except` can catch. A crash now costs
  one worker and one request instead of the whole server. Measured cost: about
  14% throughput (62.6 against 72.6 mutations per second).
- **Fidelity gate.** `run.sh` refuses to start on an ImageMagick missing the
  PNG, TIFF, WebP or freetype delegates, because those builds silently
  substitute Pillow approximations for the operators the catalogue names.
  `--allow-degraded` overrides it and prints the degradation on every start.
- **49 ICC profiles bundled** in `assets/icc/`, so the `profile` operator no
  longer depends on what the host happens to have installed.
- **Five validation suites**, taking the total to 13: `ui_wiring`,
  `augmentation_grid`, `download_paths`, `ui_behaviour`, `build_matrix`.
- Three experience modes (Beginner, Intermediate, Advanced) with per-mode
  filter sets, tuning rules and upload caps.
- `scripts/restore-point.sh` for whole-tree snapshots and rollback.

### Fixed
- **33 of 49 continuous filters were not wired to the Apply path.** The apply
  logic named 16 filters across three hand-maintained tables while the
  interface had grown to 49, so selecting any of the other 33 showed no
  controls and reported "Select a continuous filter". Now catalogue-driven.
- **9 of 19 discrete filters** had the same defect.
- **One augmentation job failed on every run**: the `charcoal` entry passed
  `factor` to an operator that takes `radius`.
- **13 bundled ICC profiles were unreachable** from the interface, and one
  appeared twice under two spellings.
- **"Clear all" left every thumbnail in the DOM**, retaining a base64 copy of
  each uploaded file.
- The parameter panel opened below the fold, and the mode switcher collided
  with the title at 1366px and narrower.

### Known limitations
- Byte-equality with the command line was established against **ImageMagick
  7.1.1-41 Q16-HDRI**. Q16 and Q16-HDRI round differently for some operators.
- **Eight of the 160 configurations return the input unchanged** at default
  slider positions, so a default augmentation run yields 148 distinct images.
- `annotate` and `colorize` are reachable only through the API.

[1.4.1]: https://github.com/vaishnavkoka/Image-Augmentation-tool/releases/tag/v1.4.1
[1.4.0]: https://github.com/vaishnavkoka/Image-Augmentation-tool/releases/tag/v1.4.0
[1.3.0]: https://github.com/vaishnavkoka/Image-Augmentation-tool/releases/tag/v1.3.0
[1.2.0]: https://github.com/vaishnavkoka/Image-Augmentation-tool/releases/tag/v1.2.0
