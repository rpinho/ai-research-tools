"""New-lane safety boundaries without fabricated reading, trials or results."""
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harness'))
import rh
import lane_setup


def create(root, *extra):
    return rh.main(['--root', str(root), 'lane', 'new', 'sequence',
        '--problem', 'Predict the next integer', '--metric', 'exact-match accuracy',
        '--dev-set', 'public-dev', '--hidden-test', 'organizer-hidden',
        '--split', 'group', '--split-reason', 'Provisionally hold out sequence families; sampling unverified',
        '--sealed-set-id', 'organizer-fixed-hidden', '--deadline-days', '7',
        '--submissions', '2', '--submission-rules', 'Two uploads by deadline', *extra])


def lane(root):
    return root / 'lanes/sequence'


def test_create_is_honest_frozen_and_lint_clean(tmp_path):
    assert create(tmp_path) == 0
    target = lane(tmp_path)
    record, policy = lane_setup.read(target)
    assert policy['policy_version'] == 2 and policy['submissions'] == 2
    assert policy['sealed_budget'] == 1 and policy['n_trials_total'] == 0
    assert policy['split'] == 'group' and policy['trials'] == []
    assert (lane_setup.stamp(record['frozen']['deadline']) - lane_setup.stamp(record['created'])).days == 7
    assert record['frozen']['worker_sealed_access'] is False
    assert record['frozen']['public_board_selection'] is False
    assert not list(target.glob('*.jsonl'))
    assert rh.main(['--root', str(tmp_path), 'lint', 'sequence']) == 0
    assert create(tmp_path) == 2  # refuse overwriting the frozen lane


def test_random_requires_affirmative_shared_unit_statement(tmp_path):
    assert create(tmp_path, '--split', 'random') == 2
    assert not lane(tmp_path).exists()
    assert create(tmp_path, '--split', 'random', '--test-shares-units-statement',
                  'Organizer confirms unseen rows from the same generators') == 0


@pytest.mark.parametrize('filename,key,value', [
    ('SELECTION.json', 'submissions', 3),
    ('SELECTION.json', 'sealed_set_id', 'replacement-hidden'),
    ('LANE.json', 'bar', 'lowered after results'),
    ('LANE.json', 'worker_sealed_access', True),
])
def test_mutation_refuses_lint_and_dry_run(tmp_path, filename, key, value):
    assert create(tmp_path) == 0
    path = lane(tmp_path) / filename
    data = json.loads(path.read_text())
    (data['frozen'] if filename == 'LANE.json' else data)[key] = value
    path.write_text(json.dumps(data))
    assert rh.main(['--root', str(tmp_path), 'lint', 'sequence']) == 1
    assert rh.main(['--root', str(tmp_path), 'sweep', 'sequence', '--dry-run']) == 1


def test_preview_is_read_only_and_real_run_requires_enabling(tmp_path, capsys):
    assert create(tmp_path) == 0
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert rh.main(['--root', str(tmp_path), 'sweep', 'sequence', '--dry-run']) == 0
    text = capsys.readouterr().out
    assert 'DRY RUN' in text and 'stage. Run synchronously' in text
    assert 'search -> read -> kill' in text and 'No submissions' in text
    assert rh.main(['--root', str(tmp_path), 'sweep', 'sequence']) == 1
    assert 'workers disabled' in capsys.readouterr().err
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_setup_grace_expires_and_work_ends_it(tmp_path, monkeypatch):
    assert create(tmp_path) == 0
    target = lane(tmp_path)
    assert lane_setup.in_setup(target, rh)
    real_datetime = dt.datetime

    class Tomorrow(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + dt.timedelta(hours=25)

    monkeypatch.setattr(lane_setup.dt, 'datetime', Tomorrow)
    assert not lane_setup.in_setup(target, rh)
    result = rh.checked_lint(target)
    assert result['status'] == 'FAIL'
    assert {'queue-depth', 'external-reading', 'intake-freshness'} <= {x['check'] for x in result['failures']}
    monkeypatch.setattr(lane_setup.dt, 'datetime', real_datetime)
    rh.append(target / 'IDEAS.jsonl', dict(id='DEV-1', status='new', source_type='internal', created=rh.now()))
    assert not lane_setup.in_setup(target, rh)
    assert rh.checked_lint(target)['status'] == 'FAIL'


def test_lane_limits_and_new_experiments_cannot_downgrade_v2(tmp_path):
    assert create(tmp_path) == 0
    target = lane(tmp_path)
    rh.append(target / 'IDEAS.jsonl', dict(id='DEV-1', status='testing', policy_version=1))
    assert 'require v2' in ';'.join(lane_setup.audit(target, rh))
    path = target / 'INTAKE.toml'
    path.write_text(path.read_text().replace('selection_submissions = 2', 'selection_submissions = 3'))
    assert 'submissions' in ';'.join(lane_setup.audit(target, rh))
