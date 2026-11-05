#!/usr/bin/env python3
"""log_result: record a lane result in its RESULTS.jsonl ledger, the yield that lane-yield counts.

A lane exists to find something new. Every result that leaves a lane (a filed PR, a submitted note, a
leaderboard entry, an email to a researcher) gets one line, NEW yes or no, with its evidence link and
the referee verdict. lane-yield counts new=true lines with a HOLDS verdict.

    log_result.py --ledger ~/.local/share/research-harness/lanes/math/RESULTS.jsonl \\
        --new yes --kind value --what "C38 bound 0.25 -> 0.2524" --link https://github.com/... --referee HOLDS
"""
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

KINDS = ('rerun', 'value', 'question', 'conjecture', 'note', 'other')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--ledger', required=True)
    p.add_argument('--new', required=True, choices=('yes', 'no'))
    p.add_argument('--kind', required=True, choices=KINDS)
    p.add_argument('--what', required=True)
    p.add_argument('--link', required=True, help='evidence: PR, DOI, claim dir or sent-mail id')
    p.add_argument('--referee', required=True, help='HOLDS, FAILS or NONE')
    p.add_argument('--utc', help='ISO time of the result (default now)')
    a = p.parse_args(argv)
    row = dict(utc=a.utc or datetime.now(timezone.utc).isoformat(timespec='seconds'), new=a.new == 'yes',
               kind=a.kind, what=a.what, link=a.link, referee=a.referee)
    path = Path(a.ledger).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as f:
        f.write(json.dumps(row) + '\n')
    print(json.dumps(row))
    return 0


if __name__ == '__main__':
    sys.exit(main())
