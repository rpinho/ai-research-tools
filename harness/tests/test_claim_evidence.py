"""The two send gates from the friends-lane postmortem (Ricardo, 11-05).

A result is sendable only once a separate model has rebuilt it from the public inputs with
its own code, and pod-touched evidence only once it has been re-run after the host incident.

Every fixture here is invented: claim ids, friend names and paths are made up, and no real
correspondence, claim or result appears in this repo.
"""
import csv
import datetime as dt
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claim_evidence as ce

COLUMNS = ('id', 'friend', 'rerun', 'verdict', 'reason', 'utc')
INCIDENT = '2026-11-05T04:58:00+00:00'
BEFORE = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
AFTER = dt.datetime(2026, 11, 5, 10, tzinfo=dt.timezone.utc)


def claim(tmp_path, name, pod=False):
    """A claim directory, optionally carrying the pod artifacts that mark its evidence."""
    d = tmp_path / 'claims' / name
    d.mkdir(parents=True)
    (d / 'CLAIM.md').write_text('# Claim ' + name + '\n')
    (d / 'VERDICT.md').write_text('HOLDS\n')
    if pod:
        (d / 'pod_run.sh').write_text('#!/bin/sh\necho ran on a rented box\n')
    return d


def ledger(tmp_path, rows, root='recheck', when=AFTER):
    """A re-check ledger in its own root, dated by its mtime like the real one."""
    d = tmp_path / root
    d.mkdir(parents=True, exist_ok=True)
    path = d / ce.RECHECK_LEDGER
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{c: '' for c in COLUMNS}, **row})
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
    return d


def row(name, rerun='match', verdict='SURVIVES'):
    return dict(id=name, friend='Contact Alpha', rerun=rerun, verdict=verdict, reason='invented')


# --- Gate 1: an independent re-check -------------------------------------------------------

def test_a_claim_with_no_independent_recheck_fails(tmp_path):
    """The failing case for gate 1: its own referee passed it and nobody else rebuilt it.

    This is the 11-05 lesson in one test: 528 claims went through a referee that passed 98%
    of them, so a pass meant nothing.
    """
    first, second = claim(tmp_path, 'alpha_A001'), claim(tmp_path, 'alpha_A002')
    root = ledger(tmp_path, [row('alpha_A001')])
    failures, warnings = ce.findings([first, second], [str(root)], INCIDENT)
    assert warnings == []
    assert len(failures) == 1
    assert failures[0].startswith('RECHECK_MISSING claim alpha_A002:')
    assert 'no independent re-check recorded' in failures[0]
    assert 'separate model' in failures[0], 'the message says what would fix it'


def test_a_claim_rebuilt_and_surviving_passes(tmp_path):
    """The passing case for gate 1: a separate model rebuilt it and got the same answer."""
    one = claim(tmp_path, 'alpha_A001')
    root = ledger(tmp_path, [row('alpha_A001')])
    assert ce.findings([one], [str(root)], INCIDENT) == ([], [])


def test_a_recheck_that_did_not_reproduce_or_that_dropped_the_claim_fails(tmp_path):
    """Two of thirteen claims did not survive the 11-05 re-check, so this is not a formality."""
    one, two = claim(tmp_path, 'alpha_A003'), claim(tmp_path, 'alpha_A004')
    root = ledger(tmp_path, [row('alpha_A003', rerun='differs'),
                             row('alpha_A004', verdict='DROP')])
    failures, _ = ce.findings([one, two], [str(root)], INCIDENT)
    assert len(failures) == 2
    assert failures[0].startswith('RECHECK_FAILED') and 'differs' in failures[0]
    assert failures[1].startswith('RECHECK_DROPPED') and 'DROP' in failures[1]


def test_several_recheck_roots_are_read_and_a_missing_claim_directory_fails(tmp_path):
    one, two = claim(tmp_path, 'alpha_A005'), claim(tmp_path, 'beta_B001')
    first = ledger(tmp_path, [row('alpha_A005')], root='recheck')
    second = ledger(tmp_path, [row('beta_B001')], root='recheck-beta')
    assert ce.findings([one, two], [str(first), str(second)], INCIDENT) == ([], [])
    gone = tmp_path / 'claims' / 'alpha_A999'
    failures, _ = ce.findings([gone], [str(first), str(second)], INCIDENT)
    assert len(failures) == 1 and failures[0].startswith('CLAIM_MISSING')


