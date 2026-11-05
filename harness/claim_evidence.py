"""Two send gates from the friends-lane postmortem (Ricardo, 11-05).

The postmortem closed with two lessons that were practice and nothing more, each marked
"not yet a gate". Both had already been obeyed by hand on the day seven emails went out,
and neither should depend on someone remembering:

1. **Re-check independently before a send.** A result is not sendable because its own
   referee passed it: 528 claims went through a referee that passed 98% of them, and a pass
   meant nothing. It is sendable when a separate model rebuilt it from the public inputs
   with its own code and got the same answer. Two of thirteen claims did not survive that
   on 11-05, so this is not a formality.
2. **Re-run pod-touched evidence after a host incident.** Evidence produced or modified on
   a rented pod stops being evidence when the host is compromised. It becomes evidence
   again once it has been re-run, after the incident, somewhere trusted.

Both read the same artifact the lane already writes: a re-check ledger, `RECHECK.csv` in a
re-check root, one row per claim, carrying the claim's directory name in `id`, the outcome
of the independent re-run in `rerun`, and the re-check's own verdict in `verdict`. Nothing
new has to be produced for the gates; what was done by hand is now what passes them.

Everything here is offline and reads only local files. A claim is pod-touched when its
directory holds pod artifacts (`pod_run.sh`, `pod_inputs.sh` and the like), which is
deliberately generous: a leftover pod script is answered by a re-check after the incident,
which is what the rule asks for anyway. The incident time is host configuration, so an
installation that has had no incident configures none and the second gate stays silent.
"""
import csv
import datetime as dt
import glob
import os
from pathlib import Path
import re

RECHECK_LEDGER = 'RECHECK.csv'
ID = 'id'
RERUN = 'rerun'
VERDICT = 'verdict'
# Columns a re-check root may carry for its own time; the ledger's mtime is the fallback.
TIME_COLUMNS = ('utc', 'date', 'checked_utc')

MATCHED = ('match', 'matches', 'matched', 'reproduced', 'same')
SURVIVED = ('survives', 'survived', 'holds')
POD_MARKER = re.compile(r'^pod[_-]', re.I)


def claim_id(path):
    return Path(path).name


def pod_markers(claim):
    """The pod artifacts in a claim directory, which make its evidence pod-touched."""
    try:
        return sorted(e.name for e in Path(claim).iterdir() if POD_MARKER.match(e.name))
    except OSError:
        return []


def roots_of(patterns):
    """Expand configured re-check roots, which are host paths and may be globs."""
    found = []
    for pattern in patterns or ():
        for path in sorted(glob.glob(os.path.expanduser(str(pattern)))):
            if Path(path).is_dir():
                found.append(Path(path))
    return found


def time_of(value):
    try:
        return dt.datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except ValueError:
        return None


def aware(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def recheck_rows(patterns):
    """{claim id: [(row, when, basis)]} from every re-check ledger under the given roots."""
    rows = {}
    for root in roots_of(patterns):
        ledger = root / RECHECK_LEDGER
        try:
            text = ledger.read_text(errors='replace')
            when, basis = aware(dt.datetime.fromtimestamp(ledger.stat().st_mtime)), 'ledger mtime'
        except OSError:
            continue
        for row in csv.DictReader(text.splitlines()):
            name = (row.get(ID) or '').strip()
            if not name:
                continue
            stamped, how = when, basis
            for column in TIME_COLUMNS:
                parsed = time_of(row.get(column)) if row.get(column) else None
                if parsed:
                    stamped, how = aware(parsed), column
                    break
            rows.setdefault(name, []).append((row, stamped, how))
    return rows


def value_in(row, column, allowed):
    value = (row.get(column) or '').strip().casefold()
    return any(value.startswith(ok) for ok in allowed)


def findings(claims, patterns, incident=None, require=False):
    """Return (failures, warnings) for the two postmortem gates.

    `claims` are claim directories, `patterns` the configured re-check roots, `incident` the
    host incident after which pod-touched evidence must have been re-run, and `require` asks
    for at least one claim, for a lane whose emails always report results.
    """
    failures, warnings = [], []
    claims = [str(c) for c in claims or ()]
    if not claims:
        if require:
            failures.append('name the claims this reports with --claim: a result is sendable '
                            'only once a separate model has rebuilt it')
        return failures, warnings
    cutoff = None
    if incident:
        parsed = time_of(incident)
        if not parsed:
            raise ValueError('pod_incident_utc must be an ISO 8601 time')
        cutoff = aware(parsed)
    ledgers = recheck_rows(patterns)
    if not ledgers:
        failures.append('no re-check ledger found: set recheck_roots to the directories '
                        'holding {} and re-check before sending'.format(RECHECK_LEDGER))
        return failures, warnings
    for claim in claims:
        name = claim_id(claim)
        if not Path(claim).is_dir():
            failures.append('claim {}: directory missing'.format(name))
            continue
        found = ledgers.get(name)
        if not found:
            failures.append('claim {}: no independent re-check recorded; a separate model must '
                            'rebuild it from the public inputs with its own code'.format(name))
            continue
        matched = [entry for entry in found if value_in(entry[0], RERUN, MATCHED)]
        if not matched:
            failures.append('claim {}: the independent re-run did not reproduce it ({}={})'.format(
                name, RERUN, ', '.join(sorted({(e[0].get(RERUN) or 'blank').strip() for e in found}))))
            continue
        survived = [entry for entry in matched if value_in(entry[0], VERDICT, SURVIVED)]
        if not survived:
            failures.append('claim {}: the independent re-check dropped it ({}={})'.format(
                name, VERDICT, ', '.join(sorted({(e[0].get(VERDICT) or 'blank').strip() for e in matched}))))
            continue
        markers = pod_markers(claim)
        if markers and cutoff:
            after = [entry for entry in survived if entry[1] > cutoff]
            if not after:
                newest = max(survived, key=lambda entry: entry[1])
                failures.append(
                    'claim {}: evidence was produced on a pod ({}) and its re-check is dated '
                    '{} by {}, before the host incident at {}; re-run it somewhere trusted'.format(
                        name, markers[0], newest[1].isoformat(), newest[2], cutoff.isoformat()))
        elif markers and not cutoff:
            warnings.append('claim {}: evidence was produced on a pod ({}); no host incident is '
                            'configured, so nothing checks when it was last re-run'.format(
                                name, markers[0]))
    return failures, warnings
