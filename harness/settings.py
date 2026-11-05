"""Environment configuration shared by standalone commands. No state is loaded here.

Host-specific values (paths, lane sets, notifier commands) live outside the repo in a
KEY=VALUE file: $RH_ENV_FILE, else $XDG_CONFIG_HOME/research-harness/env, else
~/.config/research-harness/env. It is read once at import, so every entry point (CLI,
launchd job, lane session) sees the same values. The real environment always wins.
"""
import os
from pathlib import Path
import re
import shlex


def env_file():
    explicit = os.environ.get('RH_ENV_FILE')
    if explicit:
        return Path(explicit).expanduser()
    config = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(config).expanduser() / 'research-harness/env'


def load_env_file(path=None):
    """Set unset variables from a KEY=VALUE file. Blank lines and # comments are ignored;
    values may be single- or double-quoted. Returns the keys it set."""
    path = Path(path) if path else env_file()
    try:
        text = path.read_text()
    except (FileNotFoundError, NotADirectoryError):
        return []
    loaded = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r'[A-Z_][A-Z0-9_]*', key):
            raise ValueError('{}:{}: expected KEY=VALUE'.format(path, number))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


load_env_file()


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
