#!/usr/bin/env python3
"""Stop a lane loop from restarting a row whose input is broken.

Why (2026-11-06): when a HOLD lifted, the friends tw loop restarted five rows whose pod was
long gone. Each one died on the same `KeyError: mscr` before doing any science, every four
minutes, with a model call every time, until somebody killed them by hand. The loops restart
a live row until their deadline and count only the row's own state, never their own failures.

This is the BURNING check moved earlier: BURNING notices job-hours spent with nothing new,
after the spending. A row that cannot start does not need hours to prove it.

A failure counts as pre-science when the run produced nothing: no file under --watch was
written at or after --since, counting neither logs nor the run's own bookkeeping.

Both of those words were learned the hard way (2026-11-07). The first version asked only
whether a file was written and gave any run that exited 0 a free pass. The friends tw diogo
DB15 row then restarted about 516 times over 18 hours on the same missing pod, roughly 38k
Codex tokens a run and a 240 MB log, because its worker wrote "blocked" into STATUS.md, wrote
a CHECKPOINT_RESTART<n>, a process_audit_restart<n> and a preflight receipt, and exited 0. Its
own checkpoint recorded "science_executed": false and "matches_restart499": true on every run.
Receipts about an attempt are not progress, however many of them there are, and exit 0 is a
claim about the process, not about the work; only something outside BOOKKEEPING clears the
counter. Replaying the real 516 restarts, this fires on the 6th, 510 earlier than nothing.

Add a lane's own receipt names with --ignore GLOB (repeatable). A run that exits non-zero
having written real work is an ordinary failure and resets nothing.

After --max consecutive pre-science failures the row gets a STALE_INPUT marker, the lane is
alerted once, and the exit code is 3 so the caller can stop that row and keep the rest of the
loop alive. Success, or any progress, clears the counter and the marker.
"""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

STATE = Path(os.environ.get('RH_ROW_GUARD_STATE', '.runtime/row-guard')).expanduser()

# What a worker writes about its own attempt: what it is about to do, what it just did, that it
# is blocked, and a receipt per restart. None of it says whether the work happened, so none of
# it counts as progress. The glob forms matter as much as the fixed names: DB15's receipts were
# uniquely named every run (CHECKPOINT_RESTART517_SHA256.json, process_audit_restart517.json,
# preflight_20261107T131700Z.txt), so a fixed list of three names would have caught nothing.
BOOKKEEPING = ('STATUS.md', 'PLAN.md', 'REPORT.md', 'ALERTS', 'ALERTS.*', '*_STATUS.md',
               'CPU_REQUEST.md', 'CHECKPOINT_*', 'CHECKPOINT', 'preflight*', 'process_audit*',
               'process_snapshot*', 'stage_*', 'AUDIT_RESTART*', 'RESTART*')


def bookkeeping(name, ignore=()):
    return any(fnmatch.fnmatch(name, g) for g in tuple(BOOKKEEPING) + tuple(ignore))


def progressed(watch, since, ignore=()):
    """Did this run write any real work? Logs and bookkeeping are excluded."""
    for root in watch:
        base = Path(root).expanduser()
        if not base.exists():
            continue
        entries = [base] if base.is_file() else base.rglob('*')
        for p in entries:
            if (p.is_file() and p.suffix not in ('.log', '.prompt')
                    and not bookkeeping(p.name, ignore)):
                try:
                    if p.stat().st_mtime >= since - 1:
                        return True
                except OSError:
                    continue
    return False


def guard(key, rc, since, watch, state=None, maximum=2, label='', lane='', ignore=()):
    """Return (exit_code, message). 3 means: stop this row, its input is stale."""
    state = Path(state) if state else STATE
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in key)[:120]
    counter = state / (safe + '.json')
    marker = state / (safe + '.STALE_INPUT')
    # rc is not consulted. A worker that catches its own blocker, writes "blocked" and exits 0
    # is the failure this guard exists for; only work clears the counter.
    made_progress = progressed(watch, since, ignore)
    if made_progress:
        for p in (counter, marker):
            try:
                p.unlink()
            except OSError:
                pass
        return 0, ''
    try:
        n = json.loads(counter.read_text()).get('consecutive', 0)
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        state.mkdir(parents=True, exist_ok=True)
        counter.write_text(json.dumps(dict(consecutive=n, rc=rc, key=key,
                                           utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))))
    except OSError:
        pass
    if n < maximum:
        return 0, 'pre-science failure {} of {} for {}'.format(n, maximum, key)
    exit_note = 'exited 0 each time, writing only bookkeeping' if rc == 0 else 'last exit {}'.format(rc)
    message = ('STALE_INPUT: {} ran {} times without doing any science ({}). '
               'Stopping this row; its input needs fixing, not another run.{}'
               .format(label or key, n, exit_note, ' lane=' + lane if lane else ''))
    if not marker.exists():
        try:
            marker.write_text(message)
        except OSError:
            pass
        try:
            import lane_alerts
            lane_alerts.push(message)
        except (ImportError, OSError):
            pass
    return 3, message


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--key', required=True, help='stable id for this row')
    p.add_argument('--rc', type=int, required=True, help='the run exit code')
    p.add_argument('--since', type=float, required=True, help='epoch seconds the run started')
    p.add_argument('--watch', action='append', default=[], required=True,
                   help='file or directory that holds the row\'s work; repeatable')
    p.add_argument('--state', help='counter directory (default RH_ROW_GUARD_STATE)')
    p.add_argument('--max', type=int, default=2, dest='maximum')
    p.add_argument('--label', default='')
    p.add_argument('--lane', default='')
    p.add_argument('--ignore', action='append', default=[], metavar='GLOB',
                   help='a filename pattern that is this lane\'s own bookkeeping, not work; '
                        'repeatable. See BOOKKEEPING for what is already excluded.')
    a = p.parse_args(argv)
    code, message = guard(a.key, a.rc, a.since, a.watch, a.state, a.maximum, a.label, a.lane,
                          a.ignore)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
