"""Exit codes, error codes, and the failures that map to them.

Two different things are reported. The **exit code** is for scripts: 0, 1, 2, 3
or 4, so a caller can branch on the kind of failure. The **error code** (E101,
E302 …) is for people: it names the exact condition, so an error can be looked
up, searched for, and reported without paraphrasing.
"""


class ImtError(Exception):
    exit_code = 1
    code = 'E100'
    hint = ''

    def __init__(self, message, hint='', code=None):
        super().__init__(message)
        self.hint = hint or self.hint
        if code:
            self.code = code

    def render(self):
        return f'imt [{self.code}]: {self}'


# --- 1xx: the request could not be performed --------------------------------

class MutationFailed(ImtError):
    """The server refused or could not perform the mutation."""
    exit_code = 1
    code = 'E101'


class FileMissing(MutationFailed):
    code = 'E102'


class FileUnreadable(MutationFailed):
    code = 'E103'


class DownloadFailed(MutationFailed):
    code = 'E104'


# --- 2xx: the command line itself was wrong ---------------------------------

class UsageError(ImtError):
    exit_code = 2
    code = 'E200'


class UnknownMutation(UsageError):
    code = 'E201'


class BadParameter(UsageError):
    code = 'E202'


class ModeRefused(UsageError):
    code = 'E203'


class NoInput(UsageError):
    code = 'E204'


class InterfaceMissing(UsageError):
    code = 'E205'


# --- 3xx: nothing to talk to ------------------------------------------------

class ServerUnavailable(ImtError):
    exit_code = 3
    code = 'E301'
    hint = 'Start it with ./run.sh, or point elsewhere with --base'


class ServerNotTheTool(ServerUnavailable):
    code = 'E302'


# --- 4xx: finished, but not cleanly -----------------------------------------

class PartialFailure(ImtError):
    exit_code = 4
    code = 'E401'


class Interrupted(ImtError):
    exit_code = 4
    code = 'E402'


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_SERVER = 3
EXIT_PARTIAL = 4

# Every code, so `imt codes` can print them and the docs cannot drift from them.
CODES = {
    'E101': 'the mutation could not be performed',
    'E102': 'the input file does not exist',
    'E103': 'the input file is not a readable image',
    'E104': 'the result could not be downloaded or written',
    'E200': 'the command line was wrong',
    'E201': 'no such mutation in the catalogue',
    'E202': 'a parameter was malformed or out of range',
    'E203': 'the chosen mode does not allow this',
    'E204': 'no input images were found',
    'E205': 'the interface file could not be read',
    'E301': 'no tool is running at the given address',
    'E302': 'something answered, but it is not this tool',
    'E401': 'the batch finished with failures',
    'E402': 'interrupted before finishing',
}
