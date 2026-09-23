"""A progress bar, with or without tqdm.

tqdm is nicer and is used when it is installed. It is not made a hard
requirement for one bar: this tool runs on seven packages and a missing
optional import should degrade, not stop the run.
"""
import sys
import time

try:
    from tqdm import tqdm as _tqdm
    HAVE_TQDM = True
except ImportError:
    _tqdm = None
    HAVE_TQDM = False


class _Fallback:
    """A single-line bar, redrawn in place. Silent when not on a terminal."""

    def __init__(self, total, desc='', disable=False):
        self.total = max(1, int(total))
        self.desc = desc
        self.n = 0
        self.disable = disable or not sys.stderr.isatty()
        self.started = time.time()
        self._draw()

    def _draw(self):
        if self.disable:
            return
        frac = self.n / self.total
        width = 28
        filled = int(width * frac)
        elapsed = time.time() - self.started
        rate = self.n / elapsed if elapsed > 0 and self.n else 0
        bar = '#' * filled + '.' * (width - filled)
        sys.stderr.write(f'\r  {self.desc} [{bar}] {self.n}/{self.total} '
                         f'{frac * 100:5.1f}%  {rate:5.1f}/s ')
        sys.stderr.flush()

    def update(self, n=1):
        self.n += n
        self._draw()

    def close(self):
        if not self.disable:
            self._draw()
            sys.stderr.write('\n')
            sys.stderr.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def bar(total, desc='', disable=False):
    if HAVE_TQDM:
        return _tqdm(total=total, desc=desc, disable=disable,
                     unit='img', leave=False,
                     bar_format='  {desc} {bar} {n_fmt}/{total_fmt} {rate_fmt}')
    return _Fallback(total, desc=desc, disable=disable)
