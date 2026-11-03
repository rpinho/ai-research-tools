#!/usr/bin/env python3
"""Create an isolated toy lane and preview a sweep. No network or executor calls."""
import argparse
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'harness'))
import rh


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    lane = root / 'lanes/toy'
    if lane.exists():
        parser.error('toy lane already exists; choose a new root')
    lane.mkdir(parents=True)
    for source in (REPO / 'examples/toy-lane').iterdir():
        shutil.copyfile(source, lane / source.name)
    path = lane / 'SWEEP.toml'
    import json
    path.write_text(path.read_text().replace('launcher = "../../tools/launch.sh"',
                    'launcher = ' + json.dumps(str(REPO / 'tools/launch.sh'))))
    result = rh.main(['--root', str(root), 'ideas', 'add', 'toy', '--source-type', 'internal',
                     '--claim', 'A synthetic sequence may have a shorter period'])
    if result:
        return result
    return rh.main(['--root', str(root), 'sweep', 'toy', '--dry-run'])


if __name__ == '__main__':
    raise SystemExit(main())
