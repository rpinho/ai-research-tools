"""Append-only supersession and the deliberately narrow cutover exception."""
import contextlib
from copy import deepcopy
import datetime as dt
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import idea_policy as policy
import rh
from policy_fixtures import prior_art

GAP = '2026-11-03T00:30:00+00:00'
LATER = '2026-11-03T00:50:00+00:00'
DECISION = '2026-11-03T01:00:00+00:00'


@pytest.fixture
def lane(tmp_path):
    lane = tmp_path/'lanes/test'
    lane.mkdir(parents=True)
    (lane/'INTAKE.toml').write_text('keywords = []\n')
    return lane


def closed(lane, **kw):
    result = lane/'old-result.md'
    result.write_text('historical result, no prospective receipt\n')
    row = dict(id='original', created=GAP, revised=GAP, status='closed', event='close',
               source_type='internal', claim='testable claim', board='B', test='T', bar='>1',
               result_path=str(result), closed_reason='historical close', mechanical=True)
    row.update(kw)
    return row


def reopened(**kw):
    row = dict(id='reopened', created=LATER, status='new', event='reopen', reopens='original',
               source_type='internal', claim='fresh attempt', board='B', test='T', bar='>1', result_path='')
    row.update(kw)
    return row


def exception(lane, **kw):
    evidence = lane/'claims/original'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence/'VERDICT.md').write_text('HOLDS\nIndependent referee evidence.\n')
    entry = dict(id='original', reason='refereed result imported during old sync gap',
                 evidence_path=str(evidence), decided_by="general (claude-opus-5-5) per Operator's no-fabrication rule",
                 utc=DECISION)
    entry.update(kw)
    rh.append(lane/'lint_exceptions.jsonl', entry)
    return entry


def assert_receipts(errors):
    assert set(s.split(': ', 1)[1] for s in errors) == policy.RECEIPT_ERRORS


def test_only_structured_links_supersede_and_history_is_unchanged(lane):
    original = closed(lane)
    child = reopened()
    child.pop('reopens')
    child['note'] = 'REOPEN original: now superseded'
    assert_receipts(policy.audit([original, child], lane)[0])
    history = [original, dict(child, reopens='original')]
    before = deepcopy(history)
    errors, warnings = policy.audit(history, lane)
    assert not errors
    assert len(warnings) == 4
    assert all('superseded by reopen reopened' in s for s in warnings)
    assert history == before


def test_append_only_reopen_link_snapshot(lane):
    child = reopened()
    child.pop('reopens')
    linked = dict(child, event='reopen_link', revised=DECISION, reopens='original')
    errors, warnings = policy.audit([closed(lane), child, linked], lane)
    assert not errors and len(warnings) == 4


@pytest.mark.parametrize('child', [reopened(id='original'), reopened(reopens='unknown'),
    reopened(created='2026-11-03T00:20:00Z'), reopened(reopens=None)])
def test_invalid_links_do_not_downgrade(lane, child):
    errors, _ = policy.audit([closed(lane), child], lane)
    assert any('invalid reopen link' in s for s in errors)
    assert any('result lacks frozen bar receipt' in s for s in errors)


def test_link_requires_original_already_closed_at_child_creation(lane):
    original = closed(lane)
    errors, _ = policy.audit([dict(original, status='new'), reopened(), original], lane)
    assert any('invalid reopen link' in s for s in errors)
    assert any('result lacks config' in s for s in errors)


def test_link_conflicts_fail_closed(lane):
    original = closed(lane)
    other = dict(original, id='other')
    errors, _ = policy.audit([original, other, reopened(), reopened(reopens='other', revised=DECISION)], lane)
    assert any('conflicting original ids' in s for s in errors)
    assert any('result lacks config' in s for s in errors)


