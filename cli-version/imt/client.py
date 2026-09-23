"""Talking to the running tool.

Everything goes through the same HTTP API the web interface uses. That is the
whole point: one engine, one catalogue, one set of validation rules. A second
implementation of the mutations here would be a second thing to keep in step,
and this project has produced six defects of exactly that shape.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

from .errors import (DownloadFailed, FileMissing, FileUnreadable,
                     MutationFailed, ServerNotTheTool, ServerUnavailable)
from .logs import get as get_logger


class Client:
    def __init__(self, base, timeout=300):
        self.base = base.rstrip('/')
        self.timeout = timeout
        self.log = get_logger()

    # ---- reading ----------------------------------------------------------

    def _get(self, path):
        url = self.base + path
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise ServerUnavailable(f'{url} returned HTTP {e.code}')
        except (urllib.error.URLError, OSError) as e:
            raise ServerUnavailable(f'no tool at {self.base} ({e})')
        except ValueError:
            raise ServerNotTheTool(f'{url} answered, but not with JSON — is that the tool?')

    def health(self):
        return self._get('/api/health')

    def catalogue(self):
        raw = self._get('/api/mutations')
        return {**raw['continuous'], **raw['discrete']}, raw

    # ---- mutating ---------------------------------------------------------

    def mutate(self, image, mutation, params):
        """Apply one mutation. Returns the server's reply dict."""
        if not os.path.isfile(image):
            raise FileMissing(f'no such file: {image}')
        self.log.debug('POST %s %s %s', mutation, json.dumps(params, sort_keys=True), image)
        proc = subprocess.run(
            ['curl', '-s', '--fail-with-body', '-m', str(self.timeout),
             '-X', 'POST', self.base + '/api/mutate',
             '-F', 'image=@' + image,
             '-F', 'mutation=' + mutation,
             '-F', 'parameters=' + json.dumps(params)],
            capture_output=True, text=True)
        body = proc.stdout
        try:
            reply = json.loads(body)
        except ValueError:
            raise MutationFailed(
                f'unreadable reply for {mutation} on {os.path.basename(image)}'
                + (f': {body[:80]}' if body else ' (empty)'))
        if 'result_url' not in reply:
            detail = str(reply.get('error', 'refused'))
            problem = FileUnreadable if 'Invalid image' in detail else MutationFailed
            raise problem(f'{mutation} on {os.path.basename(image)}: {detail}')
        return reply

    def download(self, url, dest):
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        proc = subprocess.run(['curl', '-s', '--fail-with-body', '-m', str(self.timeout),
                               '-o', dest, url], capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(dest):
            raise DownloadFailed(f'could not write {dest}',
                                 hint='check the output directory exists and is writable')
        return dest
