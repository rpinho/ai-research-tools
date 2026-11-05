"""Outreach commands and deterministic policy checks; all data stays in tmp_path."""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import itertools
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import outreach
import rh

# Eight independently written approaches, rather than edits to a common template.
VARIED = ['A sine wave has one extra peak. Which sample interval reveals the alias? I would inspect the sampling grid.\n\nRegards,\nOperator', 'The square sequence changes sign at every step. Can a shorter period explain it? An exact enumeration would settle this.\n\nRegards,\nOperator', 'A sparse impulse can shift a spectral estimate. Where should an independent control place that impulse? The noise model remains fixed.\n\nRegards,\nOperator', 'Sorting the integers changes their adjacency. Does preserving the sum preserve the histogram too? My current check uses only a sum.\n\nRegards,\nOperator', 'A recursive sequence stops at a fixed depth. What terminal condition distinguishes zero from missing output? I can enumerate the shallow cases.\n\nRegards,\nOperator', 'The checker returns an empty certificate. Is that evidence of failure or a valid empty set? A type contract would clarify it.\n\nRegards,\nOperator', 'An interpolation has equal endpoints. Could it still exceed the endpoint range? I want a counterexample before adding a bound.\n\nRegards,\nOperator', 'A rotated grid preserves distances. Which invariant would expose an accidental reflection? I would test oriented areas.\n\nRegards,\nOperator']


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'harness'
    lane = root / 'lanes/test'
    lane.mkdir(parents=True)
    contacts = tmp_path / 'contacts.json'
    contacts.write_text(json.dumps([{'name': n} for n in ('Contact Alpha', 'Contact Beta')]))
    monkeypatch.setattr(outreach, 'CONTACTS', contacts)
    monkeypatch.setattr(rh, 'get_url', lambda *a: pytest.fail('network forbidden'))
    return root, lane


