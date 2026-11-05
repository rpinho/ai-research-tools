"""Reversible disposal for alerts and superseded snapshots. No destructive fallback."""
import os
from pathlib import Path
import subprocess
import settings

# Moving a tree to the Trash is per-item work, so one fixed budget cannot fit both a single
# alert file and a snapshot of several hundred. A few hundred files exceeded 30 seconds on a
# volume at 92% capacity: the move was killed, the snapshot stayed on disk, and the harvest
# that had already copied and verified it was recorded as a failure (friends/fz67, 11-04,
# six times). The budget therefore grows with the size of the tree. It is a ceiling on a
# hang, not a wait, so it is generous; counting stops once the ceiling is reached, because
# a tree larger than that gets the ceiling either way.
TIMEOUT_FLOOR = 60
TIMEOUT_PER_ENTRY = 0.2
TIMEOUT_CEILING = 1800
COUNT_LIMIT = int((TIMEOUT_CEILING - TIMEOUT_FLOOR) / TIMEOUT_PER_ENTRY)


def entries(path):
    """How many items a Trash move has to move, counted no further than it matters."""
    total = 1
    for _, dirs, files in os.walk(str(path), followlinks=False):
        total += len(dirs) + len(files)
        if total >= COUNT_LIMIT:
            return COUNT_LIMIT
    return total


def timeout(path):
    return min(TIMEOUT_CEILING, TIMEOUT_FLOOR + TIMEOUT_PER_ENTRY * entries(path))


def trash(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        subprocess.run([*settings.command('RH_TRASH_COMMAND', '/usr/bin/trash'), str(path)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout(path))