def test_reopen_itself_must_meet_all_new_rules(lane):
    child = reopened(status='testing', event='promote', result_path=str(lane/'missing-result'))
    errors, warnings = policy.audit([closed(lane), child], lane)
    assert all('idea reopened:' in s for s in errors)
    assert any('prior-art' in s for s in errors)
    assert any('missing frozen bar receipt' in s for s in errors)
    assert any('result lacks config' in s for s in errors)
    assert len(warnings) == 4


@pytest.mark.parametrize('method', ['reopen', 'exception'])
def test_nonreceipt_integrity_errors_are_never_downgraded(lane, method):
    original = closed(lane)
    history = [original, dict(original, event='edit', revised='2026-11-03T00:40:00Z', bar='tampered')]
    if method == 'reopen':
        history.append(reopened())
    else:
        exception(lane)
    errors, warnings = policy.audit(history, lane)
    assert any('bar edited after first result' in s for s in errors)
    assert any('WARNING:' in s for s in warnings)


def test_reopen_does_not_waive_a_future_original_result(lane):
    original = closed(lane)
    later = dict(original, event='close', revised=DECISION, result_path='/later/result')
    errors, _ = policy.audit([original, reopened(), later], lane)
    assert any('result has no earlier frozen bar event' in s for s in errors)
    assert any('result lacks config' in s for s in errors)


@pytest.mark.parametrize('offset,valid', [(-1,False),(0,True),(1,True),(7200,True),(7201,False)])
def test_exception_window_uses_close_time_with_inclusive_boundaries(lane, offset, valid):
    when = (dt.datetime.fromisoformat(policy.FROM) + dt.timedelta(seconds=offset)).isoformat()
    exception(lane, utc='2026-11-03T02:30:00Z')
    # Freeze the validation clock to a later date for the upper boundary tests.
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026,11,4,tzinfo=dt.timezone.utc)
    with patch.object(policy.dt, 'datetime', Clock):
        errors, warnings = policy.audit([closed(lane, revised=when)], lane)
    if valid:
        assert not errors and len(warnings) == 4
        assert all('lint exception' in s and 'evidence=' in s for s in warnings)
    else:
        assert any('no close in [cutover' in s for s in errors)
        if offset > 0:
            assert any('result lacks config' in s for s in errors)


def test_exception_cannot_use_read_timestamp_to_move_old_close_into_gap(lane):
    old = closed(lane, created='2026-11-02T20:00:00Z', revised='2026-11-02T20:00:00Z')
    exception(lane)
    errors, _ = policy.audit([old, dict(old, event='read', revised=GAP)], lane)
    assert any('no close in [cutover' in s for s in errors)


@pytest.mark.parametrize('field,value', [('reason',''),('evidence_path','/does/not/exist'),
    ('decided_by','gpt-6.1-sol'),('utc','2026-11-03'),('utc','2099-01-01T00:00:00Z'),
    ('id','unknown'), ('utc','2026-11-03T00:20:00Z')])
def test_invalid_exception_fails_and_preserves_receipt_errors(lane, field, value):
    exception(lane, **{field:value})
    errors, warnings = policy.audit([closed(lane)], lane)
    assert any('lint_exceptions.jsonl:1:' in s for s in errors)
    assert any('result lacks config' in s for s in errors)
    assert not any('lint exception' in s for s in warnings)


@pytest.mark.parametrize('text,valid', [('HOLDS\n',True),('HOLDS WITH CAVEATS\n',True),
    ('# VERDICT: HOLDS\n',True),('FAILS\nEarlier quote: HOLDS\n',False),
    ('DOES NOT HOLD\n',False),('',False),('HOLDS NOT\n',False)])
def test_verdict_is_required_and_not_a_substring_search(lane, text, valid):
    entry = exception(lane)
    (Path(entry['evidence_path'])/'VERDICT.md').write_text(text)
    errors, warnings = policy.audit([closed(lane)], lane)
    assert bool(errors) != valid
    assert bool(warnings) == valid


