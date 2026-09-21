#!/usr/bin/env python3
"""A small pool of mutation worker processes.

The point is containment. If ImageMagick kills a process -- and on some builds
`median` does exactly that, with SIGFPE -- it kills a worker and not the server.
The pool notices, starts a replacement, and the request that triggered it gets an
error naming the operator instead of the whole service disappearing.

Workers are long-lived because importing the app costs about a quarter of a
second, which is close to the time a mutation itself takes. Paying that per
request would roughly halve throughput, so it is paid once per worker at startup.
"""
import json
import logging
import os
import queue
import select
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, 'mutation_worker.py')


class WorkerCrashed(RuntimeError):
    """The worker process died rather than returning a reply."""


class WorkerTimeout(RuntimeError):
    """The worker took too long and was killed."""


class _Worker:
    def __init__(self, python):
        self.python = python
        self.proc = None
        self.start()

    def start(self):
        self.proc = subprocess.Popen(
            [self.python, WORKER, '--serve'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=HERE, text=True, bufsize=1)
        # the worker announces itself once the engine is imported
        line = self.proc.stdout.readline()
        if not line:
            raise WorkerCrashed('worker exited before becoming ready')

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def kill(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=5)
        except Exception:
            pass

    def send(self, job, timeout):
        """One job in, one reply out. Raises if the worker dies or hangs."""
        try:
            self.proc.stdin.write(json.dumps(job) + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            raise WorkerCrashed('worker was not accepting work')

        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            self.kill()
            raise WorkerTimeout(f'no reply within {timeout}s')

        line = self.proc.stdout.readline()
        if not line:
            # Wait for the process to be reaped before asking how it died,
            # otherwise poll() is still None and the signal -- which is the
            # whole diagnosis -- is lost.
            try:
                code = self.proc.wait(timeout=5)
            except Exception:
                code = self.proc.poll()
            raise WorkerCrashed(_describe_exit(code))
        return json.loads(line)


def _describe_exit(code):
    """Say plainly how the worker died, since the signal is the diagnosis."""
    if code is None:
        return 'worker closed its output unexpectedly'
    if code < 0:
        import signal as _sig
        try:
            name = _sig.Signals(-code).name
        except Exception:
            name = f'signal {-code}'
        if name == 'SIGFPE':
            return ('ImageMagick raised a floating point exception and the worker '
                    'was killed (SIGFPE). This operator is not safe on this '
                    'ImageMagick build')
        if name == 'SIGSEGV':
            return ('ImageMagick crashed with a segmentation fault and the worker '
                    'was killed (SIGSEGV). This operator is not safe on this '
                    'ImageMagick build')
        return f'worker was killed by {name}'
    return f'worker exited with status {code}'


class WorkerPool:
    def __init__(self, size=2, python=None):
        self.python = python or sys.executable
        self.size = max(1, int(size))
        self._idle = queue.Queue()
        self._lock = threading.Lock()
        self._started = 0
        for _ in range(self.size):
            self._idle.put(_Worker(self.python))
            self._started += 1
        logger.info('mutation workers: %d ready (crash isolation on)', self.size)

    def submit(self, job, timeout=120):
        worker = self._idle.get()
        try:
            if not worker.alive():
                worker = _Worker(self.python)
            reply = worker.send(job, timeout)
            self._idle.put(worker)
            return reply
        except (WorkerCrashed, WorkerTimeout):
            # Replace it so the next request is not served by a corpse.
            worker.kill()
            try:
                self._idle.put(_Worker(self.python))
            except Exception:
                logger.exception('could not start a replacement worker')
                self._idle.put(worker)
            raise
        except Exception:
            self._idle.put(worker)
            raise

    def shutdown(self):
        while not self._idle.empty():
            try:
                self._idle.get_nowait().kill()
            except Exception:
                break
