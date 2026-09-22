# Changelog

All notable changes to this project are recorded here. Versions follow
[semantic versioning](https://semver.org/).

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

[1.2.0]: https://github.com/vaishnavkoka/Image-Augmentation/releases/tag/v1.2.0
