"""Offline scorecard contracts. No provider CLI or competition download is invoked."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from bench import cases, run, setup_data, selector

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('setup', cases.SETUPS)
def test_case_zero_stub_under_sixty_seconds(tmp_path, setup):
    start = time.monotonic()
    process = subprocess.run([sys.executable, str(REPO/'bench/run.py'), '--setup', setup,
        '--backend', 'stub', '--cases', 'toy', '--runtime', str(tmp_path/'runtime'),
        '--results', str(tmp_path/'results')], capture_output=True, text=True, timeout=50)
    assert process.returncode == 0, process.stdout+process.stderr
    result, = (tmp_path/'results').glob('*.json')
    scorecard = json.loads(result.read_text())
    toy, = scorecard['cases']
    assert toy['status'] == 'ok' and toy['local_validation_score'] == 1
    assert toy['hidden_test_score'] is None and toy['hidden_score_reason']
    assert toy['n_submissions'] == 1 and toy['receipt_verified']
    assert scorecard['usage']['usd'] == 0 and scorecard['usage']['tokens'] is None
    assert not (tmp_path/'runtime/paid-attempts.jsonl').exists()
    assert time.monotonic()-start < 60


def synthetic_source(monkeypatch):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['id', 'x', 'Strength'])
    # Exact feature duplicates deliberately have different IDs. They must stay together.
    for i in range(400):
        writer.writerow([2*i, i, i/10])
        writer.writerow([2*i+1, i, i/10+.01])
    payload = stream.getvalue().encode()
    import hashlib
    source = dict(cases.CASES['concrete'], rows=800, bytes=len(payload),
                  sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setitem(cases.CASES, 'concrete', source)
    monkeypatch.setattr(setup_data.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(payload))


def test_checksum_frozen_split_and_target_separation(monkeypatch, tmp_path):
    synthetic_source(monkeypatch)
    manifest = setup_data.prepare('concrete', tmp_path)
    assert setup_data.prepare('concrete', tmp_path) == manifest
    sets = []
    for name in ('train', 'dev', 'selection', 'hidden'):
        with (tmp_path/'concrete'/(name+'.csv')).open() as stream:
            reader = csv.DictReader(stream)
            assert ('Strength' in reader.fieldnames) == (name in ('train', 'dev'))
            sets.append({r['group'] for r in reader})
    assert sum(len(s) for s in sets) == len(set.union(*sets)) == 400
    frozen_path = tmp_path/'concrete/hidden-targets.csv'
    frozen_path.write_text(frozen_path.read_text()+'tampered\n')
    with pytest.raises(ValueError, match='checksum mismatch'):
        setup_data.prepare('concrete', tmp_path)
    assert manifest == json.loads((tmp_path/'concrete/manifest.json').read_text())


def test_download_checksum_failure_creates_no_data(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_data.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'bad data'))
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        setup_data.prepare('concrete', tmp_path)
    assert not (tmp_path/'concrete').exists()


def selector_fixture(root, hidden_offset=0):
    """Synthetic evidence with real paired errors, used only in tests."""
    data, output = root/'data', root/'output'
    data.mkdir(parents=True)
    output.mkdir()
    rows = [dict(id=str(i), group='unit-'+str(i), Strength=float(i)) for i in range(20)]
    for partition in ('selection', 'hidden'):
        changed = [dict(row, Strength=row['Strength']+(hidden_offset if partition == 'hidden' else 0)) for row in rows]
        setup_data.write_csv(data/(partition+'-targets.csv'), ['id', 'group', 'Strength'], changed)
    trials = [dict(id=name, name=name, status='completed', tuned_score=-error)
              for name, error in [('a', .1), ('b', .2), ('c', .3)]]
    predictions = dict(case='concrete', metric='rmse', trials=trials,
        selection_ids=[r['id'] for r in rows], hidden_ids=[r['id'] for r in rows],
        baseline=10., baseline_validation_score=-6., baseline_backtest_scores=[-6., -6.],
        backtest_splits=[dict(train_units=['start'], test_units=['next']),
                         dict(train_units=['start', 'next'], test_units=['last'])],
        candidates=[dict(name=t['name'], tuned_score=t['tuned_score'],
                    selection_predictions=[[r['Strength']-t['tuned_score'] for r in rows]]*2,
                    hidden_prediction=[r['Strength']-t['tuned_score'] for r in rows],
                    backtest_scores=[t['tuned_score']]*2) for t in trials])
    setup_data.write_json(output/'predictions.json', predictions)
    return data, output


@pytest.mark.parametrize('setup', cases.SETUPS)
def test_hidden_targets_cannot_affect_nomination_and_budget_is_single_use(tmp_path, setup):
    outcomes = []
    for name, offset in [('original', 0), ('perturbed', 1000)]:
        data, output = selector_fixture(tmp_path/name, offset)
        selector.evaluate('concrete', setup, data, output)
        outcomes.append(json.loads((output/'result.json').read_text()))
        assert outcomes[-1]['receipt_verified']
        with pytest.raises(ValueError, match='already consumed'):
            selector.evaluate('concrete', setup, data, output)
    assert outcomes[0]['selected'] == outcomes[1]['selected'] == 'a'
    assert outcomes[0]['hidden_test_score'] < outcomes[1]['hidden_test_score']


def test_failed_selector_consumes_budget_before_reading_targets(monkeypatch, tmp_path):
    data, output = selector_fixture(tmp_path)
    (data/'selection-targets.csv').rename(data/'withheld.csv')
    with pytest.raises(FileNotFoundError):
        selector.evaluate('concrete', 'v2-selection', data, output)
    assert len((output/'sealed-scoring.jsonl').read_text().splitlines()) == 1
    with pytest.raises(ValueError, match='already consumed'):
        selector.evaluate('concrete', 'v2-selection', data, output)


def test_usage_is_exact_and_tools_fail_closed(tmp_path):
    log = tmp_path/'log'
    log.write_text(json.dumps(dict(type='turn.completed', usage=dict(
        input_tokens=100, cached_input_tokens=40, output_tokens=20)))+'\n')
    usage = run.usage_from_log(log)
    assert usage['tokens'] == dict(input_tokens=100, cached_input_tokens=40, output_tokens=20)
    assert usage['usd'] is None and usage['usd_reason']
    with log.open('a') as stream:
        stream.write(json.dumps(dict(type='item.completed', item=dict(type='command_execution')))+'\n')
    with pytest.raises(ValueError, match='used a tool'):
        run.usage_from_log(log)


def test_recipe_validation_prevents_arbitrary_code():
    plan = cases.stub_plan(['toy', 'concrete'])
    cases.validate_plan(plan, ['toy', 'concrete'])
    plan['recipes']['concrete'][0]['command'] = 'anything'
    with pytest.raises(ValueError, match='invalid bounded recipe'):
        cases.validate_plan(plan, ['toy', 'concrete'])


def test_flags_ignore_small_score_changes_and_detect_spend_jump():
    previous = dict(cases=[dict(case='toy', status='ok', hidden_test_score=1)],
                    usage=dict(tokens=dict(input_tokens=100, output_tokens=10)), wall_time_seconds=1.)
    current = dict(cases=[dict(case='toy', status='ok', hidden_test_score=.99)],
                   usage=dict(tokens=dict(input_tokens=120, output_tokens=10)), wall_time_seconds=1.1)
    assert run.compare(current, previous) == []
    current['usage']['tokens']['input_tokens'] = 300
    assert run.compare(current, previous)[0]['kind'] == 'token_jump'


def test_timeout_ends_owned_descendants(tmp_path):
    sentinel = tmp_path/'still-alive'
    script = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'
              '"import time,pathlib; time.sleep(1); pathlib.Path('+repr(repr(str(sentinel)))+').touch()"]); time.sleep(10)')
    with pytest.raises(subprocess.TimeoutExpired):
        run.bounded([sys.executable, '-c', script], tmp_path/'process.log', timeout=.2)
    time.sleep(1.1)
    assert not sentinel.exists()


def test_duplicate_runner_lock_refuses_before_spend(tmp_path):
    import fcntl
    with (tmp_path/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            run.main(['--setup', 'v1-default', '--backend', 'stub', '--runtime', str(tmp_path)])


def test_paid_attempt_is_reserved_and_never_retried(monkeypatch, tmp_path):
    calls = []
    def failed_backend(*args):
        calls.append('started')
        raise RuntimeError('synthetic backend failure')
    monkeypatch.setattr(run, 'generate_plan', failed_backend)
    command = ['--setup', 'v1-default', '--backend', 'codex', '--allow-paid', '--cases', 'toy',
               '--runtime', str(tmp_path/'runtime'), '--results', str(tmp_path/'results')]
    assert run.main(command) == 1
    assert run.main(command) == 1
    assert calls == ['started']
    attempts = (tmp_path/'runtime/paid-attempts.jsonl').read_text().splitlines()
    assert len(attempts) == 1 and json.loads(attempts[0])['setup'] == 'v1-default'
    assert len(list((tmp_path/'results').glob('*.json'))) == 2


def test_changed_predictions_refused_before_sealed_accounting(tmp_path):
    data, output = selector_fixture(tmp_path)
    with pytest.raises(ValueError, match='changed after producer'):
        selector.evaluate('concrete', 'v2-selection', data, output, '0'*64)
    assert not (output/'sealed-scoring.jsonl').exists()
