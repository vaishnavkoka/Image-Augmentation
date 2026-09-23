"""Beginner, Intermediate and Advanced — read from the interface, not copied.

The interface defines these in a MODES object: which filters each mode offers,
how many images it accepts, whether tuning and augmentation are allowed. Copying
those rules here would make a second definition that nothing keeps in step, and
that is the fault this project has produced six times. So they are parsed out of
advanced-index.html, and a test asserts the parse still finds all three.
"""
import os
import re

from .errors import InterfaceMissing, ModeRefused

UI_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'src', 'ui', 'advanced-index.html')

NAMES = ('beginner', 'intermediate', 'advanced')


def _block(html, name):
    m = re.search(r'\b%s:\s*\{(.*?)\n            \}' % re.escape(name), html, re.S)
    return m.group(1) if m else None


def _flag(body, key, default=False):
    m = re.search(r'\b%s:\s*(true|false)' % re.escape(key), body)
    return (m.group(1) == 'true') if m else default


def _number(body, key, default=None):
    m = re.search(r'\b%s:\s*(\d+)' % re.escape(key), body)
    return int(m.group(1)) if m else default


def _list(body, key):
    m = re.search(r'\b%s:\s*\[(.*?)\]' % re.escape(key), body, re.S)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []


def load(ui_path=None):
    """Return {name: {...}} exactly as the interface defines them."""
    path = ui_path or UI_PATH
    if not os.path.exists(path):
        raise InterfaceMissing(f'cannot read the interface at {path}, '
                           f'so its modes cannot be honoured')
    html = open(path).read()
    modes = {}
    for name in NAMES:
        body = _block(html, name)
        if body is None:
            raise InterfaceMissing(f'mode {name!r} not found in the interface')
        modes[name] = {
            'label': (re.search(r"label:\s*'([^']+)'", body) or [None, name])[1]
                     if re.search(r"label:\s*'([^']+)'", body) else name.title(),
            'cap': _number(body, 'cap', 500),
            'tune': _flag(body, 'tune', True),
            'numeric': _flag(body, 'numeric', True),
            'augment': _flag(body, 'augment', True),
            'continuous': _list(body, 'continuous'),
            'discrete': _list(body, 'discrete'),
        }
    return modes


def allowed(mode, kind, mutation):
    """Beginner and Intermediate restrict the catalogue; Advanced does not."""
    names = mode.get(kind) or []
    return True if not names else (mutation in names)


def check_apply(mode, name, kind, mutation, params, catalogue):
    """Refuse what this mode does not offer, and say why.

    Silently widening a request is worse than refusing it: the caller would get
    a result they did not ask for and no way to tell.
    """
    if not allowed(mode, kind, mutation):
        offered = ', '.join((mode.get(kind) or [])[:8])
        raise ModeRefused(
            f"{name} mode does not offer {mutation!r}",
            hint=f'it offers: {offered}. Use --mode advanced for the whole catalogue')
    if not mode['tune']:
        tunable = {k for k, v in (catalogue.get(mutation, {}).get('parameters') or {}).items()
                   if v.get('type') in ('int', 'float')}
        given = {k for k in params if k != 'channel'} & tunable
        if given:
            raise ModeRefused(
                f"{name} mode runs filters at their defaults, so {sorted(given)} "
                f"cannot be set",
                hint='Use --mode intermediate or advanced to tune')
    if params.get('channel') and not mode['numeric']:
        raise ModeRefused(f'{name} mode does not offer channel restriction',
                         hint='Use --mode advanced')


def check_batch(mode, name, count):
    if mode['cap'] and count > mode['cap']:
        raise ModeRefused(
            f'{name} mode takes at most {mode["cap"]} images, {count} given',
            hint='Use --mode advanced, or pass fewer images')


def check_augment(mode, name):
    if not mode['augment']:
        raise ModeRefused(f'{name} mode does not offer augmentation',
                         hint='Use --mode advanced')
