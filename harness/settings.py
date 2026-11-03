"""Environment configuration shared by standalone commands. No state is loaded here."""
import os
from pathlib import Path
import re
import shlex


def names(key):
    values = frozenset(filter(None, os.environ.get(key, '').split(',')))
    if any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', v) for v in values):
        raise ValueError(key + ': expected comma-separated lane names')
    return values


def path(key, default):
    return Path(os.environ.get(key, str(default))).expanduser()


def command(key, default):
    args = shlex.split(os.environ.get(key, default))
    if not args:
        raise ValueError(key + ': command must not be empty')
    return args