def test_roots_may_be_globs_and_a_missing_ledger_fails_closed(tmp_path):
    one = claim(tmp_path, 'alpha_A006')
    ledger(tmp_path, [row('alpha_A006')], root='recheck_1105')
    assert ce.findings([one], [str(tmp_path / 'recheck_*')], INCIDENT) == ([], [])
    failures, _ = ce.findings([one], [str(tmp_path / 'nothing_here_*')], INCIDENT)
    assert len(failures) == 1 and failures[0].startswith('RECHECK_LEDGER_MISSING')


# --- Gate 2: pod-touched evidence, re-run after the incident -------------------------------

def test_pod_touched_evidence_rechecked_before_the_incident_fails(tmp_path):
    """The failing case for gate 2: the re-check predates the host incident.

    Evidence produced on a rented pod stops being evidence when the host is compromised.
    A re-check from the day before cannot answer for it.
    """
    one = claim(tmp_path, 'alpha_A007', pod=True)
    root = ledger(tmp_path, [row('alpha_A007')], when=BEFORE)
    failures, warnings = ce.findings([one], [str(root)], INCIDENT)
    assert warnings == []
    assert len(failures) == 1
    assert failures[0].startswith('POD_EVIDENCE_STALE') and 'pod_run.sh' in failures[0]
    assert 'before the host incident' in failures[0] and INCIDENT in failures[0]
    assert 're-run it somewhere trusted' in failures[0]


def test_pod_touched_evidence_rerun_after_the_incident_passes(tmp_path):
    """The passing case for gate 2, and what was done by hand on 11-05."""
    one = claim(tmp_path, 'alpha_A008', pod=True)
    root = ledger(tmp_path, [row('alpha_A008')], when=AFTER)
    assert ce.findings([one], [str(root)], INCIDENT) == ([], [])


def test_a_claim_never_near_a_pod_is_not_asked_about_the_incident(tmp_path):
    one = claim(tmp_path, 'alpha_A009', pod=False)
    root = ledger(tmp_path, [row('alpha_A009')], when=BEFORE)
    assert ce.findings([one], [str(root)], INCIDENT) == ([], [])


def test_a_row_may_carry_its_own_time_which_beats_the_ledger_mtime(tmp_path):
    """A ledger rewritten later must not vouch for an older row, or the reverse."""
    one = claim(tmp_path, 'alpha_A010', pod=True)
    root = ledger(tmp_path, [dict(row('alpha_A010'), utc='2026-11-04T09:00:00+00:00')], when=AFTER)
    failures, _ = ce.findings([one], [str(root)], INCIDENT)
    assert len(failures) == 1 and 'by utc' in failures[0]
    root = ledger(tmp_path, [dict(row('alpha_A010'), utc='2026-11-05T09:00:00+00:00')],
                  root='recheck2', when=BEFORE)
    assert ce.findings([one], [str(root)], INCIDENT) == ([], [])


def test_without_a_configured_incident_pod_evidence_only_warns(tmp_path):
    """An installation that has had no host incident configures none, and is not blocked."""
    one = claim(tmp_path, 'alpha_A011', pod=True)
    root = ledger(tmp_path, [row('alpha_A011')], when=BEFORE)
    failures, warnings = ce.findings([one], [str(root)], None)
    assert failures == []
    assert len(warnings) == 1 and warnings[0].startswith('POD_EVIDENCE_UNDATED')
    assert 'no host incident is configured' in warnings[0]


def test_a_malformed_incident_time_is_refused_rather_than_ignored(tmp_path):
    one = claim(tmp_path, 'alpha_A012')
    root = ledger(tmp_path, [row('alpha_A012')])
    with pytest.raises(ValueError):
        ce.findings([one], [str(root)], 'last Tuesday')


# --- Requiring claims at all ---------------------------------------------------------------

def test_a_lane_can_require_that_a_result_email_names_its_claims(tmp_path):
    root = ledger(tmp_path, [row('alpha_A013')])
    failures, _ = ce.findings([], [str(root)], INCIDENT, require=True)
    assert len(failures) == 1 and failures[0].startswith('CLAIMS_REQUIRED') and '--claim' in failures[0]
    assert ce.findings([], [str(root)], INCIDENT, require=False) == ([], [])


def test_pod_markers_are_found_by_name_and_tolerate_an_unreadable_claim(tmp_path):
    one = claim(tmp_path, 'alpha_A014')
    (one / 'pod-inputs.json').write_text('{}')
    (one / 'deploy.py').write_text('print(1)\n')
    assert ce.pod_markers(one) == ['pod-inputs.json']
    assert ce.pod_markers(tmp_path / 'claims' / 'gone') == []
