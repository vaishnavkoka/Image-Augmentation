"""
Image Mutation Tool - Flask Backend
Main application entry point with image processing mutations using ImageMagick
"""

import os
import json
import subprocess
import functools
import sys
import threading
import time
import uuid
import ctypes
import zipfile
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv

# The environment has to be loaded before Wand is imported: Wand resolves
# MAGICK_HOME at import time to pick which ImageMagick to bind to, and a
# distribution build that lacks the PNG/TIFF/WebP delegates will silently
# push those formats onto the PIL fallback instead.
load_dotenv()
if os.environ.get('MAGICK_HOME'):
    _magick_lib = os.path.join(os.environ['MAGICK_HOME'], 'lib')
    if os.path.isdir(_magick_lib):
        os.environ['LD_LIBRARY_PATH'] = (
            _magick_lib + os.pathsep + os.environ.get('LD_LIBRARY_PATH', '')
        )

from flask import Flask, request, send_file
from flask_cors import CORS
from wand.image import Image as WandImage
from wand.color import Color
from wand.api import library
from PIL import Image as PILImage
import numpy
import logging

# Setup logging
# LOG_LEVEL shipped in .env.example and was documented, but nothing read it --
# the level was hard-coded to INFO. DEBUG is what you want when you need to see
# what each filter actually did.
_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, _LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)
if not hasattr(logging, _LEVEL):
    logger.warning("LOG_LEVEL=%r is not a level; using INFO", _LEVEL)


class _MagickCore:
    """Direct access to the few MagickCore calls Wand does not wrap.

    Wand has no binding for `-grayscale <method>` at all, and its colorspace
    setter validates against its own list, which predates Jzazbz/Oklab/Oklch.
    Both are resolved here by name through ImageMagick's own option parser, so
    the tool accepts exactly what `magick -list colorspace` and
    `magick -list intensity` report.

    If the library cannot be loaded the callers fall back to Wand, so a machine
    without this exact build still runs, just with the smaller colorspace set.
    """

    COLORSPACE_OPTIONS = 9    # MagickColorspaceOptions
    INTENSITY_OPTIONS = 56    # MagickIntensityOptions

    def __init__(self):
        self.lib = None
        for candidate in self._candidates():
            try:
                lib = ctypes.CDLL(candidate)
                lib.AcquireExceptionInfo.restype = ctypes.c_void_p
                lib.DestroyExceptionInfo.argtypes = [ctypes.c_void_p]
                lib.DestroyExceptionInfo.restype = ctypes.c_void_p
                lib.ParseCommandOption.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
                lib.ParseCommandOption.restype = ctypes.c_ssize_t
                lib.TransformImageColorspace.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                lib.TransformImageColorspace.restype = ctypes.c_int
                lib.SetImageColorspace.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                lib.SetImageColorspace.restype = ctypes.c_int
                lib.GrayscaleImage.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
                lib.GrayscaleImage.restype = ctypes.c_int
                # MagickWand's posterize takes a dither method but no value of
                # it reproduces the CLI's dithered output; MagickCore's does.
                lib.PosterizeImage.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.c_int, ctypes.c_void_p]
                lib.PosterizeImage.restype = ctypes.c_int
                library.GetImageFromMagickWand.argtypes = [ctypes.c_void_p]
                library.GetImageFromMagickWand.restype = ctypes.c_void_p
                # Wand's image.profiles[...] = data calls MagickSetImageProfile,
                # which only *attaches* the blob. MagickProfileImage is the one
                # that performs the colour conversion, i.e. what -profile does.
                library.MagickProfileImage.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                                       ctypes.c_void_p, ctypes.c_size_t]
                library.MagickProfileImage.restype = ctypes.c_int
                self.lib = lib
                break
            except (OSError, AttributeError):
                continue

    @staticmethod
    def _candidates():
        names = ['libMagickCore-7.Q16HDRI.so', 'libMagickCore-7.Q16HDRI.so.10',
                 'libMagickCore-7.Q16.so', 'libMagickCore-6.Q16.so.6']
        home = os.environ.get('MAGICK_HOME')
        if home:
            for n in names:
                yield os.path.join(home, 'lib', n)
        for n in names:
            yield n

    @property
    def available(self):
        return self.lib is not None

    def resolve(self, option_group, name):
        """Name -> ImageMagick enum value, or None if it isn't recognised."""
        if not self.available:
            return None
        code = self.lib.ParseCommandOption(option_group, 0, str(name).encode())
        return None if code < 0 else code

    def _apply(self, image, fn, code):
        ptr = library.GetImageFromMagickWand(image.wand)
        exc = self.lib.AcquireExceptionInfo()
        try:
            # A NULL ExceptionInfo segfaults these calls, so it is always real.
            return bool(fn(ptr, code, exc))
        finally:
            self.lib.DestroyExceptionInfo(exc)

    def transform_colorspace(self, image, name, keep_values=True):
        """Apply `-colorspace <name>`.

        With keep_values, the transformed channel values are then *relabelled*
        as sRGB (`-set colorspace sRGB`) instead of being converted back.
        Without it, writing to an sRGB format such as JPEG or PNG converts the
        pixels straight back and the mutation vanishes -- which is what plain
        ImageMagick does, and why 23 of 31 colorspaces produced a file
        identical to the untouched input.
        """
        code = self.resolve(self.COLORSPACE_OPTIONS, name)
        if code is None:
            return False
        if not self._apply(image, self.lib.TransformImageColorspace, code):
            return False
        if keep_values:
            srgb = self.resolve(self.COLORSPACE_OPTIONS, 'sRGB')
            if srgb is not None:
                self._apply(image, self.lib.SetImageColorspace, srgb)
        return True

    @staticmethod
    def apply_icc(image, path):
        """Run `-profile <path>` on the image, converting its pixels."""
        with open(path, 'rb') as fh:
            data = fh.read()
        buf = ctypes.create_string_buffer(data)
        return bool(library.MagickProfileImage(image.wand, b'ICC', buf, len(data)))

    def posterize(self, image, levels, dither_method):
        """MagickCore PosterizeImage: DitherMethod enum, not a boolean.

        UndefinedDither=0, NoDither=1, Riemersma=2, FloydSteinberg=3.
        """
        if not self.available:
            return False
        ptr = library.GetImageFromMagickWand(image.wand)
        exc = self.lib.AcquireExceptionInfo()
        try:
            return bool(self.lib.PosterizeImage(ptr, int(levels), int(dither_method), exc))
        finally:
            self.lib.DestroyExceptionInfo(exc)

    def grayscale(self, image, method):
        code = self.resolve(self.INTENSITY_OPTIONS, method)
        if code is None:
            return False
        return self._apply(image, self.lib.GrayscaleImage, code)

    def supported(self, option_group, names):
        return [n for n in names if self.resolve(option_group, n) is not None]


MAGICK_CORE = _MagickCore()

# Everything `magick -list colorspace` offers that makes sense as a mutation.
COLORSPACE_CHOICES = [
    'CMY', 'CMYK', 'Gray', 'HCL', 'HCLp', 'HSB', 'HSI', 'HSL', 'HSV', 'Jzazbz',
    'Lab', 'LCHab', 'LCHuv', 'LMS', 'Log', 'OHTA', 'Oklab', 'Oklch',
    'Rec601YCbCr', 'Rec709YCbCr', 'RGB', 'scRGB', 'sRGB', 'Transparent',
    'xyY', 'YCbCr', 'YCC', 'YDbDr', 'YIQ', 'YPbPr', 'YUV',
]

# `magick -list intensity`
GRAYSCALE_CHOICES = [
    'Rec601Luma', 'Rec601Luminance', 'Rec709Luma', 'Rec709Luminance',
    'Brightness', 'Lightness', 'Average', 'MS', 'RMS',
]

# ---------------------------------------------------------------- ICC profiles
#
# `-profile <file.icc>` needs an actual profile file. Attaching one to an image
# that carries no profile only tags it -- measured: WideGamutRGB and ProPhotoRGB
# outputs were pixel-identical, and both differed from the input by exactly the
# same amount as a plain re-encode. A real conversion needs a source profile
# assigned first, which is what PROFILE_SOURCE does below.

# Bundled profiles only, by default. Every profile the tool offers now lives in
# assets/icc, so the catalogue is the same on every machine and a generated
# corpus reproduces across hosts -- it used to be 51 options here and 14 on a
# machine without colord-data, which silently changed the size of an
# augmentation run.
#
# Set PROFILE_SCAN_SYSTEM=true to also pick up whatever the host has installed.
# That is useful for exploring, and bad for reproducibility, which is why it is
# off unless asked for.
_BUNDLED_PROFILES = os.path.join(os.path.dirname(__file__), '..', '..', 'assets', 'icc')
_SYSTEM_PROFILE_PATHS = [
    '/usr/share/color/icc/colord',
    '/usr/share/color/icc',
    '/usr/share/color/icc/ghostscript',
    '/Library/ColorSync/Profiles',                               # macOS
]
PROFILE_SEARCH_PATHS = [
    _BUNDLED_PROFILES,
    os.path.join(os.path.dirname(__file__), '..', '..', 'profiles'),   # legacy location
]
if os.getenv('PROFILE_SCAN_SYSTEM', 'false').lower() in ('1', 'true', 'yes'):
    PROFILE_SEARCH_PATHS += _SYSTEM_PROFILE_PATHS

STRIP_PROFILES = 'Strip'   # the +profile '*' form: remove every profile

# One name for the grayscale default, because there were three. The UI sent
# Rec709Luma, the catalogue advertised Rec601Luma, and omitting the parameter
# took a third path entirely (colorspace = 'gray'), giving max pixel differences
# of 152, 172 and 27 between them for the same nominal "default grayscale".
DEFAULT_GRAYSCALE_METHOD = 'Rec709Luma'

# Ordered-dither threshold maps that ImageMagick accepts, checked with
# `magick -list threshold`.
DITHER_MAPS = ['o2x2', 'o3x3', 'o4x4', 'o8x8', 'h4x4a', 'h6x6a', 'h8x8a']


def _discover_profiles():
    """name -> path for every readable ICC profile on this machine."""
    found = {}
    for root in PROFILE_SEARCH_PATHS:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            if not entry.lower().endswith(('.icc', '.icm')):
                continue
            name = os.path.splitext(entry)[0]
            path = os.path.join(root, entry)
            if name not in found and os.path.isfile(path):
                found[name] = path
    return found


ICC_PROFILES = _discover_profiles()
# Source profile assumed for images that carry none of their own. Without this
# the destination profile is only attached and no pixel changes at all.
PROFILE_SOURCE = os.getenv('PROFILE_SOURCE', 'sRGB')


# Named-colour profiles (device class 'nmcl' — Crayons, x11-colors) are
# lookup tables of spot colours, not colour spaces, and ImageMagick refuses
# them as a conversion target. Read the class out of the ICC header rather
# than probing: a trial conversion per profile added 30s to startup.
USABLE_PROFILE_CLASSES = {b'scnr', b'mntr', b'prtr', b'link', b'spac', b'abst'}


def _usable_profiles(profiles):
    """Drop profiles that cannot be a conversion destination."""
    usable, rejected = {}, []
    for name, path in profiles.items():
        try:
            with open(path, 'rb') as fh:
                header = fh.read(16)
            if len(header) >= 16 and header[12:16] in USABLE_PROFILE_CLASSES:
                usable[name] = path
            else:
                rejected.append(name)
        except OSError:
            rejected.append(name)
    if rejected:
        logger.info("Skipping ICC profiles that are not conversion targets: %s",
                    ', '.join(sorted(rejected)))
    return usable


if ICC_PROFILES:
    ICC_PROFILES = _usable_profiles(ICC_PROFILES)

PROFILE_CHOICES = [STRIP_PROFILES] + sorted(ICC_PROFILES)
logger.info("ICC profiles available: %d (source: %s)", len(ICC_PROFILES), PROFILE_SOURCE)

if MAGICK_CORE.available:
    COLORSPACE_CHOICES = MAGICK_CORE.supported(_MagickCore.COLORSPACE_OPTIONS, COLORSPACE_CHOICES)
    GRAYSCALE_CHOICES = MAGICK_CORE.supported(_MagickCore.INTENSITY_OPTIONS, GRAYSCALE_CHOICES)


class ValidationError(ValueError):
    """Bad request from the caller — reported as 400, not 500."""


# What every mutation accepts. Nothing reaches ImageMagick unvalidated:
#   ('int'|'float', low, high)  numeric, clamped into range
#   ('choice', <callable returning the allowed values>)
#
# The upper bounds are cost limits as much as correctness ones. A 999x999
# median kernel on a small image never returns — it hung a worker thread
# indefinitely until this table capped it.
def _cs_choices():
    return COLORSPACE_CHOICES
def _gs_choices():
    return GRAYSCALE_CHOICES
def _profile_choices():
    return PROFILE_CHOICES

