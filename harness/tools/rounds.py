#!/usr/bin/env python3
"""Per-round ledger and the gate that makes a dry streak change direction.

Why (2026-11-06, the c5 postmortem). c5 ran about 80 rounds, R16 to R83, almost all in one
method family, printing "0 selective" round after round. It spent roughly $340 through an
external API, about $80 of it on R41-R57: seventeen dry rounds in a single day. It changed
method only when Ricardo stepped in, and its own account is that the KB bookmarks had already
identified the better family for this target and "I should have consulted them 38 rounds ago."

Every harness check was green, for four separate reasons, and this module answers them:

  1. The rounds were invisible. They ran in-session against an external API, so they never
     went through launch.sh, log-result or any spend ledger. c5's SPEND.md records ~$267 over
     17 rounds against the ~80 that happened, so even the yield test could not see them.
     `log-round` gives every paid round one row: round, family, spend, outcome.
  2. The verdict was not binding. c5 is report_only, so DRY_ROUNDS never did anything.
  3. Nothing forced a change of method. A dry streak in one family is evidence about that
     family, and the next round could be the same family again.
  4. Nothing made it read what we already knew.

The rule here is deliberately not "stop". Ricardo's lanes exist to spend and find things; a
dry streak means change direction, not halt. So the gate refuses the NEXT round in a family
that has just produced three dry rounds, and asks for a different one. A closed family goes in
the lane's dead-ends log and cannot be reopened without new evidence recorded against it.

A round is dry when it produced nothing selective: `--outcome dry`. The lane says which,
because only the lane knows what selective means for its target.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROUNDS = 'ROUNDS.jsonl'
DEAD_ENDS = 'DEAD_ENDS.jsonl'
RECEIPTS = 'KB_CONSULTS.jsonl'
OUTCOMES = ('selective', 'dry', 'failed')
DRY_LIMIT = 3


def read(path):
    """Rows in file order; a corrupt line is skipped rather than blinding the ledger."""
    out = []
    try:
        lines = Path(path).read_text(errors='replace').splitlines()
    except OSError:
        return out
    for line in lines:
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        f.write(json.dumps(row, sort_keys=True) + '\n')


def dry_streak(rows, family=None):
    """Consecutive dry rounds at the end of the ledger, optionally within one family.

    `failed` rounds do not break a streak: a round that crashed produced nothing selective
    either, and treating a crash as a fresh start is how a streak hides.
    """
    streak = 0
    for row in reversed(rows):
        if family is not None and row.get('family') != family:
            continue
        if row.get('outcome') == 'selective':
            break
        streak += 1
    return streak


def spend(rows):
    total = 0.0
    for row in rows:
        try:
            total += float(row.get('spend_usd') or 0)
        except (TypeError, ValueError):
            continue
    return total


def closed_families(lane):
    return {r.get('family') for r in read(Path(lane) / DEAD_ENDS)
            if r.get('status') == 'closed' and r.get('family')}


def consulted_since(lane, rounds_rows):
    """Is there a KB consult receipt at or after the last round in the ledger?

    Before round 1 there is no last round, so any receipt counts. After a dry streak the
    receipt has to be newer than the streak, or it is the reading that failed to help.
    """
    receipts = read(Path(lane) / RECEIPTS)
    if not receipts:
        return False
    last_receipt = max((str(r.get('utc') or '') for r in receipts), default='')
    last_round = max((str(r.get('utc') or '') for r in rounds_rows), default='')
    return bool(last_receipt) and last_receipt >= last_round


def gate(lane, family, new_evidence='', limit=DRY_LIMIT):
    """Return (errors, warnings) for starting a round in `family`.

    Errors refuse the round. The point is to redirect it, not to end the lane, so every error
    names what would let it proceed.
    """
    lane = Path(lane)
    rows = read(lane / ROUNDS)
    errors, warnings = [], []
    if family in closed_families(lane) and not new_evidence.strip():
        errors.append('DEAD_END: method family "{}" was closed in {} after a dry streak. '
                      'Reopening it needs new evidence: --new-evidence "<what changed>".'
                      .format(family, DEAD_ENDS))
    streak = dry_streak(rows, family)
    if streak >= limit:
        errors.append('DRY_FAMILY: the last {} rounds in family "{}" produced nothing '
                      'selective (${:.0f} spent in this lane so far). A dry streak is evidence '
                      'about the family: run a different one. Close this family with '
                      '`rounds.py close-family` and the reason.'
                      .format(streak, family, spend(rows)))
    overall = dry_streak(rows)
    if overall >= limit and not consulted_since(lane, rows):
        errors.append('KB_UNREAD: {} dry rounds and no KB consult receipt since the last one. '
                      'Search index.md, the wiki, raw/bookmarks and the sibling lanes for this '
                      'target and method, then record it with `rounds.py consult`. c5 spent 38 '
                      'rounds before reading what the KB already had.'.format(overall))
    if not rows and not consulted_since(lane, rows):
        errors.append('KB_UNREAD: no KB consult receipt before round 1. Search index.md, the '
                      'wiki, raw/bookmarks and the sibling lanes first, then record it with '
                      '`rounds.py consult`.')
    if 0 < overall < limit:
        warnings.append('{} dry round(s) in a row; {} more closes this family'
                        .format(overall, limit - overall))
    return errors, warnings


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lane', required=True, type=Path, help='the lane directory')
    sub = p.add_subparsers(dest='action', required=True)

    log = sub.add_parser('log', help='one row per paid round')
    log.add_argument('--round', required=True)
    log.add_argument('--family', required=True, help='method family, the unit a dry streak closes')
    log.add_argument('--outcome', required=True, choices=OUTCOMES)
    log.add_argument('--spend-usd', type=float, default=0.0)
    log.add_argument('--provider', default='')
    log.add_argument('--n-selective', type=int, default=0)
    log.add_argument('--what', default='')

    check = sub.add_parser('check', help='may a round in this family start?')
    check.add_argument('--family', required=True)
    check.add_argument('--new-evidence', default='')

    close = sub.add_parser('close-family', help='record a family as a dead end')
    close.add_argument('--family', required=True)
    close.add_argument('--reason', required=True)

    consult = sub.add_parser('consult', help='record a KB consult receipt')
    consult.add_argument('--source', action='append', default=[], required=True,
                         help='a KB path or sibling lane that was searched; repeatable')
    consult.add_argument('--took', required=True, help='what this round takes from it')

    a = p.parse_args(argv)
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if a.action == 'log':
        row = dict(utc=now, round=a.round, family=a.family, outcome=a.outcome,
                   spend_usd=a.spend_usd, provider=a.provider, n_selective=a.n_selective,
                   what=a.what)
        append(a.lane / ROUNDS, row)
        print(json.dumps(row, sort_keys=True))
        return 0
    if a.action == 'close-family':
        append(a.lane / DEAD_ENDS, dict(utc=now, family=a.family, status='closed',
                                        reason=a.reason))
        print('closed family {} in {}'.format(a.family, DEAD_ENDS))
        return 0
    if a.action == 'consult':
        append(a.lane / RECEIPTS, dict(utc=now, sources=a.source, took=a.took))
        print('recorded a KB consult over {} source(s)'.format(len(a.source)))
        return 0
    errors, warnings = gate(a.lane, a.family, a.new_evidence)
    for w in warnings:
        print('WARN: ' + w, file=sys.stderr)
    for e in errors:
        print('REFUSED: ' + e, file=sys.stderr)
    return 2 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