def test_missing_verdict_and_arbitrary_file_are_not_evidence(lane):
    entry = exception(lane)
    verdict = Path(entry['evidence_path'])/'VERDICT.md'
    # This fixture file was created in this test session.
    verdict.unlink()
    (verdict.parent/'REPORT.md').write_text('HOLDS')
    assert any('containing a VERDICT' in s for s in policy.audit([closed(lane)],lane)[0])


@pytest.mark.parametrize('text', ['{broken\n', '[]\n', '{"id":"original"}\n'])
def test_malformed_exception_is_a_lint_error(lane,text):
    (lane/'lint_exceptions.jsonl').write_text(text)
    errors,_ = policy.audit([closed(lane)],lane)
    assert any('lint_exceptions.jsonl:1:' in s for s in errors)
    assert any('result lacks config' in s for s in errors)


def test_exception_does_not_waive_later_closes(lane):
    original=closed(lane)
    exception(lane)
    reopened_same=dict(original,status='new',revised=LATER,event='edit',result_path='')
    later=dict(original,revised='2026-11-03T02:20:00Z',result_path='/new/result')
    errors,_=policy.audit([original,reopened_same,later],lane)
    assert any('no silent close' in s for s in errors)
    assert any('result lacks config' in s for s in errors)


def test_other_close_error_kinds_stay_errors(lane):
    exception(lane)
    errors,warnings=policy.audit([closed(lane,attempts_declared=1)],lane)
    assert any('--attempts must be' in s for s in errors)
    assert len(warnings)==3


def test_cli_reopen_is_new_id_with_fresh_evidence_and_old_bytes_preserved(lane):
    original=closed(lane, prior_art='/old/prior', frozen_bar={'at':GAP},
                    frozen_bar_sha256='old', attempt_records=[{'old':'receipt'}],
                    decided_by='old-decider', no_test_reason='old disposition')
    rh.append(lane/'IDEAS.jsonl',original)
    before=(lane/'IDEAS.jsonl').read_bytes()
    out,err=io.StringIO(),io.StringIO()
    with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
        rc=rh.main(['--root',str(lane.parents[1]),'ideas','reopen','test','original'])
    assert rc==0,err.getvalue()
    assert (lane/'IDEAS.jsonl').read_bytes().startswith(before)
    history=rh.rows(lane/'IDEAS.jsonl');child=history[-1]
    assert child['id']!='original' and child['reopens']=='original'
    assert child['status']=='new' and child['event']=='reopen'
    assert not child['result_path'] and not child['closed_reason']
    assert all(k not in child for k in ('prior_art','frozen_bar','frozen_bar_sha256','attempt_records',
                                      'decided_by','no_test_reason','friend_queue_id'))
    assert rh.latest(history)['original']==original
    # New row is subject to the ordinary promotion gate.
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(err):
        rc=rh.main(['--root',str(lane.parents[1]),'ideas','promote','test',child['id']])
    assert rc==2


def test_cli_reopen_rejects_open_original_without_append(lane):
    rh.append(lane/'IDEAS.jsonl',dict(closed(lane),status='new'))
    before=(lane/'IDEAS.jsonl').read_bytes()
    with contextlib.redirect_stderr(io.StringIO()):
        assert rh.main(['--root',str(lane.parents[1]),'ideas','reopen','test','original'])==2
    assert (lane/'IDEAS.jsonl').read_bytes()==before


def test_rh_lint_keeps_other_gates_and_prints_exception_citation(lane):
    original=closed(lane,source_type='paper',source_url='')
    rh.append(lane/'IDEAS.jsonl',original)
    exception(lane)
    out=io.StringIO()
    with patch.object(rh,'banned_cs_sessions',return_value=[]),contextlib.redirect_stdout(out):
        assert rh.lint(lane)==1
    text=out.getvalue()
    assert 'WARN test: idea original:' in text and 'lint exception' in text
    assert 'FAIL test: IDEAS.jsonl row 1: external source requires source_url' in text
    assert 'queue depth' in text
    assert 'FAIL test: idea original: result lacks' not in text