MUTATION_SPECS = {
    # continuous
    'blur':            {'sigma':      ('float', 0.0, 50.0)},
    'gaussian_blur':   {'radius':     ('float', 0.0, 50.0)},
    'black_threshold': {'percentage': ('float', 0.0, 100.0)},
    'border':          {'pixels':     ('int', 0, 500)},
    'charcoal':        {'radius':     ('float', 0.0, 20.0)},
    'brightness':      {'percentage': ('float', -100.0, 200.0)},
    'rotation':        {'degrees':    ('float', -360.0, 360.0)},
    'rotate':          {'degrees':    ('float', -360.0, 360.0)},
    'contrast':        {'percentage': ('float', -100.0, 100.0)},
    'saturation':      {'percentage': ('float', -100.0, 200.0)},
    'color_reduce':    {'colors':     ('int', 2, 256), 'dither': ('bool',)},
    'colors':          {'colors':     ('int', 2, 256), 'dither': ('bool',)},
    'edge':            {'radius':     ('float', 0.0, 20.0)},
    'emboss':          {'radius':     ('float', 0.0, 20.0)},
    'median':          {'kernel':     ('int', 1, 25)},
    'paint':           {'radius':     ('float', 0.0, 25.0)},
    'posterize':       {'levels':     ('int', 2, 256), 'dither': ('bool',)},
    'raise':           {'pixels':     ('int', 0, 200)},
    'rotational_blur': {'angle':      ('float', -360.0, 360.0)},
    'gamma':           {'gamma':      ('float', 0.01, 10.0)},
    'implode':         {'factor':     ('float', -5.0, 5.0)},
    'chop':            {'pixels':     ('int', 0, 10000),
                        'value':      ('int', 0, 10000),
                        'type':       ('choice', lambda: ['horizontal', 'vertical', 'center'])},
    # discrete
    'colorspace':      {'colorspace':  ('choice', _cs_choices),
                        'keep_values': ('bool',)},
    'grayscale':       {'method':      ('choice', _gs_choices)},
    'profile':         {'profile':     ('choice', _profile_choices),
                        'source':      ('choice', _profile_choices)},
    'colorize':        {'tone': ('choice', lambda: ['red', 'green', 'blue', 'yellow',
                                                    'cyan', 'magenta', 'sepia', 'grayscale'])},
    'annotate':        {'text':     ('text', 100),
                        'position': ('choice', lambda: ['center', 'north', 'south', 'east', 'west',
                                                        'northwest', 'northeast', 'southwest', 'southeast']),
                        'fontSize': ('int', 4, 400)},
    'flip': {}, 'flop': {}, 'monochrome': {}, 'negate': {}, 'normalize': {},
    # Parameterless operators verified against the binary: each runs with no
    # argument and demonstrably changes the image. -auto-orient, -flatten,
    # -trim and -clamp were rejected for changing nothing on ordinary input.
    'auto_level': {}, 'equalize': {}, 'auto_gamma': {}, 'despeckle': {},
    'enhance': {},
    # Tunable operators. Every bound below was checked against the binary, and
    # each has a differential-oracle case pinning it to the command line.
    # Twenty more operators, each verified pixel-identical to its command-line
    # form before being added. `-spread` was tested and rejected: it displaces
    # pixels randomly, which would break the byte-reproducibility guarantee.
    'adaptive_blur':      {'sigma':      ('float', 0.5, 15)},
    'adaptive_sharpen':   {'sigma':      ('float', 0.5, 15)},
    'blue_shift':         {'factor':     ('float', 0.1, 5)},
    'brightness_contrast':{'amount':     ('int',  -50, 50)},
    'canny':              {'lower':      ('int',   1,  50)},
    'kuwahara':           {'radius':     ('int',   1,  10)},
    'magnify':            {},
    'modulate':           {'brightness': ('int',   20, 200)},
    'motion_blur':        {'sigma':      ('float', 1,  25)},
    # The choice form takes a callable returning the allowed values, not the
    # list itself -- passing the list made validation try to call it.
    'ordered_dither':     {'threshold_map': ('choice', lambda: DITHER_MAPS)},
    'polaroid':           {'angle':      ('float', -30, 30)},
    'roll':               {'pixels':     ('int',   0,  200)},
    'selective_blur':     {'sigma':      ('float', 0.5, 15)},
    'shade':              {'azimuth':    ('int',   0,  360)},
    'sigmoidal_contrast': {'strength':   ('float', 0.5, 20)},
    'transpose':          {},
    'transverse':         {},
    'vignette':           {'sigma':      ('float', 1,  40)},
    'wavelet_denoise':    {'threshold':  ('int',   1,  30)},
    'white_threshold':    {'percentage': ('int',   0,  100)},
    'sharpen':        {'sigma':     ('float', 0.5, 10)},
    'unsharp':        {'sigma':     ('float', 0.5, 10)},
    'solarize':       {'threshold': ('int',   0,   100)},
    'sepia_tone':     {'threshold': ('int',   0,   100)},
    'tint':           {'percentage':('int',   0,   100)},
    'swirl':          {'degrees':   ('float', -180, 180)},
    'wave':           {'amplitude': ('float', 1,   40)},
    'shear':          {'degrees':   ('float', -45, 45)},
    'frame':          {'pixels':    ('int',   1,   60)},
    'mode_filter':    {'kernel':    ('int',   1,   10)},
    'resize':         {'percentage':('int',   25,  200)},
    'liquid_rescale': {'percentage':('int',   50,  150)},
}


def validate_params(mutation_name, params):
    """Coerce and range-check parameters. Raises ValidationError on bad input.

    Numeric values are clamped rather than rejected, and the clamped value is
    what gets recorded in the output filename, so the name always states what
    was actually applied.
    """
    spec = MUTATION_SPECS.get(mutation_name)
    if spec is None:
        raise ValidationError(f"Unknown mutation: {mutation_name}")
    if not isinstance(params, dict):
        raise ValidationError("parameters must be a JSON object")

    unknown = set(params) - set(spec)
    if unknown:
        allowed = ', '.join(sorted(spec)) or '(none)'
        raise ValidationError(
            f"{mutation_name} does not take {', '.join(sorted(unknown))}. Accepts: {allowed}"
        )

    clean = {}
    for key, value in params.items():
        if value is None:
            continue                      # treat null as "use the default"
        kind = spec[key][0]
        if kind in ('int', 'float'):
            _, low, high = spec[key]
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValidationError(f"{mutation_name}.{key} must be a number, got {value!r}")
            if number != number or number in (float('inf'), float('-inf')):
                raise ValidationError(f"{mutation_name}.{key} must be a finite number")
            number = max(low, min(high, number))
            clean[key] = int(round(number)) if kind == 'int' else number
        elif kind == 'choice':
            allowed = spec[key][1]()
            match = {str(a).lower(): a for a in allowed}.get(str(value).lower())
            if match is None:
                raise ValidationError(
                    f"{mutation_name}.{key} must be one of: {', '.join(map(str, allowed[:12]))}"
                    + ('…' if len(allowed) > 12 else '')
                )
            clean[key] = match
        elif kind == 'bool':
            clean[key] = str(value).lower() not in ('false', '0', 'no', '')
        elif kind == 'text':
            clean[key] = str(value)[:spec[key][1]]
    return clean


def format_params(params, mutation_name=''):
    """Render mutation parameters as a filename-safe suffix.

    blur   {'sigma': 5}           -> '_sigma5'
    rotate {'degrees': 15.0}      -> '_degrees15'
    gamma  {'gamma': 1.5}         -> '_1p5'          (key repeats the mutation)
    colorspace {'colorspace':'Gray'} -> '_Gray'
    negate {}                     -> ''

    Values are kept in the name so a generated dataset records the exact
    setting each image was produced with.
    """
    if not isinstance(params, dict) or not params:
        return ''
    parts = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, float):
            # 5.0 -> 5, 1.5 -> 1p5 (a dot would look like a file extension)
            value = int(value) if value.is_integer() else str(value).replace('.', 'p')
        text = ''.join(c for c in str(value) if c.isalnum())
        if not text:
            continue
        # Don't repeat the mutation name: gamma_gamma1p5 -> gamma_1p5
        label = '' if key.lower() == mutation_name.lower() else key
        parts.append(f"{label}{text}")
    return ('_' + '_'.join(parts)) if parts else ''


def _probe_imagemagick():
    """Work out which image formats this ImageMagick build can actually handle.

    A build without the png/tiff/webp delegates still loads fine, it just
    refuses those formats at read time, so probe rather than assume.
    """
    from wand.version import MAGICK_VERSION, configure_options
    try:
        delegates = set(configure_options('DELEGATES').get('DELEGATES', '').split())
    except Exception:
        delegates = set()

    # ext -> delegate that ImageMagick needs in order to handle it
    needs = {'.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.tiff': 'tiff',
             '.tif': 'tiff', '.webp': 'webp', '.gif': None, '.bmp': None}
    native, fallback = set(), set()
    for ext, delegate in needs.items():
        (native if (delegate is None or delegate in delegates) else fallback).add(ext)
    return MAGICK_VERSION, delegates, native, fallback


MAGICK_VERSION, MAGICK_DELEGATES, NATIVE_FORMATS, FALLBACK_FORMATS = _probe_imagemagick()

try:
    library.MagickSetIteratorIndex.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t]
    library.MagickSetIteratorIndex.restype = ctypes.c_bool
    library.MagickResetIterator.argtypes = [ctypes.c_void_p]
except Exception:  # pragma: no cover - older binding
    pass


# JPEG is re-encoded on the way out, so every JPEG picks up compression loss on
# top of whatever the filter did -- measurably: at quality 90 a mutation that
# changes no pixels at all still shifts them by up to 25 levels. PNG, TIFF and
# other lossless formats are unaffected. Raise this (up to 100) when the output
# is research data and that extra loss would confound the mutation.
JPEG_QUALITY = max(1, min(100, int(os.getenv('JPEG_QUALITY', 90))))

logger.info("ImageMagick: %s", MAGICK_VERSION)
logger.info("ImageMagick delegates: %s", ' '.join(sorted(MAGICK_DELEGATES)) or '(none)')
if FALLBACK_FORMATS:
    logger.warning(
        "No ImageMagick delegate for %s - these will use the PIL fallback, which "
        "approximates the ImageMagick operators. Run 'python3 doctor.py' for the fix.",
        ' '.join(sorted(FALLBACK_FORMATS))
    )

def apply_to_every_frame(image, func, **params):
    """Run one mutation over every frame of a multi-frame image.

    Wand's Image methods act on whichever frame the MagickWand iterator points
    at, and after loading a file that is the *last* frame. So an animated GIF
    came back with only its final frame mutated: HTTP 200, a file that opens
    cleanly, and three frames of untouched data behind it. The ImageMagick CLI
    applies an operator to every frame, and this restores that.

    Verified against ``magick anim.gif -negate out.gif``, which changes all
    four frames where the tool previously changed one.

    Every mutator returns the image it was handed (checked: 36 of 36), so
    looping and keeping the last return value is safe.
    """
    try:
        frames = len(image.sequence)
    except Exception:
        frames = 1

    if frames <= 1:
        return func(image, **params)

    result = image
    for index in range(frames):
        library.MagickSetIteratorIndex(image.wand, index)
        result = func(image, **params)
    library.MagickResetIterator(image.wand)
    return result