def cli(root, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = rh.main(['--root', str(root), 'outreach', *map(str, args)])
    return rc, out.getvalue(), err.getvalue()


def draft(env, text=None, to='Contact Alpha', channel='email', batch=None, lane_name='test', attach=None):
    root, _ = env
    path = root / ('body-' + str(len(list(root.glob('body-*')))) + '.txt')
    path.write_text(text or VARIED[0])
    args = ['draft', lane_name, '--to', to, '--channel', channel, '--purpose', 'Learn a biological constraint', '--file', path]
    if batch:
        args += ['--batch', batch]
    if attach:
        args += ['--attach', attach]
    rc, out, err = cli(root, *args)
    assert rc == 0, err
    return out.strip(), path


def approve(env):
    ident, path = draft(env)
    assert cli(env[0], 'lint', 'test', ident)[0] == 0
    assert cli(env[0], 'approve', 'test', ident, '--quote', 'Send this one')[0] == 0
    return ident, path


def test_lifecycle_all_commands(env):
    root, lane = env
    ident, path = approve(env)
    before = rh.rows(lane / 'OUTREACH.jsonl')
    row = outreach.current(lane, rh)[ident]
    assert row['to_key'] == 'Contact Alpha'
    assert row['approval']['quote'] == 'Send this one'
    assert row['approval']['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert cli(root, 'sent', 'test', ident, '--message-id', 'm1', '--thread-id', 't1', '--via', 'api')[0] == 0
    row = outreach.current(lane, rh)[ident]
    assert outreach.time_of(row['follow_up_due']) - outreach.time_of(row['sent']['utc']) == dt.timedelta(days=7)
    assert row['sent']['thread_id'] == 't1'
    assert ident in cli(root, 'due', '--all')[1]
    assert ident not in cli(root, 'due')[1]
    assert cli(root, 'reply', 'test', ident, '--summary', 'A staging control exists')[0] == 0
    assert ident not in cli(root, 'due', '--all')[1]
    assert cli(root, 'close', 'test', ident, '--reason', 'Question answered')[0] == 0
    assert outreach.current(lane, rh)[ident]['status'] == 'closed'
    assert rh.rows(lane / 'OUTREACH.jsonl')[:len(before)] == before
    assert json.loads(cli(root, 'status', '--json')[1])[0]['status'] == 'closed'
    assert ident in cli(root, 'status', 'test')[1]
    assert (lane / 'OUTREACH.jsonl.bak').exists()


def test_drop_command(env):
    ident, _ = draft(env)
    assert cli(env[0], 'drop', 'test', ident, '--reason', 'No longer useful')[0] == 0
    assert outreach.current(env[1], rh)[ident]['status'] == 'dropped'


@pytest.mark.parametrize('stage', ['lint', 'approval'])
def test_edit_invalidates_hash(env, stage):
    ident, path = draft(env)
    assert cli(env[0], 'lint', 'test', ident)[0] == 0
    if stage == 'approval':
        assert cli(env[0], 'approve', 'test', ident, '--quote', 'Looks fine')[0] == 0
    path.write_text(path.read_text() + '\nAnother sentence.')
    if stage == 'lint':
        rc, _, err = cli(env[0], 'approve', 'test', ident, '--quote', 'Fine')
    else:
        rc, _, err = cli(env[0], 'sent', 'test', ident, '--message-id', 'm', '--via', 'browser')
    assert rc == 2 and 'changed' in err


def test_approve_and_sent_require_predecessor(env):
    ident, _ = draft(env)
    assert cli(env[0], 'approve', 'test', ident, '--quote', 'Fine')[0] == 2
    assert cli(env[0], 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 2
    assert cli(env[0], 'reply', 'test', ident, '--summary', 'Reply')[0] == 2


def test_approval_batch_all_or_none(env):
    a, _ = draft(env, VARIED[0], batch='b')
    b, path = draft(env, VARIED[1], to='Contact Beta', batch='b')
    assert cli(env[0], 'lint', 'test', '--batch', 'b')[0] == 0
    path.write_text('Edited.')
    assert cli(env[0], 'approve', 'test', '--batch', 'b', '--quote', 'Both')[0] == 2
    assert all(r['status'] == 'linted' for r in outreach.current(env[1], rh).values())
    assert cli(env[0], 'lint', 'test', '--batch', 'b')[0] == 0
    assert cli(env[0], 'approve', 'test', '--batch', 'b', '--quote', 'Both')[0] == 0


def test_varied_batch_passes_every_check(env):
    for i, text in enumerate(VARIED):
        draft(env, text, to='@varied' + str(i), channel='x_dm', batch='varied')
    rc, out, err = cli(env[0], 'lint', 'test', '--batch', 'varied')
    assert rc == 0, out + err
    assert out.count('PASS') == 8


def test_identical_batch_reports_pairs(env):
    a, _ = draft(env, batch='same')
    b, _ = draft(env, to='Contact Beta', batch='same')
    rc, out, _ = cli(env[0], 'lint', 'test', '--batch', 'same')
    assert rc == 1 and 'pair ' + a + ' / ' + b in out
    assert 'body-5gram' in out and 'first Jaccard' in out and 'last Jaccard' in out


def test_recent_sends_compared_even_without_batch(env):
    ident, _ = approve(env)
    assert cli(env[0], 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 0
    new, _ = draft(env, to='Contact Beta')
    rc, out, _ = cli(env[0], 'lint', 'test', new)
    assert rc == 1 and 'pair ' + new + ' / ' + ident in out


def test_old_sends_not_compared(env):
    ident, _ = draft(env)
    old = outreach.current(env[1], rh)[ident]
    old.update(status='sent', sent={'utc': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=15)).isoformat()})
    rh.append(env[1] / 'OUTREACH.jsonl', old)
    new, _ = draft(env, to='Contact Beta')
    assert cli(env[0], 'lint', 'test', new)[0] == 0


@pytest.mark.parametrize('text,check', [('Hello! Can you help?', 'voice'), ('What changed? Why?', 'questions'),
                                       ('See https:// for details.', 'links'), ('Read http://bad:port/test.', 'links')])
def test_lint_failures(env, text, check):
    ident, _ = draft(env, text)
    rc, out, _ = cli(env[0], 'lint', 'test', ident)
    assert rc == 1 and check + ':' in out
    assert outreach.current(env[1], rh)[ident]['status'] == 'drafted'


def test_x_reply_uses_reply_flag(env):
    ident, _ = draft(env, 'A' * 245 + '.', to='@scientist', channel='x_reply')
    rc, out, _ = cli(env[0], 'lint', 'test', ident)
    assert rc == 1 and 'reply longer' in out


def test_contact_validation_and_ledger_privacy(env):
    root, lane = env
    path = root / 'private.txt'
    path.write_text('A question?')
    assert cli(root, 'draft', 'test', '--to', 'Unknown', '--channel', 'email', '--purpose', 'Learn', '--file', path)[0] == 2
    assert cli(root, 'draft', 'test', '--to', ('private' + chr(64) + 'example.org'), '--channel', 'email', '--purpose', 'Learn', '--file', path)[0] == 2
    ident, _ = draft(env)
    env_contacts = json.loads(outreach.CONTACTS.read_text())
    outreach.CONTACTS.write_text('[]')
    assert 'contact:' in cli(root, 'lint', 'test', ident)[1]
    outreach.CONTACTS.write_text(json.dumps(env_contacts))
    assert cli(root, 'lint', 'test', ident)[0] == 0
    assert cli(root, 'approve', 'test', ident, '--quote', ('send private' + chr(64) + 'example.org'))[0] == 2
    assert not outreach.EMAIL.search((lane / 'OUTREACH.jsonl').read_text())


def test_cross_lane_collision_and_override(env):
    root, lane = env
    ident, _ = approve(env)
    assert cli(root, 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 0
    other = root / 'lanes/other'
    other.mkdir()
    new, _ = draft(env, VARIED[2], lane_name='other')
    rc, out, _ = cli(root, 'lint', 'other', new)
    assert rc == 1 and 'collision:' in out and 'test/' + ident in out
    assert cli(root, 'lint', 'other', new, '--allow-collision', 'Operator requested a second message')[0] == 0
    assert cli(root, 'approve', 'other', new, '--quote', 'Send again')[0] == 0
    assert cli(root, 'sent', 'other', new, '--message-id', 'second', '--via', 'browser')[0] == 0


def test_collision_rechecked_at_sent(env):
    root, lane = env
    ident, _ = approve(env)
    other = root / 'lanes/other'
    other.mkdir()
    new, _ = draft(env, VARIED[2], lane_name='other')
    assert cli(root, 'lint', 'other', new)[0] == 0
    assert cli(root, 'approve', 'other', new, '--quote', 'Send')[0] == 0
    assert cli(root, 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 0
    assert cli(root, 'sent', 'other', new, '--message-id', 'second', '--via', 'api')[0] == 2


def test_team_reuses_science_checks_and_two_questions(env):
    root, _ = env
    (root / 'lanes/team').mkdir()
    (root / 'lanes/team/OUTREACH.toml').write_text('question_limit = 2\nscience_checks = true\n')
    ident, _ = draft(env, 'Would this help? Is it useful or slop?', lane_name='team')
    assert cli(root, 'lint', 'team', ident)[0] == 0
    with patch.object(outreach, 'science_checks', wraps=outreach.science_checks) as check:
        assert cli(root, 'lint', 'team', ident)[0] == 0
        check.assert_called_once()
    # Actual legacy function supplies the results/slop diagnostic as well as review requirements.
    errors = outreach.science_checks('Would this help?', [root / 'results.pdf'])
    assert any('useful or slop' in e for e in errors)


def test_config_question_limit_and_follow_up(env):
    root, lane = env
    (lane / 'OUTREACH.toml').write_text('question_limit = 2\nfollow_up_days = 10\n')
    ident, _ = draft(env, 'Which landmark? At what age?')
    assert cli(root, 'lint', 'test', ident)[0] == 0
    assert cli(root, 'approve', 'test', ident, '--quote', 'Send')[0] == 0
    assert cli(root, 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 0
    row = outreach.current(lane, rh)[ident]
    assert outreach.time_of(row['follow_up_due']) - outreach.time_of(row['sent']['utc']) == dt.timedelta(days=10)


def test_health_due_stale_approval_and_no_approval(env):
    root, lane = env
    ident, _ = approve(env)
    row = outreach.current(lane, rh)[ident]
    row['approval']['utc'] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=49)).isoformat()
    rh.append(lane / 'OUTREACH.jsonl', row)
    assert 'over 48 h' in ' '.join(outreach.health(lane, rh))
    row.update(status='sent', approval=None, follow_up_due='2000-01-01T00:00:00+00:00')
    rh.append(lane / 'OUTREACH.jsonl', row)
    assert 'without approval' in ' '.join(outreach.health(lane, rh))
    assert 'overdue' in ' '.join(outreach.health(lane, rh))
    assert ident in cli(root, 'due', '--json')[1]
    assert cli(root, 'close', 'test', ident, '--reason', 'Done')[0] == 0
    assert outreach.health(lane, rh) == []


def test_rh_lint_integration(env):
    root, lane = env
    ident, _ = draft(env)
    row = outreach.current(lane, rh)[ident]
    row.update(status='sent', follow_up_due='2000-01-01T00:00:00+00:00')
    rh.append(lane / 'OUTREACH.jsonl', row)
    with patch.object(rh.cadence, 'lint', return_value=[]), patch.object(rh.pods, 'lint', return_value=[]):
        result = rh.lint_result(lane)
    assert result['outreach']['status'] == 'FAIL'
    assert any(f['check'] == 'outreach' for f in result['failures'])


def test_normalisation_names_links_titles_and_signature():
    a = 'Hi Alpha,\n\nI am reading “First Paper” at https://one.example/a. Can you explain the result?\n\nThanks,\nOperator'
    b = 'Hi Beta,\n\nI am reading “Second Paper” at https://two.example/b. Can you explain the result?\n\nBest,\nOperator'
    assert outreach.features(a, ('Alpha', 'Beta'))[:2] == outreach.features(b, ('Alpha', 'Beta'))[:2]
    assert outreach.normalise('Alpha says hello', ('Alpha',)) == outreach.normalise('Beta says hello', ('Beta',))


@pytest.mark.parametrize('url', ['https://example.org/a?x=3&y=4', 'http://localhost:8080/a', 'https://doi.org/10.1000/abc', 'https://[::1]/'])
def test_valid_links(url):
    assert outreach.link_errors('See ' + url) == []


@pytest.mark.parametrize('url', ['https://', 'https:/bad', 'www.example.org/a', 'ftp://example.org', 'https://bad..host', 'https://host:99999'])
def test_invalid_links(url):
    assert outreach.link_errors('See ' + url)




def test_url_query_does_not_add_a_question(env):
    ident, _ = draft(env, 'Can this landmark work? See https://example.org/a?q=signal for the diagram.')
    assert cli(env[0], 'lint', 'test', ident)[0] == 0


def test_team_x_still_defaults_to_one_question(env):
    (env[0] / 'lanes/team').mkdir()
    (env[0] / 'lanes/team/OUTREACH.toml').write_text('question_limit = 2\nscience_checks = true\n')
    ident, _ = draft(env, 'Which marker? At what age?', to='@friend', channel='x_reply', lane_name='team')
    assert 'questions:' in cli(env[0], 'lint', 'team', ident)[1]


def test_unreadable_draft_fails_lint_without_advancing(env):
    ident, path = draft(env)
    row = outreach.current(env[1], rh)[ident]
    row['draft_path'] = str(env[0] / 'missing.txt')
    rh.append(env[1] / 'OUTREACH.jsonl', row)
    assert cli(env[0], 'lint', 'test', ident)[0] == 1
    assert outreach.current(env[1], rh)[ident]['status'] == 'drafted'


def test_malformed_sent_record_reports_outreach_instead_of_crashing(env):
    ident, _ = draft(env)
    row = outreach.current(env[1], rh)[ident]
    row['status'] = 'sent'
    rh.append(env[1] / 'OUTREACH.jsonl', row)
    errors = outreach.health(env[1], rh)
    assert any('without approval' in e for e in errors)
    assert any('follow-up date' in e for e in errors)


def test_empty_batch_and_traversal_refused(env):
    assert cli(env[0], 'lint', 'test', '--batch', 'missing')[0] == 2
    assert cli(env[0], 'status', '../test')[0] == 2
    assert cli(env[0], 'lint', 'test', 'id', '--batch', 'both')[0] == 2


def test_reply_summary_refuses_quoted_text(env):
    ident, _ = approve(env)
    assert cli(env[0], 'sent', 'test', ident, '--message-id', 'm', '--via', 'api')[0] == 0
    assert cli(env[0], 'reply', 'test', ident, '--summary', 'They said "yes"')[0] == 2
    assert outreach.current(env[1], rh)[ident]['status'] == 'sent'


def test_collision_ids_are_scoped_to_lane():
    now = dt.datetime.now(dt.timezone.utc)
    draft = dict(id='same-id', lane='a', to_key='Contact Alpha')
    sent = dict(draft, lane='b', sent={'utc': now.isoformat()})
    assert outreach.collisions(draft, [sent], now) == [sent]


def test_outreach_only_lane_skips_research_checks(tmp_path):
    lane = tmp_path / 'lanes' / 'post-history'
    lane.mkdir(parents=True)
    (lane / 'OUTREACH.jsonl').write_text('')
    assert rh.outreach_only(lane)
    result = rh.lint_result(lane)
    assert result['depth'] is None and not any(f['check'] in ('queue-depth', 'intake-freshness') for f in result['failures'])
    (lane / 'IDEAS.jsonl').write_text('')
    assert not rh.outreach_only(lane)
