"""The augmentation grid, read from the interface that defines it.

There is exactly one definition of "the grid": AUGMENTATION_SET in
advanced-index.html. Deriving a second one here from the catalogue was tried
and produced a grid six configurations different from the interface's -- it
added annotate with empty text and disagreed about chop and contrast. One
definition, parsed in both places, checked by a test.
"""
import os
import re

from .errors import InterfaceMissing
from .modes import UI_PATH


def build(catalogue, ui_path=None):
    """Return [(mutation, params), ...] exactly as the interface would."""
    path = ui_path or UI_PATH
    if not os.path.exists(path):
        raise InterfaceMissing(f'cannot read the interface at {path}, '
                               f'so the grid it defines cannot be built')
    html = open(path).read()

    m = re.search(r'const AUGMENTATION_SET\s*=\s*\[', html)
    if not m:
        raise InterfaceMissing('AUGMENTATION_SET not found in the interface')
    i, depth = m.end() - 1, 0
    for j in range(i, len(html)):
        if html[j] == '[':
            depth += 1
        elif html[j] == ']':
            depth -= 1
            if depth == 0:
                break
    entries = re.findall(r'\{[^{}]*\}', html[i + 1:j])

    def attr(entry, key):
        mm = re.search(key + r":\s*'([^']+)'", entry)
        return mm.group(1) if mm else None

    jobs = []
    for e in entries:
        mutation, param = attr(e, 'mutation'), attr(e, 'param')
        slider, radio = attr(e, 'slider'), attr(e, 'radio')
        expand = 'expand' in e and 'true' in e
        if slider:
            # augValue overrides the slider for augmentation only: six filters
            # default to their own identity, and without this the grid returned
            # six copies of the input.
            override = re.search(r'augValue:\s*([-\d.]+)', e)
            if override:
                raw = override.group(1)
            else:
                sm = re.search(r'<input[^>]*id="%s"[^>]*>' % re.escape(slider), html)
                got = re.search(r'value="([^"]+)"', sm.group(0)) if sm else None
                raw = got.group(1) if got else None
            params = {}
            if param and raw is not None:
                params[param] = float(raw) if '.' in raw else int(raw)
            jobs.append((mutation, params))
        elif radio:
            spec = (catalogue.get(mutation, {}).get('parameters', {}).get(param) or {})
            options = spec.get('options') or []
            if expand:
                for option in options:
                    jobs.append((mutation, {param: option}))
            else:
                jobs.append((mutation, {param: spec.get('default',
                                                        options[0] if options else None)}))
        else:
            jobs.append((mutation, {}))
    return jobs
