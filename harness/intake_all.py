#!/usr/bin/python3
"""launchd entrypoint: skip absent lanes, continue after individual failures."""
from pathlib import Path
import os
import subprocess
import sys

CODE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('RH_ROOT', str(CODE))).expanduser()



def main():
    failed = False
    for lane in sorted(p.name for p in (ROOT / 'lanes').glob('*') if p.is_dir()):
        if not (ROOT / 'lanes' / lane).is_dir():
            continue
        result = subprocess.run(['/usr/bin/nice', '-n', '19', sys.executable,
                                 str(CODE / 'rh.py'), '--root', str(ROOT), 'intake', lane, '--live'])
        failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
