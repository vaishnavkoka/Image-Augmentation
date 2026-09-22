#!/usr/bin/env python3
"""Applies one mutation, in a process of its own.

Why this exists: `median` on a stock ImageMagick build does not raise, it raises
SIGFPE -- the operating system kills the process outright. A `try/except` cannot
catch that, because no Python runs after it. On the developer's purpose-built
ImageMagick the same call is fine, which is how twelve passing suites missed a
one-request denial of service.

So the image work happens here, in a separate process. When ImageMagick takes a
process down it takes this one, the server notices the worker died and answers
the request with an error naming the operator. The tool stays up.

Two modes:

    python mutation_worker.py --serve     one job per line on stdin, reply per
                                          line on stdout (how the server runs it)
    python mutation_worker.py --once      a single job as JSON on stdin

A job is JSON:
    {"input_path", "output_path", "mutation", "resolved", "params",
     "format", "use_pil", "jpeg_quality"}

A reply is JSON: {"ok": true, "width": w, "height": h, "frames": n}
or {"ok": false, "kind": "client"|"server", "error": "..."}.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_engine():
    """Import the app once, for the engine and the PIL fallback."""
    import app as app_module
    if app_module.MUTATOR_CLASS is None:
        raise RuntimeError('the mutation engine was not published by create_app()')
    return app_module


def run_job(job, app_module):
    """Load, mutate, save. Mirrors what the request handler used to do inline."""
    from PIL import Image as PILImage
    from wand.image import Image as WandImage

    use_pil = bool(job.get('use_pil'))
    params = dict(job.get('params') or {})
    # -channel is a setting, not a parameter of the operator, so it travels
    # separately and never reaches the operator's own keyword arguments.
    channel = params.pop('channel', None)
    fmt = job.get('format') or 'png'
    quality = int(job.get('jpeg_quality') or 90)

    if use_pil:
        if channel and channel != 'all':
            raise ValueError(
                'a channel restriction needs the ImageMagick path, and this '
                'format is being handled by the Pillow fallback')
        with PILImage.open(job['input_path']) as im:
            im.load()
            mutated = app_module.PIL_MUTATION_FUNC(im, job['mutation'], **params)
            save_kwargs = {}
            if fmt == 'jpeg':
                save_kwargs['quality'] = quality
            mutated.save(job['output_path'],
                         format=fmt.upper() if fmt != 'tiff' else 'TIFF',
                         **save_kwargs)
            return {'ok': True, 'width': mutated.size[0], 'height': mutated.size[1],
                    'frames': 1}

    mutator = app_module.MUTATOR_CLASS()
    func = getattr(mutator, job['resolved'], None)
    if not callable(func):
        return {'ok': False, 'kind': 'client',
                'error': f"Unknown mutation: {job.get('mutation')}"}

    if channel and channel != 'all':
        # Measured, not assumed: some operators ignore the mask and would hand
        # back a whole-image mutation labelled as channel-restricted.
        if not app_module.honours_channel(job['resolved'], func, params):
            return {'ok': False, 'kind': 'client',
                    'error': f"'{job.get('mutation')}' does not honour a channel "
                             f"restriction: it writes to every channel whatever "
                             f"the mask says"}

    with WandImage(filename=job['input_path']) as im:
        with app_module.channel_mask(im, channel):
            mutated = app_module.apply_to_every_frame(im, func, **params)
        if not mutated.format or mutated.format.lower() != fmt:
            try:
                mutated.format = fmt
            except Exception:
                pass
        if fmt == 'jpeg':
            mutated.compression_quality = quality
        app_module.strip_timestamps(mutated)
        mutated.save(filename=job['output_path'])
        frames = len(mutated.sequence) if hasattr(mutated, 'sequence') else 1
        return {'ok': True, 'width': mutated.width, 'height': mutated.height,
                'frames': frames}


def _attempt(job, app_module):
    """Run one job, turning the caller's mistakes into a reply rather than a crash."""
    try:
        return run_job(job, app_module)
    except Exception as e:
        # ValidationError subclasses ValueError, so both land here. Anything
        # that is the caller's fault is a 400 upstream, everything else a 500.
        kind = 'client' if isinstance(e, ValueError) else 'server'
        return {'ok': False, 'kind': kind, 'error': str(e) or e.__class__.__name__}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else '--serve'
    app_module = _load_engine()

    if mode == '--once':
        job = json.loads(sys.stdin.read())
        sys.stdout.write(json.dumps(_attempt(job, app_module)) + '\n')
        return 0

    # --serve: the server keeps this process alive and feeds it jobs, so the
    # 0.25s import is paid once here rather than on every mutation.
    sys.stdout.write(json.dumps({'ready': True}) + '\n')
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps({'ok': False, 'kind': 'server',
                                         'error': 'unreadable job'}) + '\n')
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps(_attempt(job, app_module)) + '\n')
        sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