@functools.lru_cache(maxsize=128)
def font_for_text(text):
    """Path to a font that can actually render `text`, or None.

    ImageMagick's default font is Latin-only, and when a glyph is missing it
    draws *nothing* -- no error, no warning. So a CJK, Arabic or Greek caption
    came back as HTTP 200 with an image that had not been touched, which is the
    worst failure mode this tool has: a plausible-looking result that is wrong.

    fontconfig can pick a face by the exact code points required, which is what
    `fc-match :charset=...` does here. Cached because the answer only depends on
    the set of scripts in the string.
    """
    points = sorted({ord(c) for c in text if ord(c) > 32})
    if not points:
        return None
    charset = ' '.join(f'{c:x}' for c in points)
    try:
        out = subprocess.run(['fc-match', '-f', '%{file}', f'sans:charset={charset}'],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None          # no fontconfig on this host; fall back to the default
    path = out.stdout.strip()
    return path if path and os.path.isfile(path) else None


def strip_timestamps(image):
    """Remove the wall-clock metadata ImageMagick stamps into every output.

    ImageMagick writes ``date:create``, ``date:modify`` and ``date:timestamp``
    properties, and for PNG a ``tIME`` chunk. The pixels are identical run to
    run, but those fields carry the current second -- so the same input with the
    same parameters produces byte-different files depending only on when it ran.

    That breaks the two things this tool's output is used for: checking that a
    generated dataset reproduces, and de-duplicating it by content hash. Both
    compare bytes, and both were silently unreliable.

    Only the date fields are dropped. A blanket ``-strip`` would also discard
    the ICC profile, which the ``profile`` filter exists to manage.
    """
    for key in ('date:create', 'date:modify', 'date:timestamp'):
        try:
            del image.metadata[key]
        except Exception:
            pass          # absent for this format, or not deletable -- fine
    try:
        # PNG carries its own tIME chunk, independent of the properties above.
        image.options['png:exclude-chunk'] = 'date,time'
    except Exception:
        pass


# The mutation engine and the PIL fallback are defined inside create_app().
# mutation_worker.py needs both, so create_app publishes them here once built.
# Without this a worker process cannot reach ImageMutator at all.
MUTATOR_CLASS = None
PIL_MUTATION_FUNC = None


# ---- crash isolation -------------------------------------------------------
# ImageMagick can kill the process it runs in rather than raising: `median` on a
# stock build raises SIGFPE, which no try/except can catch. Mutations therefore
# run in worker processes, so a crash costs one worker and one request instead
# of the whole server.
#
#   MUTATION_ISOLATION=worker      (default) run mutations in worker processes
#   MUTATION_ISOLATION=inprocess   the old behaviour, for comparison or if the
#                                  pool causes trouble
#   MUTATION_WORKERS=N             how many workers (default 3)
#   MUTATION_TIMEOUT=N             seconds before a stuck mutation is killed
MUTATION_ISOLATION = os.getenv('MUTATION_ISOLATION', 'worker').strip().lower()
MUTATION_WORKERS = int(os.getenv('MUTATION_WORKERS', '3') or 3)
MUTATION_TIMEOUT = int(os.getenv('MUTATION_TIMEOUT', '120') or 120)

try:
    from worker_pool import WorkerPool, WorkerCrashed, WorkerTimeout
except ImportError:                      # pragma: no cover - worker_pool sits beside app.py
    WorkerPool = None

    class WorkerCrashed(RuntimeError):
        pass

    class WorkerTimeout(RuntimeError):
        pass

_WORKER_POOL = None
_WORKER_POOL_LOCK = threading.Lock()


def get_worker_pool():
    """The pool, started on first use. None means run in-process."""
    global _WORKER_POOL
    if MUTATION_ISOLATION != 'worker' or WorkerPool is None:
        return None
    if _WORKER_POOL is None:
        with _WORKER_POOL_LOCK:
            if _WORKER_POOL is None:
                try:
                    _WORKER_POOL = WorkerPool(size=MUTATION_WORKERS, python=sys.executable)
                except Exception:
                    # Better to serve without isolation than not to serve.
                    logger.exception('could not start mutation workers, '
                                     'falling back to in-process mutations')
                    return None
    return _WORKER_POOL


def create_app():
    """Create and configure Flask app"""
    app = Flask(__name__)
    
    # Configuration
    # Upload size limit. Unset (or 0) means no limit, which is the default:
    # the tool is used on whole image sets, and a silent 413 on a large file
    # looks like a broken filter rather than a size cap.
    _max_size = int(os.getenv('MAX_FILE_SIZE', 0) or 0)
    app.config['MAX_CONTENT_LENGTH'] = _max_size if _max_size > 0 else None
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), '..', '..', 'uploads')
    app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(__file__), '..', '..', 'outputs')
    
    # Ensure directories exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
    
    # Enable CORS
    CORS(app)
    
    # ==================== PIL MUTATION HELPERS ====================
    
    def apply_pil_mutation(pil_image, mutation_name, **params):
        """Apply mutations to PIL images for formats Wand can't handle.

        This path runs when the local ImageMagick build has no delegate for the
        input format (PNG, TIFF and WebP on a build without libpng/libtiff/
        libwebp). The operators here reproduce the ImageMagick ones as closely
        as PIL allows, but they are approximations, not the same code path --
        install the missing delegates if you need byte-identical semantics
        across every format.
        """
        from PIL import ImageOps, ImageEnhance, ImageFilter, ImageDraw

        img = pil_image.copy()
        
        if mutation_name == 'grayscale':
            # ImageMagick's intensity formulas, so the nine methods stay
            # distinct here too. Collapsing them all onto ImageOps.grayscale
            # reproduced, on the fallback path, exactly the defect this tool
            # had on the main path: nine settings, one image.
            method = str(params.get('method') or 'Rec709Luma')
            rgb = numpy.asarray(img.convert('RGB'), dtype=numpy.float64) / 255.0
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

            def linear(c):                       # sRGB -> linear light
                return numpy.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

            key = method.lower()
            if key == 'average':
                v = (r + g + b) / 3.0
            elif key == 'brightness':
                v = numpy.maximum(numpy.maximum(r, g), b)
            elif key == 'lightness':
                v = (numpy.maximum(numpy.maximum(r, g), b) +
                     numpy.minimum(numpy.minimum(r, g), b)) / 2.0
            elif key == 'ms':
                v = (r ** 2 + g ** 2 + b ** 2) / 3.0
            elif key == 'rms':
                v = numpy.sqrt((r ** 2 + g ** 2 + b ** 2) / 3.0)
            elif key == 'rec601luma':
                v = 0.298839 * r + 0.586811 * g + 0.114350 * b
            elif key == 'rec709luma':
                v = 0.212656 * r + 0.715158 * g + 0.072186 * b
            elif key == 'rec601luminance':
                lr, lg, lb = linear(r), linear(g), linear(b)
                v = 0.298839 * lr + 0.586811 * lg + 0.114350 * lb
            elif key == 'rec709luminance':
                lr, lg, lb = linear(r), linear(g), linear(b)
                v = 0.212656 * lr + 0.715158 * lg + 0.072186 * lb
            else:
                raise ValueError(f"Unsupported grayscale method: {method}")
            return PILImage.fromarray(
                numpy.clip(v * 255.0, 0, 255).astype(numpy.uint8), 'L')
        
        elif mutation_name == 'blur':
            sigma = float(params.get('sigma', 5))
            radius = sigma * 0.4
            return img.filter(ImageFilter.GaussianBlur(radius=radius))
        
        elif mutation_name == 'black_threshold':
            percentage = float(params.get('percentage', 25))
            threshold = int((percentage / 100.0) * 255)
            # Convert to grayscale, then apply threshold
            if img.mode != 'L':
                img_gray = ImageOps.grayscale(img)
            else:
                img_gray = img
            # Apply threshold
            img_threshold = ImageOps.autocontrast(img_gray)
            return img_threshold
        
        elif mutation_name == 'border':
            pixels = int(params.get('pixels', 10))
            border_color = params.get('color', (200, 200, 200))
            return ImageOps.expand(img, border=pixels, fill=border_color)
        
        elif mutation_name == 'charcoal':
            radius = float(params.get('radius', 5))
            # Create charcoal effect using edge detection + blur
            img_edge = img.filter(ImageFilter.FIND_EDGES)
            img_inv = ImageOps.invert(img_edge.convert('L'))
            return img_inv.convert('RGB')
        
        elif mutation_name == 'color_reduce':
            colors = int(params.get('colors', 8))
            if img.mode != 'P':
                img = img.quantize(colors=colors)
            return img
        
        elif mutation_name == 'colorize':
            tone = params.get('tone', 'blue').lower()
            gs = ImageOps.grayscale(img)
            gs_rgb = gs.convert('RGB')
            
            # Tone color mappings
            tone_colors = {
                'red': (255, 100, 100),
                'green': (100, 255, 100),
                'blue': (100, 100, 255),
                'yellow': (255, 255, 100),
                'cyan': (100, 255, 255),
                'magenta': (255, 100, 255),
                'sepia': (112, 66, 20)
            }
            
            tone_color = tone_colors.get(tone, (100, 100, 255))
            
            # Create colored overlay
            overlay = PILImage.new('RGB', img.size, tone_color)
            return PILImage.blend(gs_rgb, overlay, 0.4)
        
        elif mutation_name == 'colorspace':
            # Only Gray is faithfully representable here. The previous code
            # approximated Lab with grayscale and turned every other colorspace
            # into convert('RGB') -- a no-op that returned the input unchanged
            # while reporting success.
            name = str(params.get('colorspace', 'sRGB'))
            if name.lower() in ('gray', 'grey', 'lineargray'):
                return ImageOps.grayscale(img)
            if name.lower() in ('srgb', 'rgb'):
                return img.convert('RGB')
            raise ValueError(
                f"colorspace '{name}' needs ImageMagick, but this build has no "
                f"delegate for this image format. Convert the image to JPEG, or "
                f"install an ImageMagick with the missing delegate (see DOCUMENTATION.md section 3)."
            )
        
        elif mutation_name == 'annotate':
            text = params.get('text', 'Watermark').strip()
            if not text:
                return img
            position = params.get('position', 'center')
            font_size = int(params.get('fontSize', 36))
            
            from PIL import ImageFont
            canvas = img.copy()
            draw = ImageDraw.Draw(canvas)
            
            # Try to use default font, fallback to default
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except:
                font = ImageFont.load_default()
            
            # Calculate text position
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            img_width, img_height = canvas.size
            
            positions = {
                'center': ((img_width - text_width) // 2, (img_height - text_height) // 2),
                'northwest': (10, 10),
                'north': ((img_width - text_width) // 2, 10),
                'northeast': (img_width - text_width - 10, 10),
                'west': (10, (img_height - text_height) // 2),
                'east': (img_width - text_width - 10, (img_height - text_height) // 2),
                'southwest': (10, img_height - text_height - 10),
                'south': ((img_width - text_width) // 2, img_height - text_height - 10),
                'southeast': (img_width - text_width - 10, img_height - text_height - 10)
            }
            
            pos = positions.get(position, (10, 10))
            
            # Draw text with white color and black outline
            outline_range = 2
            for adj_x in range(-outline_range, outline_range + 1):
                for adj_y in range(-outline_range, outline_range + 1):
                    draw.text((pos[0] + adj_x, pos[1] + adj_y), text, font=font, fill=(0, 0, 0))
            draw.text(pos, text, font=font, fill=(255, 255, 255))
            
            return canvas
        
        elif mutation_name == 'chop':
            chop_type = params.get('type', 'horizontal')
            pixels = int(params.get('value', 50))
            
            width, height = img.size
            
            # The catalogue allows up to 200 pixels, which is more than half of
            # a small image. Unclamped, crop((200, 0, 40, 240)) puts the right
            # edge left of the left edge and PIL raises "Coordinate 'right' is
            # less than 'left'" -- a 400 on the fallback path for a request the
            # ImageMagick path completes. Leave at least one pixel on each axis.
            def _clamp(n, extent):
                return max(0, min(int(n), (extent - 1) // 2))

            if chop_type == 'horizontal':
                # Remove from left and right
                px = _clamp(pixels, width)
                return img.crop((px, 0, width - px, height))
            elif chop_type == 'vertical':
                # Remove from top and bottom
                py = _clamp(pixels, height)
                return img.crop((0, py, width, height - py))
            elif chop_type == 'center':
                # Crop from center
                px, py = _clamp(pixels, width), _clamp(pixels, height)
                return img.crop((px, py, width - px, height - py))
            else:
                return img
        
        elif mutation_name == 'brightness':
            percentage = float(params.get('percentage', 50))
            factor = (100 + percentage) / 100.0
            enhancer = ImageEnhance.Brightness(img)
            return enhancer.enhance(factor)
        
        elif mutation_name == 'rotation':
            degrees = float(params.get('degrees', 15))
            return img.rotate(degrees, expand=False, fillcolor='white')
        
        elif mutation_name == 'contrast':
            percentage = float(params.get('percentage', 50))
            factor = (100 + percentage) / 100.0
            enhancer = ImageEnhance.Contrast(img)
            return enhancer.enhance(factor)
        
        elif mutation_name == 'saturation':
            percentage = float(params.get('percentage', 50))
            factor = (100 + percentage) / 100.0
            enhancer = ImageEnhance.Color(img)
            return enhancer.enhance(factor)

        elif mutation_name == 'colors':
            n = max(2, int(params.get('colors', 256)))
            return img.convert('P', palette=PILImage.ADAPTIVE, colors=n).convert(img.mode)

        elif mutation_name == 'edge':
            return img.filter(ImageFilter.FIND_EDGES)

        elif mutation_name == 'emboss':
            return img.filter(ImageFilter.EMBOSS)

        elif mutation_name == 'median':
            size = max(1, int(params.get('kernel', 1)))
            if size % 2 == 0:
                size += 1  # PIL requires an odd kernel
            return img.filter(ImageFilter.MedianFilter(size=size))

        elif mutation_name == 'paint':
            size = max(1, int(params.get('radius', 1)))
            return img.filter(ImageFilter.ModeFilter(size=size))

        elif mutation_name == 'posterize':
            levels = max(2, min(8, int(params.get('levels', 4))))
            return ImageOps.posterize(img.convert('RGB'), levels)

        elif mutation_name == 'rotate':
            return img.rotate(-float(params.get('degrees', 90)), expand=True, fillcolor='white')

        elif mutation_name == 'gamma':
            g = max(0.01, float(params.get('gamma', 1.0)))
            table = [min(255, int(((i / 255.0) ** (1.0 / g)) * 255)) for i in range(256)]
            return img.convert('RGB').point(table * 3)

        elif mutation_name == 'gaussian_blur':
            return img.filter(ImageFilter.GaussianBlur(radius=float(params.get('radius', 5))))

        elif mutation_name == 'flip':
            return ImageOps.flip(img)

        elif mutation_name == 'flop':
            return ImageOps.mirror(img)

        elif mutation_name == 'negate':
            return ImageOps.invert(img.convert('RGB'))

        elif mutation_name == 'normalize':
            return ImageOps.autocontrast(img.convert('RGB'))

        elif mutation_name == 'monochrome':
            return img.convert('1')

        elif mutation_name == 'profile':
            name = str(params.get('profile') or STRIP_PROFILES)
            if name == STRIP_PROFILES:
                # Metadata only, by definition; PIL drops it on re-save.
                img.info.pop('icc_profile', None)
                return img
            path = ICC_PROFILES.get(name)
            if not path:
                raise ValueError(f"Unknown ICC profile: {name}")
            # Real conversion via LittleCMS. Popping the key used to be the
            # whole implementation, so every profile returned the input.
            try:
                from PIL import ImageCms
            except ImportError:
                raise ValueError(
                    f"applying ICC profile '{name}' needs either ImageMagick with a "
                    f"delegate for this format, or Pillow built with LittleCMS"
                )
            src_path = ICC_PROFILES.get(str(params.get('source') or PROFILE_SOURCE))
            src = (ImageCms.getOpenProfile(src_path) if src_path
                   else ImageCms.createProfile('sRGB'))
            out = ImageCms.profileToProfile(
                img.convert('RGB'), src, ImageCms.getOpenProfile(path),
                outputMode='RGB')
            if out is None:
                raise ValueError(f"LittleCMS could not apply profile: {name}")
            return out

        elif mutation_name == 'rotational_blur':
            # Average successive rotations about the centre.
            angle = float(params.get('angle', 10))
            base = img.convert('RGB')
            steps = max(2, min(int(abs(angle)), 45))
            acc = numpy.zeros((base.size[1], base.size[0], 3), dtype=numpy.float64)
            for i in range(steps):
                theta = angle * (i / float(steps - 1)) - angle / 2.0
                acc += numpy.asarray(
                    base.rotate(theta, resample=PILImage.BICUBIC), dtype=numpy.float64
                )
            return PILImage.fromarray(
                numpy.clip(acc / steps, 0, 255).astype(numpy.uint8), 'RGB'
            )

        elif mutation_name == 'implode':
            # Pull pixels toward the centre (negative values push them out),
            # following ImageMagick's factor = sin(pi/2 * d/r) ** -amount.
            amount = float(params.get('factor', 0.5))
            base = img.convert('RGB')
            src = numpy.asarray(base, dtype=numpy.float64)
            h, w = src.shape[:2]
            cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
            radius = min(cx, cy)
            yy, xx = numpy.mgrid[0:h, 0:w].astype(numpy.float64)
            dx, dy = xx - cx, yy - cy
            dist = numpy.sqrt(dx * dx + dy * dy)
            with numpy.errstate(divide='ignore', invalid='ignore'):
                factor = numpy.sin(numpy.pi / 2.0 * numpy.clip(dist / radius, 0, 1)) ** (-amount)
            factor = numpy.where(numpy.isfinite(factor), factor, 1.0)
            factor = numpy.where(dist < radius, factor, 1.0)
            sx = numpy.clip(cx + factor * dx, 0, w - 1)
            sy = numpy.clip(cy + factor * dy, 0, h - 1)
            # Bilinear sample so the result stays smooth.
            x0, y0 = numpy.floor(sx).astype(int), numpy.floor(sy).astype(int)
            x1, y1 = numpy.minimum(x0 + 1, w - 1), numpy.minimum(y0 + 1, h - 1)
            wx, wy = (sx - x0)[..., None], (sy - y0)[..., None]
            out = (src[y0, x0] * (1 - wx) * (1 - wy) + src[y0, x1] * wx * (1 - wy) +
                   src[y1, x0] * (1 - wx) * wy + src[y1, x1] * wx * wy)
            return PILImage.fromarray(numpy.clip(out, 0, 255).astype(numpy.uint8), 'RGB')

        elif mutation_name == 'raise':
            # 3-D bevel: lighten the top/left bands, darken the bottom/right.
            px = max(0, int(params.get('pixels', 10)))
            base = img.convert('RGB')
            out = numpy.asarray(base, dtype=numpy.float64).copy()
            h, w = out.shape[:2]
            px = min(px, h // 2, w // 2)
            for i in range(px):
                out[i, i:w - i] *= 1.25                 # top -> highlight
                out[h - 1 - i, i:w - i] *= 0.75         # bottom -> shadow
                out[i:h - i, i] *= 1.25                 # left -> highlight
                out[i:h - i, w - 1 - i] *= 0.75         # right -> shadow
            return PILImage.fromarray(numpy.clip(out, 0, 255).astype(numpy.uint8), 'RGB')

        else:
            # Returning `img` unchanged here would hand back an image that is
            # labelled as mutated but is byte-identical to the input, which
            # would silently corrupt any dataset built with this tool.
            raise ValueError(
                f"Mutation '{mutation_name}' is not supported for this image format "
                f"(ImageMagick could not read it, and there is no PIL fallback)"
            )
    
    # ==================== MUTATION IMPLEMENTATIONS ====================
    
    class ImageMutator:
        """Image mutation processor using ImageMagick"""
        
        @staticmethod
        def blur(image, sigma=5):
            """Apply Gaussian blur using ImageMagick"""
            sigma = float(sigma)
            image.blur(radius=sigma*0.4, sigma=sigma)
            return image
        
        @staticmethod
        def black_threshold(image, percentage=25):
            """Apply black threshold to image (-black-threshold 25%)"""
            percentage = max(0.0, min(100.0, float(percentage)))
            # Wand expects a Color for the threshold, not a raw quantum value
            image.black_threshold(threshold=Color('gray(%s%%)' % percentage))
            return image
        
        @staticmethod
        def border(image, pixels=10):
            """Add colored border to image"""
            pixels = int(pixels)
            image.border(width=pixels, height=pixels, color=Color('gray(200)'))
            return image
        
        @staticmethod
        def charcoal(image, radius=5):
            """Apply charcoal sketch effect"""
            radius = float(radius)
            # Wand charcoal requires both radius and sigma parameters
            # sigma controls the blur effect (higher = more blur)
            sigma = radius * 0.5  # Proportional sigma based on radius
            image.charcoal(radius=radius, sigma=sigma)
            return image
        
        @staticmethod
        def brightness(image, percentage=0):
            """Adjust brightness using ImageMagick (-modulate)"""
            percentage = float(percentage)
            # Convert percentage to brightness multiplier (0-200%)
            factor = max(0.0, 100 + percentage)  # ranges from 0 to 200
            # `image.brightness = ...` is not a Wand property, it silently does
            # nothing. -modulate is the operator that actually changes pixels.
            image.modulate(brightness=factor, saturation=100, hue=100)
            return image
        
        @staticmethod
        def rotation(image, degrees=15):
            """Rotate image using ImageMagick"""
            angle = float(degrees)
            image.rotate(-angle)
            return image
        
        @staticmethod
        def contrast(image, percentage=0):
            """Enhance or reduce contrast (-contrast / +contrast).

            Called with no parameters by the discrete filter, which is the
            plain ImageMagick `-contrast` (one enhancement pass). A positive
            percentage applies more passes, a negative one reduces contrast.
            """
            percentage = float(percentage)
            # `image.contrast` is a method on Wand's Image; assigning to it
            # merely shadows the method and changes no pixels.
            if percentage == 0:
                passes, sharpen = 1, True
            else:
                passes, sharpen = max(1, int(round(abs(percentage) / 25.0))), percentage > 0
            for _ in range(min(passes, 10)):
                image.contrast(sharpen=sharpen)
            return image
        
        @staticmethod
        def saturation(image, percentage=0):
            """Adjust color saturation using ImageMagick"""
            percentage = float(percentage)
            # Convert percentage to saturation multiplier
            factor = 100 + percentage
            image.modulate(saturation=factor)
            return image
        
        @staticmethod
        def color_reduce(image, colors=8):
            """Reduce color palette using ImageMagick quantization"""
            num_colors = int(colors)
            # Quantize to specified number of colors
            image.quantize(number_colors=num_colors)
            return image
        
        @staticmethod
        def colorize(image, tone='blue'):
            """Apply color tint using ImageMagick"""
            tone = str(tone).lower()
            
            if tone == 'grayscale':
                image.colorspace = 'gray'
            else:
                # Apply color tint by modulating hue and saturation
                hue_shifts = {
                    'red': 0,
                    'green': 120,
                    'blue': 240,
                    'yellow': -60,
                    'cyan': 180,
                    'magenta': 300,
                    'sepia': -30
                }
                hue = hue_shifts.get(tone, 0)
                # Modulate is modulation(brightness, saturation, hue)
                image.modulate(brightness=100, saturation=150, hue=hue)
            
            return image
        
        @staticmethod
        def colorspace(image, colorspace='sRGB', keep_values=True):
            """-colorspace <value>: convert to any colorspace this build supports.

            Was a hand-written if/elif covering 7 names, everything else
            silently falling through to RGB. Now resolved by name through
            ImageMagick, so all of `magick -list colorspace` works.
            """
            name = str(colorspace)
            keep = str(keep_values).lower() not in ('false', '0', 'no')
            if MAGICK_CORE.available and MAGICK_CORE.transform_colorspace(image, name, keep):
                return image
            # Fallback for a build where MagickCore could not be loaded.
            try:
                image.colorspace = name.lower()
            except Exception:
                raise ValueError(f"Unsupported colorspace: {colorspace}")
            return image
        
        @staticmethod
        def annotate(image, text='Watermark', position='center', fontSize='36'):
            """Add text annotation to image"""
            from wand.drawing import Drawing
            
            text_str = str(text)[:100]
            font_size = int(fontSize)
            
            with Drawing() as draw:
                draw.font_size = font_size
                draw.fill_color = Color('white')
                draw.stroke_color = Color('black')
                draw.stroke_width = 2

                # Pick a face that covers this text's scripts. Without this the
                # default Latin-only font silently drew nothing for CJK, Arabic
                # and Greek.
                face = font_for_text(text_str)
                if face:
                    draw.font = face
                
                # Calculate text metrics
                metrics = draw.get_font_metrics(image, text_str)
                text_width = metrics[4] - metrics[0]
                text_height = metrics[5] - metrics[1]
                
                img_width = image.width
                img_height = image.height
                
                # Position mappings
                positions = {
                    'center': ((img_width - text_width) / 2, (img_height - text_height) / 2),
                    'northwest': (10, 20),
                    'north': ((img_width - text_width) / 2, 20),
                    'northeast': (img_width - text_width - 10, 20),
                    'west': (10, (img_height - text_height) / 2),
                    'east': (img_width - text_width - 10, (img_height - text_height) / 2),
                    'southwest': (10, img_height - text_height - 10),
                    'south': ((img_width - text_width) / 2, img_height - text_height - 10),
                    'southeast': (img_width - text_width - 10, img_height - text_height - 10)
                }
                
                x, y = positions.get(position, (10, 20))

                # Text wider or taller than the image makes these negative, and
                # Wand's draw.text rejects a negative origin outright -- so a
                # 100-character caption, which the API's own max_length allows,
                # failed with "x=-882 must be a positive integer" on a small
                # image. Clamp to the top-left instead: the text starts at the
                # edge and overflows, which is what `-gravity center -annotate`
                # does on the command line.
                x = max(0, int(x))
                y = max(0, int(y))

                # The baseline sits at y, so y=0 would push the glyphs off the
                # top. Keep at least one line of headroom.
                y = max(y, int(font_size))

                # Signature before and after, so a draw that produces no
                # glyphs is reported rather than returned as a success. This is
                # the backstop for the case above: if no installed font covers
                # the text, say so instead of handing back the input.
                before = image.signature
                draw.text(x, y, text_str)
                draw(image)

            if image.signature == before:
                raise ValueError(
                    f"nothing was drawn for {text_str[:24]!r} — no installed font "
                    f"has glyphs for it. Install a font covering this script "
                    f"(for example fonts-noto) and try again"
                )

            return image
        
        @staticmethod
        def chop(image, pixels=None, type='horizontal', value=50):
            """Chop parts of the image.

            The UI sends `pixels`; `type`/`value` are kept for older API callers.
            """
            amount = int(pixels if pixels is not None else value)
            img_width = image.width
            img_height = image.height

            # Never chop away the whole image: leave at least 1px in each axis.
            max_h = max(0, (img_width - 1) // 2)
            max_v = max(0, (img_height - 1) // 2)

            if type == 'horizontal':
                px = min(amount, max_h)
                image.crop(left=px, top=0, right=img_width - px, bottom=img_height)
            elif type == 'vertical':
                py = min(amount, max_v)
                image.crop(left=0, top=py, right=img_width, bottom=img_height - py)
            elif type == 'center':
                px, py = min(amount, max_h), min(amount, max_v)
                image.crop(left=px, top=py, right=img_width - px, bottom=img_height - py)

            return image
        
        @staticmethod
        def grayscale(image, method=None, **kwargs):
            """-grayscale <method>: convert using a chosen intensity formula.

            The method used to be ignored entirely — every grayscale variant
            produced the same image. Each of `magick -list intensity` now gives
            a genuinely different result.
            """
            # An absent method used to fall through to colorspace='gray', a
            # different operator producing a visibly different image from the
            # one the interface produces. Default to the declared method so all
            # three routes agree.
            method = method or DEFAULT_GRAYSCALE_METHOD
            if MAGICK_CORE.available and MAGICK_CORE.grayscale(image, method):
                return image
            if MAGICK_CORE.available:
                raise ValueError(f"Unsupported grayscale method: {method}")
            image.colorspace = 'gray'   # only when MagickCore is unavailable
            return image

        # ---------- continuous filters from the master filter catalogue ----------

        @staticmethod
        def colors(image, colors=256, dither=True):
            """-colors: reduce the image to N colours (quantization).

            Dithering defaults to ON to match `magick input -colors N output`.
            Wand's quantize() defaults it OFF, which made the tool's output
            differ from the documented command line (max channel difference
            168 on a test image). Pass dither=false for the undithered result.
            """
            use_dither = str(dither).lower() not in ('false', '0', 'no')
            image.quantize(number_colors=max(2, int(colors)), dither=use_dither)
            return image

        @staticmethod
        def edge(image, radius=1):
            """-edge radius: highlight object boundaries"""
            image.edge(radius=max(0.0, float(radius)))
            return image

        @staticmethod
        def emboss(image, radius=1):
            """-emboss: three-dimensional light/shadow effect"""
            radius = max(0.0, float(radius))
            image.emboss(radius=radius, sigma=radius * 0.5)
            return image

        @staticmethod
        def median(image, kernel=1):
            """-statistic median: median filter, smooths noise but keeps edges"""
            k = max(1, int(kernel))
            image.statistic('median', width=k, height=k)
            return image

        @staticmethod
        def paint(image, radius=1):
            """-paint radius: simulate an oil painting"""
            radius = max(0.0, float(radius))
            image.oil_paint(radius=radius, sigma=radius * 0.5)
            return image

        @staticmethod
        def posterize(image, levels=4, dither=True):
            """-posterize levels: limit colour levels per channel.

            Same story as `colors`: the CLI dithers by default (Riemersma),
            Wand does not.
            """
            levels = max(2, int(levels))
            want_dither = str(dither).lower() not in ('false', '0', 'no')
            # 2 = Riemersma (the CLI default), 1 = NoDither
            if MAGICK_CORE.available and MAGICK_CORE.posterize(image, levels, 2 if want_dither else 1):
                return image
            image.posterize(levels=levels, dither='no')   # fallback: undithered
            return image

        @staticmethod
        def raise_edges(image, pixels=10):
            """-raise thickness: 3-D raised-button edge effect.

            Wand exposes no wrapper for this, so call MagickRaiseImage directly.
            Registered under the name 'raise' via MUTATION_ALIASES.
            """
            px = max(0, int(pixels))
            library.MagickRaiseImage.argtypes = [
                ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
                ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_int,
            ]
            library.MagickRaiseImage.restype = ctypes.c_int
            if not library.MagickRaiseImage(image.wand, px, px, 0, 0, 1):
                raise RuntimeError('ImageMagick could not apply the raise effect')
            return image

        @staticmethod
        def rotate(image, degrees=90):
            """-rotate degrees: positive values rotate clockwise"""
            image.rotate(float(degrees), background=Color('white'))
            return image

        @staticmethod
        def rotational_blur(image, angle=10):
            """-rotational-blur angle: blur around the centre of the image"""
            image.rotational_blur(angle=float(angle))
            return image

        @staticmethod
        def gamma(image, gamma=1.0):
            """-gamma value: <1 darkens, >1 brightens"""
            image.gamma(adjustment_value=max(0.01, float(gamma)))
            return image

        @staticmethod
        def implode(image, factor=0.5):
            """-implode factor: positive pulls pixels in, negative pushes out"""
            image.implode(amount=float(factor))
            return image

        @staticmethod
        def gaussian_blur(image, radius=5):
            """-gaussian-blur radius: blur with a Gaussian operator"""
            radius = max(0.0, float(radius))
            image.gaussian_blur(radius=radius, sigma=max(radius / 2.0, 0.1))
            return image

        # ---------- discrete filters from the master filter catalogue ----------

        @staticmethod
        def profile(image, profile=None, source=None, **kwargs):
            """-profile <file.icc>: convert the image into an ICC profile.

            `profile=Strip` (or no argument) is the `+profile '*'` form and
            removes every profile — metadata only, no pixel change.

            Any other name applies that ICC profile. An image carrying no
            profile of its own is first assigned `source` (sRGB by default),
            because ImageMagick only *tags* an untagged image otherwise: two
            different destination profiles then produce pixel-identical output.
            With the source assigned, each destination gives a real conversion.
            """
            name = str(profile) if profile else STRIP_PROFILES

            if name == STRIP_PROFILES:
                for key in list(image.profiles.keys()):
                    del image.profiles[key]
                return image

            path = ICC_PROFILES.get(name)
            if not path:
                raise ValueError(
                    f"Unknown ICC profile: {name}. "
                    f"Available: {', '.join(PROFILE_CHOICES[:8])}…"
                )

            # Give it a source colour space to convert *from*, if it has none.
            # Membership must be tested against keys(): Wand's ProfileDict
            # returns None for a missing profile rather than raising KeyError,
            # so `'icc' in image.profiles` is True even when the image carries
            # no profile at all — which silently skipped this whole step.
            existing = set(image.profiles.keys())
            if not existing & {'icc', 'icm'}:
                src_path = ICC_PROFILES.get(str(source or PROFILE_SOURCE))
                if src_path:
                    _MagickCore.apply_icc(image, src_path)

            if not _MagickCore.apply_icc(image, path):
                raise ValueError(f"ImageMagick could not apply profile: {name}")
            return image

        @staticmethod
        def flip(image, **kwargs):
            """-flip: mirror vertically (upside-down)"""
            image.flip()
            return image

        @staticmethod
        def flop(image, **kwargs):
            """-flop: mirror horizontally"""
            image.flop()
            return image

        @staticmethod
        def monochrome(image, **kwargs):
            """-monochrome: transform the image to black and white"""
            image.type = 'bilevel'
            return image

        @staticmethod
        def adaptive_blur(image, sigma=3.0, **kwargs):
            """-adaptive-blur 0xsigma: blur that preserves edges."""
            image.adaptive_blur(radius=0, sigma=float(sigma)); return image

        @staticmethod
        def adaptive_sharpen(image, sigma=3.0, **kwargs):
            """-adaptive-sharpen 0xsigma: sharpen more strongly near edges."""
            image.adaptive_sharpen(radius=0, sigma=float(sigma)); return image

        @staticmethod
        def blue_shift(image, factor=1.5, **kwargs):
            """-blue-shift: the "moonlight" effect."""
            image.blue_shift(factor=float(factor)); return image

        @staticmethod
        def brightness_contrast(image, amount=20, **kwargs):
            """-brightness-contrast NxN: shift brightness and contrast together."""
            a = float(amount)
            image.brightness_contrast(brightness=a, contrast=a); return image

        @staticmethod
        def canny(image, lower=10, **kwargs):
            """-canny 0x1+L%+30%: Canny edge detection."""
            image.canny(radius=0, sigma=1, lower_percent=float(lower) / 100.0,
                        upper_percent=0.3)
            return image

        @staticmethod
        def kuwahara(image, radius=3, **kwargs):
            """-kuwahara RxS: edge-preserving smoothing, a painterly look."""
            r = float(radius)
            image.kuwahara(radius=r, sigma=r * 0.5); return image

        @staticmethod
        def magnify(image, **kwargs):
            """-magnify: double the size with the default filter."""
            image.magnify(); return image

        @staticmethod
        def modulate(image, brightness=120, **kwargs):
            """-modulate B,100,100: scale brightness in HSL."""
            image.modulate(brightness=float(brightness), saturation=100, hue=100)
            return image

        @staticmethod
        def motion_blur(image, sigma=5.0, **kwargs):
            """-motion-blur 0xsigma+45: directional blur."""
            image.motion_blur(radius=0, sigma=float(sigma), angle=45); return image

        @staticmethod
        def ordered_dither(image, threshold_map='o4x4', **kwargs):
            """-ordered-dither MAP: dither using an ordered threshold map."""
            image.ordered_dither(str(threshold_map)); return image

        @staticmethod
        def polaroid(image, angle=6.0, **kwargs):
            """-polaroid N: a rotated instant-photo border."""
            image.polaroid(angle=float(angle)); return image

        @staticmethod
        def roll(image, pixels=30, **kwargs):
            """-roll +N+0: wrap the image horizontally."""
            image.roll(x=int(pixels), y=0); return image

        @staticmethod
        def selective_blur(image, sigma=3.0, **kwargs):
            """-selective-blur 0xsigma+10%: blur only within a tonal threshold."""
            image.selective_blur(radius=0, sigma=float(sigma),
                                 threshold=0.1 * image.quantum_range)
            return image

        @staticmethod
        def shade(image, azimuth=120, **kwargs):
            """-shade AxE: light the image as a height field."""
            image.shade(gray=True, azimuth=float(azimuth), elevation=30); return image

        @staticmethod
        def sigmoidal_contrast(image, strength=5.0, **kwargs):
            """-sigmoidal-contrast Nx50%: non-linear contrast."""
            image.sigmoidal_contrast(sharpen=True, strength=float(strength),
                                     midpoint=0.5 * image.quantum_range)
            return image

        @staticmethod
        def transpose(image, **kwargs):
            """-transpose: flip about the leading diagonal."""
            image.transpose(); return image

        @staticmethod
        def transverse(image, **kwargs):
            """-transverse: flip about the trailing diagonal."""
            image.transverse(); return image

        @staticmethod
        def vignette(image, sigma=10.0, **kwargs):
            """-vignette 0xsigma+10+10: darken toward the corners."""
            image.vignette(radius=0, sigma=float(sigma), x=10, y=10); return image

        @staticmethod
        def wavelet_denoise(image, threshold=5, **kwargs):
            """-wavelet-denoise N%: wavelet-domain noise removal."""
            image.wavelet_denoise(threshold=(float(threshold) / 100.0) * image.quantum_range,
                                  softness=0.0)
            return image

        @staticmethod
        def white_threshold(image, percentage=60, **kwargs):
            """-white-threshold N%: force everything above the threshold to white."""
            image.white_threshold(Color(f'gray({int(percentage)}%)')); return image

        @staticmethod
        def sharpen(image, sigma=2.0, **kwargs):
            """-sharpen 0xsigma: sharpen with a Gaussian operator."""
            image.sharpen(radius=0, sigma=float(sigma))
            return image

        @staticmethod
        def unsharp(image, sigma=1.5, **kwargs):
            """-unsharp 0xsigma+1.0+0.05: unsharp mask."""
            image.unsharp_mask(radius=0, sigma=float(sigma),
                               amount=1.0, threshold=0.05)
            return image

        @staticmethod
        def solarize(image, threshold=50, **kwargs):
            """-solarize N%: invert every pixel above the threshold."""
            image.solarize(threshold=(float(threshold) / 100.0) * image.quantum_range)
            return image

        @staticmethod
        def sepia_tone(image, threshold=80, **kwargs):
            """-sepia-tone N%: the classic photographic tone."""
            image.sepia_tone(threshold=float(threshold) / 100.0)
            return image

        @staticmethod
        def tint(image, percentage=50, **kwargs):
            """-fill red -tint N%: blend the image toward a tint colour."""
            image.tint(color=Color('red'),
                       alpha=Color(f'rgb({percentage}%,{percentage}%,{percentage}%)'))
            return image

        @staticmethod
        def swirl(image, degrees=90.0, **kwargs):
            """-swirl N: rotate pixels about the centre, falling off outward."""
            image.swirl(degree=float(degrees))
            return image

        @staticmethod
        def wave(image, amplitude=10.0, **kwargs):
            """-wave AxW: a sine wave along the vertical axis."""
            image.wave(amplitude=float(amplitude), wave_length=60)
            return image

        @staticmethod
        def shear(image, degrees=10.0, **kwargs):
            """-background white -shear Nx0: slide rows sideways."""
            image.shear(background=Color('white'), x=float(degrees))
            return image

        @staticmethod
        def frame(image, pixels=10, **kwargs):
            """-frame NxN: add a bevelled border.

            The matte colour must be set explicitly: Wand's default differs from
            the CLI's, which put the two 63 quantum levels apart.
            """
            image.frame(matte=Color('#BDBDBD'), width=int(pixels), height=int(pixels))
            return image

        @staticmethod
        def mode_filter(image, kernel=3, **kwargs):
            """-statistic mode NxN: replace each pixel with its neighbourhood mode."""
            k = int(kernel)
            image.statistic('mode', width=k, height=k)
            return image

        @staticmethod
        def resize(image, percentage=150, **kwargs):
            """-resize N%: scale the image."""
            f = float(percentage) / 100.0
            image.resize(max(1, int(image.width * f)), max(1, int(image.height * f)))
            return image

        @staticmethod
        def liquid_rescale(image, percentage=80, **kwargs):
            """-liquid-rescale N%: content-aware scaling (seam carving)."""
            f = float(percentage) / 100.0
            image.liquid_rescale(max(1, int(image.width * f)), max(1, int(image.height * f)))
            return image

        @staticmethod
        def auto_level(image, **kwargs):
            """-auto-level: stretch each channel to the full available range."""
            image.auto_level()
            return image

        @staticmethod
        def equalize(image, **kwargs):
            """-equalize: histogram equalisation."""
            image.equalize()
            return image

        @staticmethod
        def auto_gamma(image, **kwargs):
            """-auto-gamma: gamma correction from the image's own mean."""
            image.auto_gamma()
            return image

        @staticmethod
        def despeckle(image, **kwargs):
            """-despeckle: reduce speckle noise while preserving edges."""
            image.despeckle()
            return image

        @staticmethod
        def enhance(image, **kwargs):
            """-enhance: noise-reducing digital filter."""
            image.enhance()
            return image

        @staticmethod
        def negate(image, **kwargs):
            """-negate: replace each pixel with its complementary colour"""
            image.negate()
            return image

        @staticmethod
        def normalize(image, **kwargs):
            """-normalize: stretch intensity values across the full range"""
            image.normalize()
            return image

    # Names the UI sends that cannot be Python identifiers or that differ
    # from the method name on ImageMutator.
    MUTATION_ALIASES = {
        'raise': 'raise_edges',
    }

    # Publish the engine so a worker process can use it (see the note by the
    # module-level declarations).
    global MUTATOR_CLASS, PIL_MUTATION_FUNC
    MUTATOR_CLASS = ImageMutator
    PIL_MUTATION_FUNC = apply_pil_mutation

    # ==================== API ENDPOINTS ====================
    
    @app.route('/', methods=['GET'])
    def index():
        """Landing route.

        Port 5000 is the API, not the interface; without this, visiting it in a
        browser returned a bare 404 that looked like the tool was down.
        """
        return {
            'name': 'Image Mutation Tool — API',
            'version': '1.0',
            'web_interface': 'http://localhost:3000',
            'note': 'This port serves the API only. Open the web interface above.',
            'endpoints': {
                'GET  /api/health': 'engine, delegates, and which formats are native',
                'GET  /api/mutations': 'the filter catalogue with parameter ranges',
                'POST /api/mutate': 'apply one filter to one image',
                'GET  /api/image/<file>': 'view a result',
                'GET  /api/download/<file>': 'download a result',
                'POST /api/download-batch': 'download several as a ZIP',
            },
        }, 200

    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint.

        Reports which ImageMagick is bound and which formats it can handle, so
        a build missing delegates is visible instead of silently degrading to
        the PIL fallback.
        """
        return {
            'status': 'healthy',
            'version': '1.0.0',
            'message': 'Image Mutation Tool API is running',
            'imagemagick': {
                'version': MAGICK_VERSION,
                'delegates': sorted(MAGICK_DELEGATES),
                'native_formats': sorted(NATIVE_FORMATS),
                'pil_fallback_formats': sorted(FALLBACK_FORMATS),
                'core_bound': MAGICK_CORE.available,
                'colorspaces': COLORSPACE_CHOICES,
                'grayscale_methods': GRAYSCALE_CHOICES,
                'icc_profiles': PROFILE_CHOICES,
                'profile_source': PROFILE_SOURCE,
            }
        }, 200
    
    @app.route('/api/mutations', methods=['GET'])
    def get_mutations():
        """Get all available mutations"""
        mutations = {
            'continuous': {
                'blur': {
                    'name': 'Blur (Gaussian)',
                    'description': 'Apply Gaussian blur effect to soften image',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {
                            'type': 'float',
                            'min': 0.1,
                            'max': 20,
                            'step': 0.5,
                            'default': 3,   # matches the slider's starting value
                            'description': 'Blur strength - 0.1=sharp, 20=extreme blur'
                        }
                    }
                },
                'sharpen': {
                    'name': 'Sharpen',
                    'description': 'Sharpen edges with a Gaussian operator',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {
                            'type': 'float',
                            'min': 0.5,
                            'max': 10,
                            'default': 2,
                            'step': 0.5,
                            'description': 'Sharpening strength'
                        }
                    }
                },
                'unsharp': {
                    'name': 'Unsharp Mask',
                    'description': 'Classic unsharp masking',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {
                            'type': 'float',
                            'min': 0.5,
                            'max': 10,
                            'default': 1.5,
                            'step': 0.5,
                            'description': 'Mask radius'
                        }
                    }
                },
                'solarize': {
                    'name': 'Solarize',
                    'description': 'Invert every pixel above a brightness threshold',
                    'category': 'continuous',
                    'parameters': {
                        'threshold': {
                            'type': 'int',
                            'min': 0,
                            'max': 100,
                            'default': 50,
                            'step': 5,
                            'description': 'Threshold percentage'
                        }
                    }
                },
                'sepia_tone': {
                    'name': 'Sepia Tone',
                    'description': 'The classic photographic sepia tone',
                    'category': 'continuous',
                    'parameters': {
                        'threshold': {
                            'type': 'int',
                            'min': 0,
                            'max': 100,
                            'default': 80,
                            'step': 5,
                            'description': 'Tone threshold percentage'
                        }
                    }
                },
                'tint': {
                    'name': 'Tint',
                    'description': 'Blend the image toward a tint colour',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': 0,
                            'max': 100,
                            'default': 50,
                            'step': 5,
                            'description': 'Tint strength'
                        }
                    }
                },
                'swirl': {
                    'name': 'Swirl',
                    'description': 'Rotate pixels about the centre, falling off outward',
                    'category': 'continuous',
                    'parameters': {
                        'degrees': {
                            'type': 'float',
                            'min': -180,
                            'max': 180,
                            'default': 90,
                            'step': 15,
                            'description': 'Swirl angle'
                        }
                    }
                },
                'wave': {
                    'name': 'Wave',
                    'description': 'Displace rows along a sine wave',
                    'category': 'continuous',
                    'parameters': {
                        'amplitude': {
                            'type': 'float',
                            'min': 1,
                            'max': 40,
                            'default': 10,
                            'step': 1,
                            'description': 'Wave amplitude'
                        }
                    }
                },
                'shear': {
                    'name': 'Shear',
                    'description': 'Slide rows sideways, slanting the image',
                    'category': 'continuous',
                    'parameters': {
                        'degrees': {
                            'type': 'float',
                            'min': -45,
                            'max': 45,
                            'default': 10,
                            'step': 5,
                            'description': 'Shear angle'
                        }
                    }
                },
                'frame': {
                    'name': 'Frame',
                    'description': 'Add a bevelled border',
                    'category': 'continuous',
                    'parameters': {
                        'pixels': {
                            'type': 'int',
                            'min': 1,
                            'max': 60,
                            'default': 10,
                            'step': 1,
                            'description': 'Frame width in pixels'
                        }
                    }
                },
                'mode_filter': {
                    'name': 'Mode Filter',
                    'description': 'Replace each pixel with its neighbourhood mode',
                    'category': 'continuous',
                    'parameters': {
                        'kernel': {
                            'type': 'int',
                            'min': 1,
                            'max': 10,
                            'default': 3,
                            'step': 1,
                            'description': 'Kernel size'
                        }
                    }
                },
                'resize': {
                    'name': 'Resize',
                    'description': 'Scale the image up or down',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': 25,
                            'max': 200,
                            'default': 150,
                            'step': 25,
                            'description': 'Scale percentage'
                        }
                    }
                },
                'liquid_rescale': {
                    'name': 'Liquid Rescale',
                    'description': 'Content-aware scaling by seam carving',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': 50,
                            'max': 150,
                            'default': 80,
                            'step': 10,
                            'description': 'Target percentage'
                        }
                    }
                },
                'adaptive_blur': {
                    'name': 'Adaptive Blur',
                    'description': 'Blur that preserves edges',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {'type': 'float', 'min': 0.5, 'max': 15,
                                  'default': 3, 'step': 0.5,
                                  'description': 'Blur strength'}
                    }
                },
                'adaptive_sharpen': {
                    'name': 'Adaptive Sharpen',
                    'description': 'Sharpen more strongly near edges',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {'type': 'float', 'min': 0.5, 'max': 15,
                                  'default': 3, 'step': 0.5,
                                  'description': 'Sharpen strength'}
                    }
                },
                'blue_shift': {
                    'name': 'Blue Shift',
                    'description': 'The moonlight effect',
                    'category': 'continuous',
                    'parameters': {
                        'factor': {'type': 'float', 'min': 0.1, 'max': 5,
                                  'default': 1.5, 'step': 0.1,
                                  'description': 'Shift factor'}
                    }
                },
                'brightness_contrast': {
                    'name': 'Brightness & Contrast',
                    'description': 'Shift brightness and contrast together',
                    'category': 'continuous',
                    'parameters': {
                        'amount': {'type': 'int', 'min': -50, 'max': 50,
                                  'default': 20, 'step': 5,
                                  'description': 'Amount'}
                    }
                },
                'canny': {
                    'name': 'Canny Edges',
                    'description': 'Canny edge detection',
                    'category': 'continuous',
                    'parameters': {
                        'lower': {'type': 'int', 'min': 1, 'max': 50,
                                  'default': 10, 'step': 1,
                                  'description': 'Lower threshold percent'}
                    }
                },
                'kuwahara': {
                    'name': 'Kuwahara',
                    'description': 'Edge-preserving smoothing, a painterly look',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {'type': 'int', 'min': 1, 'max': 10,
                                  'default': 3, 'step': 1,
                                  'description': 'Radius'}
                    }
                },
                'modulate': {
                    'name': 'Modulate',
                    'description': 'Scale brightness in HSL',
                    'category': 'continuous',
                    'parameters': {
                        'brightness': {'type': 'int', 'min': 20, 'max': 200,
                                  'default': 120, 'step': 5,
                                  'description': 'Brightness percent'}
                    }
                },
                'motion_blur': {
                    'name': 'Motion Blur',
                    'description': 'Directional blur at 45 degrees',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {'type': 'float', 'min': 1, 'max': 25,
                                  'default': 5, 'step': 1,
                                  'description': 'Blur strength'}
                    }
                },
                'polaroid': {
                    'name': 'Polaroid',
                    'description': 'A rotated instant-photo border',
                    'category': 'continuous',
                    'parameters': {
                        'angle': {'type': 'float', 'min': -30, 'max': 30,
                                  'default': 6, 'step': 1,
                                  'description': 'Tilt angle'}
                    }
                },
                'roll': {
                    'name': 'Roll',
                    'description': 'Wrap the image horizontally',
                    'category': 'continuous',
                    'parameters': {
                        'pixels': {'type': 'int', 'min': 0, 'max': 200,
                                  'default': 30, 'step': 5,
                                  'description': 'Roll distance'}
                    }
                },
                'selective_blur': {
                    'name': 'Selective Blur',
                    'description': 'Blur only within a tonal threshold',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {'type': 'float', 'min': 0.5, 'max': 15,
                                  'default': 3, 'step': 0.5,
                                  'description': 'Blur strength'}
                    }
                },
                'shade': {
                    'name': 'Shade',
                    'description': 'Light the image as a height field',
                    'category': 'continuous',
                    'parameters': {
                        'azimuth': {'type': 'int', 'min': 0, 'max': 360,
                                  'default': 120, 'step': 15,
                                  'description': 'Light azimuth'}
                    }
                },
                'sigmoidal_contrast': {
                    'name': 'Sigmoidal Contrast',
                    'description': 'Non-linear contrast curve',
                    'category': 'continuous',
                    'parameters': {
                        'strength': {'type': 'float', 'min': 0.5, 'max': 20,
                                  'default': 5, 'step': 0.5,
                                  'description': 'Curve strength'}
                    }
                },
                'vignette': {
                    'name': 'Vignette',
                    'description': 'Darken toward the corners',
                    'category': 'continuous',
                    'parameters': {
                        'sigma': {'type': 'float', 'min': 1, 'max': 40,
                                  'default': 10, 'step': 1,
                                  'description': 'Falloff'}
                    }
                },
                'wavelet_denoise': {
                    'name': 'Wavelet Denoise',
                    'description': 'Wavelet-domain noise removal',
                    'category': 'continuous',
                    'parameters': {
                        'threshold': {'type': 'int', 'min': 1, 'max': 30,
                                  'default': 5, 'step': 1,
                                  'description': 'Threshold percent'}
                    }
                },
                'white_threshold': {
                    'name': 'White Threshold',
                    'description': 'Force everything above the threshold to white',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {'type': 'int', 'min': 0, 'max': 100,
                                  'default': 60, 'step': 5,
                                  'description': 'Threshold percent'}
                    }
                },
                'black_threshold': {
                    'name': 'Black Threshold',
                    'description': 'Convert pixels below threshold to black',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': 0,
                            'max': 100,
                            'step': 5,
                            'default': 0,   # matches the slider's starting value
                            'description': 'Brightness threshold percentage'
                        }
                    }
                },
                'border': {
                    'name': 'Border',
                    'description': 'Add colored border around image',
                    'category': 'continuous',
                    'parameters': {
                        'pixels': {
                            'type': 'int',
                            'min': 0,
                            'max': 100,
                            'step': 5,
                            'default': 10,
                            'description': 'Border width in pixels'
                        }
                    }
                },
                'charcoal': {
                    'name': 'Charcoal Effect',
                    'description': 'Apply charcoal/sketch effect to image',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {
                            'type': 'float',
                            'min': 0.2,
                            'max': 10,
                            'step': 0.2,
                            'default': 5,
                            'description': 'Sketch intensity - 0.2=subtle, 10=heavy'
                        }
                    }
                },
                'color_reduce': {
                    'name': 'Color Palette',
                    'description': 'Reduce number of colors in palette',
                    'category': 'continuous',
                    'parameters': {
                        'colors': {
                            'type': 'int',
                            'min': 2,
                            'max': 256,
                            'step': 1,
                            'default': 16,
                            'description': 'Number of colors - 2=B&W, 256=full'
                        }
                    }
                },
                'brightness': {
                    'name': 'Brightness',
                    'description': 'Adjust image brightness',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': -100,
                            'max': 100,
                            'step': 5,
                            'default': 0,
                            'description': 'Brightness adjustment (-100 to +100)'
                        }
                    }
                },
                'rotation': {
                    'name': 'Rotation',
                    'description': 'Rotate image by angle',
                    'category': 'continuous',
                    'parameters': {
                        'degrees': {
                            'type': 'float',
                            'min': 0,
                            'max': 360,
                            'step': 5,
                            'default': 0,
                            'description': 'Rotation angle in degrees'
                        }
                    }
                },
                'contrast': {
                    'name': 'Contrast',
                    'description': 'Adjust image contrast',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': -100,
                            'max': 100,
                            'step': 5,
                            'default': 0,
                            'description': 'Contrast adjustment percentage'
                        }
                    }
                },
                'saturation': {
                    'name': 'Saturation',
                    'description': 'Adjust color saturation',
                    'category': 'continuous',
                    'parameters': {
                        'percentage': {
                            'type': 'int',
                            'min': -100,
                            'max': 200,
                            'step': 10,
                            'default': 0,
                            'description': 'Saturation adjustment percentage'
                        }
                    }
                },
                'colors': {
                    'name': 'Colors',
                    'description': 'Reduce the image to N colors (quantization)',
                    'category': 'continuous',
                    'parameters': {
                        'colors': {'type': 'int', 'min': 2, 'max': 256, 'step': 1,
                                   'default': 256, 'description': 'Number of colors to keep'}
                    }
                },
                'edge': {
                    'name': 'Edge Detect',
                    'description': 'Highlight the boundaries of objects',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {'type': 'int', 'min': 1, 'max': 10, 'step': 1,
                                   'default': 1, 'description': 'Edge detection radius'}
                    }
                },
                'emboss': {
                    'name': 'Emboss',
                    'description': 'Three-dimensional light and shadow effect',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {'type': 'float', 'min': 0.5, 'max': 5, 'step': 0.5,
                                   'default': 1, 'description': 'Emboss radius'}
                    }
                },
                'median': {
                    'name': 'Median Filter',
                    'description': 'Smooth noise while preserving edges',
                    'category': 'continuous',
                    'parameters': {
                        'kernel': {'type': 'int', 'min': 1, 'max': 10, 'step': 1,
                                   'default': 1, 'description': 'Median kernel size'}
                    }
                },
                'paint': {
                    'name': 'Oil Paint',
                    'description': 'Simulate an oil painting',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {'type': 'int', 'min': 1, 'max': 10, 'step': 1,
                                   'default': 1, 'description': 'Paint radius'}
                    }
                },
                'posterize': {
                    'name': 'Posterize',
                    'description': 'Limit the number of color levels per channel',
                    'category': 'continuous',
                    'parameters': {
                        'levels': {'type': 'int', 'min': 2, 'max': 8, 'step': 1,
                                   'default': 4, 'description': 'Color levels per channel'}
                    }
                },
                'raise': {
                    'name': 'Raise',
                    'description': '3-D raised edge effect (does not resize the image)',
                    'category': 'continuous',
                    'parameters': {
                        'pixels': {'type': 'int', 'min': 1, 'max': 50, 'step': 1,
                                   'default': 10, 'description': 'Bevel thickness in pixels'}
                    }
                },
                'rotate': {
                    'name': 'Rotate',
                    'description': 'Rotate the image clockwise about its center',
                    'category': 'continuous',
                    'parameters': {
                        'degrees': {'type': 'int', 'min': 0, 'max': 360, 'step': 15,
                                    'default': 90, 'description': 'Rotation angle in degrees'}
                    }
                },
                'rotational_blur': {
                    'name': 'Rotational Blur',
                    'description': 'Blur around the center of the image',
                    'category': 'continuous',
                    'parameters': {
                        'angle': {'type': 'int', 'min': 1, 'max': 90, 'step': 1,
                                  'default': 10, 'description': 'Blur angle in degrees'}
                    }
                },
                'gamma': {
                    'name': 'Gamma',
                    'description': 'Gamma correction - below 1 darkens, above 1 brightens',
                    'category': 'continuous',
                    'parameters': {
                        'gamma': {'type': 'float', 'min': 0.5, 'max': 5, 'step': 0.1,
                                  'default': 1.0, 'description': 'Gamma value'}
                    }
                },
                'implode': {
                    'name': 'Implode',
                    'description': 'Pull pixels toward the center - negative values explode',
                    'category': 'continuous',
                    'parameters': {
                        'factor': {'type': 'float', 'min': -2, 'max': 2, 'step': 0.1,
                                   'default': 0.5, 'description': 'Implode factor'}
                    }
                },
                'gaussian_blur': {
                    'name': 'Gaussian Blur',
                    'description': 'Blur the image with a Gaussian operator',
                    'category': 'continuous',
                    'parameters': {
                        'radius': {'type': 'int', 'min': 1, 'max': 20, 'step': 1,
                                   'default': 5, 'description': 'Blur radius'}
                    }
                }
            },
            'discrete': {
                'profile': {
                    'name': 'Profile',
                    'description': ("Apply an ICC profile, or 'Strip' to remove all profiles. "
                                    "An untagged image is assigned the source profile first so "
                                    "the conversion actually changes pixels."),
                    'category': 'discrete',
                    'parameters': {
                        'profile': {
                            'type': 'choice',
                            'options': PROFILE_CHOICES,
                            'default': STRIP_PROFILES,
                            'description': 'ICC profile to convert into'
                        }
                    }
                },
                'flip': {
                    'name': 'Flip',
                    'description': 'Mirror the image vertically',
                    'category': 'discrete',
                    'parameters': {}
                },
                'flop': {
                    'name': 'Flop',
                    'description': 'Mirror the image horizontally',
                    'category': 'discrete',
                    'parameters': {}
                },
                'monochrome': {
                    'name': 'Monochrome',
                    'description': 'Transform the image to black and white',
                    'category': 'discrete',
                    'parameters': {}
                },
                'magnify': {
                    'name': 'Magnify',
                    'description': 'Double the size with the default filter',
                    'category': 'discrete',
                    'parameters': {}
                },
                'transpose': {
                    'name': 'Transpose',
                    'description': 'Flip about the leading diagonal',
                    'category': 'discrete',
                    'parameters': {}
                },
                'transverse': {
                    'name': 'Transverse',
                    'description': 'Flip about the trailing diagonal',
                    'category': 'discrete',
                    'parameters': {}
                },
                'ordered_dither': {
                    'name': 'Ordered Dither',
                    'description': 'Dither using an ordered threshold map',
                    'category': 'discrete',
                    'parameters': {
                        'threshold_map': {
                            'type': 'choice',
                            'options': DITHER_MAPS,
                            'default': 'o4x4',
                            'description': 'Threshold map'
                        }
                    }
                },
                'auto_level': {
                    'name': 'Auto Level',
                    'description': 'Stretch each channel to the full available range',
                    'category': 'discrete',
                    'parameters': {}
                },
                'equalize': {
                    'name': 'Equalize',
                    'description': 'Histogram equalisation across the image',
                    'category': 'discrete',
                    'parameters': {}
                },
                'auto_gamma': {
                    'name': 'Auto Gamma',
                    'description': "Gamma correction from the image's own mean",
                    'category': 'discrete',
                    'parameters': {}
                },
                'despeckle': {
                    'name': 'Despeckle',
                    'description': 'Reduce speckle noise while preserving edges',
                    'category': 'discrete',
                    'parameters': {}
                },
                'enhance': {
                    'name': 'Enhance',
                    'description': 'Noise-reducing digital filter',
                    'category': 'discrete',
                    'parameters': {}
                },
                'negate': {
                    'name': 'Negate',
                    'description': 'Replace each pixel with its complementary color',
                    'category': 'discrete',
                    'parameters': {}
                },
                'normalize': {
                    'name': 'Normalize',
                    'description': 'Stretch intensity values across the full range',
                    'category': 'discrete',
                    'parameters': {}
                },
                'colorize': {
                    'name': 'Colorize',
                    'description': 'Apply color tone to grayscale image',
                    'category': 'discrete',
                    'parameters': {
                        'tone': {
                            'type': 'choice',
                            'options': ['red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'sepia'],
                            'default': 'blue',
                            'description': 'Color tone to apply'
                        }
                    }
                },
                'colorspace': {
                    'name': 'Colorspace Conversion',
                    'description': 'Convert to different color model',
                    'category': 'discrete',
                    'parameters': {
                        'colorspace': {
                            'type': 'choice',
                            'options': COLORSPACE_CHOICES,
                            'default': 'sRGB',   # matches the checked radio
                            'description': 'Target color model'
                        }
                    }
                },
                'annotate': {
                    'name': 'Text Annotation',
                    'description': 'Add text watermark to image',
                    'category': 'discrete',
                    'parameters': {
                        'text': {
                            'type': 'string',
                            'max_length': 100,
                            'default': '',
                            'description': 'Text to add'
                        },
                        'position': {
                            'type': 'choice',
                            'options': ['center', 'northwest', 'north', 'northeast', 'west', 'east', 'southwest', 'south', 'southeast'],
                            'default': 'center',
                            'description': 'Text position'
                        },
                        'fontSize': {
                            'type': 'choice',
                            'options': ['24', '36', '48', '72'],
                            'default': '36',
                            'description': 'Font size'
                        }
                    }
                },
                'chop': {
                    'name': 'Chop/Crop',
                    'description': 'Crop parts of image',
                    'category': 'discrete',
                    'parameters': {
                        'type': {
                            'type': 'choice',
                            'options': ['horizontal', 'vertical', 'center'],
                            'default': 'horizontal',
                            'description': 'Crop direction'
                        },
                        'value': {
                            'type': 'int',
                            'min': 1,
                            'max': 200,
                            'default': 50,
                            'description': 'Pixels to remove'
                        }
                    }
                },
                'grayscale': {
                    'name': 'Grayscale',
                    'description': 'Convert to grayscale using a chosen intensity formula',
                    'category': 'discrete',
                    'parameters': {
                        'method': {
                            'type': 'choice',
                            'options': GRAYSCALE_CHOICES,
                            # Not GRAYSCALE_CHOICES[0]: that is Rec601Luma, while
                            # the interface has always sent Rec709Luma.
                            'default': DEFAULT_GRAYSCALE_METHOD,
                            'description': 'Intensity formula (magick -list intensity)'
                        }
                    }
                }
            }
        }
        
        return mutations, 200
    
    @app.route('/api/mutate', methods=['POST'])
    def mutate_image():
        """Apply mutation to image using ImageMagick via Wand"""
        try:
            # Check if files are in request
            if 'image' not in request.files:
                return {'error': 'No image file provided'}, 400
            
            file = request.files['image']
            if file.filename == '':
                return {'error': 'No file selected'}, 400
            
            # Get mutation and parameters
            mutation_name = request.form.get('mutation')
            params_str = request.form.get('parameters', '{}')
            
            if not mutation_name:
                return {'error': 'No mutation specified'}, 400

            # Parse and validate parameters up front, so nothing unchecked
            # reaches ImageMagick. Bad input is the caller's fault: 400, not 500.
            try:
                params = json.loads(params_str) if params_str.strip() else {}
            except ValueError:
                return {'error': f'parameters is not valid JSON: {params_str[:60]}'}, 400

            resolved_name = MUTATION_ALIASES.get(mutation_name, mutation_name)
            try:
                params = validate_params(mutation_name, params)
            except ValidationError as exc:
                return {'error': str(exc)}, 400
            
            # Extract original filename and extension
            original_filename = file.filename
            name_without_ext = os.path.splitext(original_filename)[0]
            file_ext = os.path.splitext(original_filename)[1].lower()
            
            # Map extensions to format names
            ext_to_format = {
                '.jpg': 'jpeg',
                '.jpeg': 'jpeg',
                '.png': 'png',
                '.gif': 'gif',
                '.webp': 'webp',
                '.bmp': 'bmp',
                '.tiff': 'tiff',
                '.tif': 'tiff'
            }
            
            # Determine output format (preserve original)
            original_format = ext_to_format.get(file_ext, 'jpeg')
            
            # Open image
            try:
                # Read file bytes
                file.seek(0)
                image_bytes = file.read()
                
                # Try to open directly with Wand first
                image = None
                use_pil = False
                try:
                    image = WandImage(blob=image_bytes)
                except Exception as exc:
                    # If Wand fails, fall back to PIL for the entire pipeline.
                    # Python deletes the `as` name at the end of the block, so
                    # keep the text: re-raising the variable later produced
                    # "local variable 'wand_error' referenced before assignment"
                    # as the user-facing message for every unreadable file.
                    wand_error = str(exc)
                    logger.warning("Wand could not open the upload (%s), trying PIL", wand_error)
                    use_pil = True

                if use_pil:
                    # Use PIL to open and validate
                    try:
                        pil_img = PILImage.open(BytesIO(image_bytes))
                        pil_img.load()          # force a decode: catches truncated data
                        image = pil_img
                        logger.info(f"Opened image with PIL: {pil_img.format} {pil_img.mode}")
                    except Exception as pil_error:
                        logger.error(f"PIL open failed: {pil_error}")
                        raise ValueError(
                            f"neither ImageMagick nor Pillow could read it "
                            f"(ImageMagick: {wand_error}; Pillow: {pil_error})"
                        )
                        
            except Exception as e:
                return {'error': f'Invalid image file: {str(e)}'}, 400
            
            # Save original with unique ID, preserving format
            # 8 hex chars is 32 bits; with thousands of files already in
            # outputs/ and augmentation writing hundreds per run, widen it so a
            # new result cannot overwrite an earlier one.
            unique_id = uuid.uuid4().hex[:16]
            storage_original_filename = f"{unique_id}_original{file_ext}"
            original_path = os.path.join(app.config['OUTPUT_FOLDER'], storage_original_filename)
            
            if use_pil:
                # Save PIL image in original format
                pil_save_kwargs = {}
                if original_format == 'jpeg':
                    pil_save_kwargs['quality'] = JPEG_QUALITY
                image.save(original_path, format=original_format.upper() if original_format != 'tiff' else 'TIFF', **pil_save_kwargs)
            else:
                # Save Wand image
                image.format = original_format
                strip_timestamps(image)
                image.save(filename=original_path)
            
            # Apply mutation
            # Only names in MUTATION_SPECS are callable. A bare getattr let any
            # attribute through -- `mutation=__init__` reached ImageMutator's
            # constructor and returned a 500.
            mutator = ImageMutator()
            mutation_func = getattr(mutator, resolved_name, None)
            if not callable(mutation_func):
                return {'error': f'Unknown mutation: {mutation_name}'}, 400

            # The result path is settled before the mutation runs, because the
            # worker writes straight to it.
            storage_result_filename = f"{unique_id}_result{file_ext}"
            result_path = os.path.join(app.config['OUTPUT_FOLDER'], storage_result_filename)

            _t_started = time.time()
            try:
                if use_pil:
                    _in_w, _in_h = image.size[0], image.size[1]
                else:
                    # After loading a multi-frame file the iterator sits on the
                    # last frame, and an optimised GIF frame is a sub-rectangle
                    # of the canvas -- reading it here reported 68x56 for a
                    # 120x90 animation. Rewind so the trace shows the canvas.
                    library.MagickResetIterator(image.wand)
                    _in_w, _in_h = image.width, image.height
            except Exception:
                _in_w = _in_h = 0

            pool = get_worker_pool()
            try:
                if pool is not None:
                    # Isolated path. ImageMagick runs in a worker process, so an
                    # operator that kills its process -- `median` does exactly
                    # that on some builds, with SIGFPE -- takes the worker down
                    # and not the server. Set MUTATION_ISOLATION=inprocess to
                    # turn this off and get the old in-process behaviour back.
                    reply = pool.submit({
                        'input_path': original_path,
                        'output_path': result_path,
                        'mutation': mutation_name,
                        'resolved': resolved_name,
                        'params': params,
                        'format': original_format,
                        'use_pil': use_pil,
                        'jpeg_quality': JPEG_QUALITY,
                    }, timeout=MUTATION_TIMEOUT)

                    if not reply.get('ok'):
                        kind = reply.get('kind', 'server')
                        msg = reply.get('error', 'mutation failed')
                        if kind == 'client':
                            return {'error': f'Mutation failed: {msg}'}, 400
                        logger.error('Mutation %s failed in worker: %s', mutation_name, msg)
                        return {'error': f'Mutation failed: {msg}'}, 500

                    _out_w = reply.get('width', 0)
                    _out_h = reply.get('height', 0)
                    _frames = reply.get('frames', 1)
                else:
                    if use_pil:
                        # For PIL images, apply simple PIL-based mutations
                        mutated_image = apply_pil_mutation(image, mutation_name, **params)
                    else:
                        # For Wand images, use the existing mutator. Routed through
                        # apply_to_every_frame so animated GIFs and multi-page TIFFs
                        # get the operator on every frame, as the CLI does.
                        mutated_image = apply_to_every_frame(image, mutation_func, **params)

                    if use_pil:
                        pil_save_kwargs = {}
                        if original_format == 'jpeg':
                            pil_save_kwargs['quality'] = JPEG_QUALITY
                        mutated_image.save(
                            result_path,
                            format=original_format.upper() if original_format != 'tiff' else 'TIFF',
                            **pil_save_kwargs)
                        _out_w, _out_h = mutated_image.size[0], mutated_image.size[1]
                        _frames = 1
                    else:
                        if not mutated_image.format or mutated_image.format.lower() != original_format:
                            try:
                                mutated_image.format = original_format
                            except Exception as fmt_error:
                                logger.warning(f"Could not set format to {original_format}: {fmt_error}")
                        if original_format == 'jpeg':
                            mutated_image.compression_quality = JPEG_QUALITY
                        strip_timestamps(mutated_image)
                        mutated_image.save(filename=result_path)
                        _out_w, _out_h = mutated_image.width, mutated_image.height
                        _frames = (len(mutated_image.sequence)
                                   if hasattr(mutated_image, 'sequence') else 1)

                # One line per applied filter, at INFO, so the terminal running
                # the tool shows what each request actually did: which engine
                # handled it, the parameters after validation and clamping, and
                # how long ImageMagick took. Set LOG_LEVEL=DEBUG in src/backend/.env
                # for the before/after geometry as well.
                _elapsed_ms = (time.time() - _t_started) * 1000
                logger.info('%-16s %-22s %s  %s  %.0fms',
                            resolved_name,
                            json.dumps(params, sort_keys=True)[:22],
                            'PIL-fallback' if use_pil else 'ImageMagick',
                            original_format,
                            _elapsed_ms)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug('    %s: %dx%d -> %dx%d, %d frame(s)',
                                 original_filename, _in_w, _in_h, _out_w, _out_h, _frames)
            except WorkerTimeout as e:
                logger.error('Mutation %s timed out: %s', mutation_name, e)
                return {'error': f'Mutation timed out after {MUTATION_TIMEOUT}s — '
                                 f'try a smaller image or a lower setting'}, 504
            except WorkerCrashed as e:
                # The whole point of the worker: this used to take the server
                # down with it.
                logger.error('Mutation %s crashed the worker: %s', mutation_name, e)
                return {'error': f"'{mutation_name}' could not run here: {e}"}, 500
            except ValidationError as e:
                return {'error': str(e)}, 400
            except ValueError as e:
                # Bad argument that slipped past validation - still the caller's.
                return {'error': f'Mutation failed: {e}'}, 400
            except MemoryError:
                return {'error': 'Mutation needs more memory than is available — '
                                 'try a smaller image or a lower setting'}, 507
            except Exception as e:
                logger.exception("Mutation %s failed", mutation_name)
                return {'error': f'Mutation failed: {e}'}, 500
            
            # Create user-friendly download filename, carrying the mutation and
            # its parameter values so a generated set stays traceable:
            #   photo.jpg + blur sigma=5      -> photo_blur_sigma5.jpg
            #   photo.jpg + rotate degrees=15 -> photo_rotate_degrees15.jpg
            #   photo.jpg + negate            -> photo_negate.jpg
            safe_name = "".join(c for c in name_without_ext if c.isalnum() or c in ('-', '_', ' ')).strip()
            download_filename = f"{safe_name}_{mutation_name}{format_params(params, mutation_name)}{file_ext}"
            
            # Build absolute URLs for CORS compatibility
            base_url = request.url_root.rstrip('/')
            original_url = f'{base_url}/api/image/{storage_original_filename}'
            result_url = f'{base_url}/api/image/{storage_result_filename}'
            download_url = f'{base_url}/api/download/{storage_result_filename}?filename={download_filename}'
            
            # Return response with URLs and metadata
            return {
                'success': True,
                'mutation': mutation_name,
                'parameters': params,
                'original_filename': original_filename,
                'download_filename': download_filename,
                'original_url': original_url,
                'result_url': result_url,
                'download_url': download_url,
                'timestamp': datetime.now().isoformat()
            }, 200
        
        except Exception as e:
            logger.error(f"Mutation error: {str(e)}")
            return {'error': f'Server error: {str(e)}'}, 500
    

    MIME_BY_EXT = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
        '.tiff': 'image/tiff', '.tif': 'image/tiff',
    }

    def resolve_output_file(filename):
        """Map a request filename to a real file inside OUTPUT_FOLDER.

        Returns (path, None) or (None, (payload, status)).

        Rejecting '/' and '\\' alone was not enough: '%2e%2e' decodes to '..',
        which resolved to the output directory itself and made send_file raise
        a 500, and a symlink planted in outputs/ was followed straight out of
        the directory. Everything is resolved and then required to be a regular
        file physically inside the output root.
        """
        if not filename or filename in ('.', '..') or '/' in filename or '\\' in filename:
            return None, ({'error': 'Invalid filename'}, 400)
        root = os.path.realpath(app.config['OUTPUT_FOLDER'])
        target = os.path.realpath(os.path.join(root, filename))
        if target != root and not target.startswith(root + os.sep):
            return None, ({'error': 'Invalid filename'}, 400)
        if not os.path.isfile(target):
            return None, ({'error': 'Image not found'}, 404)
        return target, None

    @app.route('/api/image/<filename>', methods=['GET'])
    def get_image(filename):
        """Serve result images"""
        try:
            image_path, failure = resolve_output_file(filename)
            if failure:
                return failure
            # Serve the real type: everything used to be sent as image/jpeg.
            mimetype = MIME_BY_EXT.get(os.path.splitext(image_path)[1].lower(), 'image/jpeg')
            return send_file(image_path, mimetype=mimetype)
        except Exception as e:
            logger.exception("get_image failed for %r", filename)
            return {'error': str(e)}, 500
    
    @app.route('/api/download/<filename>', methods=['GET'])
    def download_image(filename):
        """Download result image with custom filename"""
        try:
            image_path, failure = resolve_output_file(filename)
            if failure:
                return failure

            # Get custom download name from query parameter
            # Caller-supplied, and it ends up in a Content-Disposition header,
            # so strip anything that could break out of it or walk a path.
            download_name = request.args.get('filename', filename)
            download_name = os.path.basename(download_name).replace('\r', '').replace('\n', '')
            download_name = ''.join(c for c in download_name if c.isprintable() and c != '"')[:200]
            if not download_name:
                download_name = filename

            # Detect file format from filename
            file_ext = os.path.splitext(download_name)[1].lower()
            
            # Map extensions to MIME types
            mime_types = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.tiff': 'image/tiff',
                '.tif': 'image/tiff'
            }
            
            mimetype = mime_types.get(file_ext, 'image/jpeg')
            
            # If filename doesn't have an extension, try to preserve original format
            if not file_ext:
                # Extract format from stored file
                _, stored_ext = os.path.splitext(filename)
                if stored_ext:
                    download_name += stored_ext
                    mimetype = mime_types.get(stored_ext.lower(), 'image/jpeg')
            
            # Return file with proper attachment header
            return send_file(
                image_path,
                mimetype=mimetype,
                as_attachment=True,
                download_name=download_name
            )
        except Exception as e:
            return {'error': str(e)}, 500
    
    @app.route('/api/download-batch', methods=['POST'])
    def download_batch():
        """Download multiple images as ZIP archive"""
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or 'results' not in data:
                return {'error': 'Body must be a JSON object with a "results" list'}, 400

            results = data['results']
            if not isinstance(results, list) or not results:
                return {'error': '"results" must be a non-empty list'}, 400

            # Create ZIP in memory
            zip_buffer = BytesIO()
            used_names = set()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    url = str(result.get('url', ''))
                    filename = str(result.get('filename', 'image.jpg'))

                    # Extract file ID from URL (e.g. /api/image/uuid_result.jpg)
                    if '/api/image/' not in url:
                        continue
                    file_id = url.split('/api/image/')[-1].split('?')[0]

                    # Must resolve to a real file inside outputs/. Joining the
                    # id straight onto the output folder let a crafted url such
                    # as /api/image/../src/backend/.env read any file on the host
                    # and hand it back inside the archive.
                    file_path, failure = resolve_output_file(file_id)
                    if failure:
                        continue

                    # The caller also names the entry, so keep it to a basename
                    # and make it unique, or one entry could overwrite another.
                    entry = os.path.basename(filename).replace('\\', '_') or 'image'
                    base, ext = os.path.splitext(entry)
                    n = 2
                    while entry in used_names:
                        entry = f"{base}_{n}{ext}"
                        n += 1
                    used_names.add(entry)

                    with open(file_path, 'rb') as f:
                        zip_file.writestr(entry, f.read())

            if not used_names:
                return {'error': 'None of the requested files could be found'}, 404
            
            # Prepare ZIP for download
            zip_buffer.seek(0)
            return send_file(
                zip_buffer,
                mimetype='application/zip',
                as_attachment=True,
                download_name=f'mutated_images_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
            )
        
        except Exception as e:
            logger.error(f"Batch download error: {str(e)}")
            return {'error': f'Batch download failed: {str(e)}'}, 500
    
    return app

# Create the app
app = create_app()

if __name__ == '__main__':
    debug = os.getenv('DEBUG', 'False') == 'True'
    port = int(os.getenv('FLASK_PORT', 5000))

    # Bind to loopback unless explicitly told otherwise. This used to be a
    # hard-coded 0.0.0.0, so `./run.sh` -- which advertises "localhost only" --
    # still exposed the API, and with it Werkzeug's /console debugger, to every
    # machine on the network.
    bind_host = os.getenv('BIND_HOST', '127.0.0.1')

    if debug and bind_host not in ('127.0.0.1', 'localhost'):
        # The debugger is a Python execution endpoint. Never serve it off-host.
        logger.warning("Debug mode disabled: refusing to expose the Werkzeug "
                       "debugger on %s. Set DEBUG=False to silence this.", bind_host)
        debug = False

    # Decided before the banner prints, so the banner reports what will actually
    # happen rather than what was asked for.
    print(f"""
    ╔════════════════════════════════════════╗
    ║   Image Mutation Tool - Backend API    ║
    ╚════════════════════════════════════════╝
    
    🚀 Server starting...
    📍 Running on http://{'localhost' if bind_host in ('127.0.0.1', 'localhost') else bind_host}:{port}
    🔗 Bound to: {bind_host}
    🔧 Debug mode: {debug}
    
    Available endpoints:
    ✓ GET  /api/health              - Health check
    ✓ GET  /api/mutations           - List mutations
    ✓ POST /api/mutate              - Apply mutation
    ✓ GET  /api/image/<file>        - View result image
    ✓ GET  /api/download/<file>     - Download single result
    ✓ POST /api/download-batch      - Download multiple as ZIP
    
    📚 API Docs: http://localhost:{port}/api/mutations
    
    Press CTRL+C to quit
    """)

    app.run(host=bind_host, port=port, debug=debug)
