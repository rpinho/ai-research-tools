"""Reversible disposal for alerts and superseded snapshots. No destructive fallback."""
from pathlib import Path
import subprocess
import settings


def trash(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        subprocess.run([*settings.command('RH_TRASH_COMMAND', '/usr/bin/trash'), str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
