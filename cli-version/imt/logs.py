"""Logging for the command line.

A run that mutates a few thousand images needs a record of what it did, and a
failure halfway through needs to say which image and which operator. Console
output stays readable -- one line per problem, not per request -- while the file
keeps everything.
"""
import logging
import logging.handlers
import os

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
DEFAULT_FILE = os.path.join(DEFAULT_DIR, 'imt.log')

_configured = False


def setup(level='INFO', log_file=None, quiet=False):
    """Console at the chosen level, file always at DEBUG.

    The file is the record you go back to; the console is what you watch. They
    are deliberately different levels, so -q silences the terminal without
    losing the run.
    """
    global _configured
    logger = logging.getLogger('imt')
    if _configured:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    path = log_file or DEFAULT_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-7s %(message)s', '%Y-%m-%d %H:%M:%S'))
        logger.addHandler(handler)
    except OSError as e:
        # A read-only or missing log directory must not stop the run.
        print(f'imt: could not open the log file ({e}); continuing without it')

    # The console defaults to WARNING, not INFO. A command whose output is a
    # file path should print that path and nothing else -- the run record
    # belongs in the file. -v/--log-level DEBUG opens the console up.
    console = logging.StreamHandler()
    console.setLevel(logging.CRITICAL if quiet
                     else getattr(logging, str(level).upper(), logging.WARNING))
    console.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(console)
    _configured = True
    return logger


def get():
    return logging.getLogger('imt')


def record(level, message, *args):
    """Write to the log file only.

    Used for something already shown to the user: routing it through the normal
    logger would print it a second time.
    """
    logger = logging.getLogger('imt')
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            record_obj = logger.makeRecord(
                'imt', getattr(logging, level.upper(), logging.INFO),
                '(cli)', 0, message, args, None)
            handler.handle(record_obj)
