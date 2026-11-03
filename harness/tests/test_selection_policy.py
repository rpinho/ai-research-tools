"""Prospective v2 freeze, CLI and lint contracts; all writes use isolated lanes."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rh
import idea_policy as policy
import selection_policy as selection
from policy_fixtures import prior_art, config


def protocol():
    return dict(policy_version=2, split='group', split_reason='The test holds out subjects.',
                preprocessing_inside_folds=True, augmentation_used=False, final_fit='fixed_steps',
                n_trials_total=0, sealed_budget=20, sealed_set_id='fixed-subjects-v1')


def evidence():
    ids = ['fresh-'+str(i) for i in range(10)]
    splits = ['window-'+str(i) for i in range(3)]
    trials = [dict(id=name, name=name, status='completed', tuned_score=score)
              for name, score in [('a', 70), ('b', 71), ('c', 72), ('d', 40)]]
    trials += [dict(id='pruned', name='pruned', status='pruned')]
    ev = dict(competition='competition-1', scoring_id='sealed-1', trials=trials,
              tuned_seed_ids=['tuned-seed'], tuned_split_ids=['tuned-split'],
              fresh=dict(nobody_tuned_against=True, independent_measurements=True,
                         measurement_kind='seeds', measurement_ids=ids,
                         seed_ids=['seed-'+str(i) for i in range(10)], split_ids=splits),
              baseline_unit_scores=[50]*4, units=['subject-1', 'subject-2', 'subject-3', 'subject-4'],
              baseline_measurement_ids=ids, baseline_backtest_scores=[50]*3,
              backtest_split_ids=splits,
                    backtest_splits=[dict(train_units=['initial']+['stage-'+str(j) for j in range(i)],
                                          test_units=['stage-'+str(i)]) for i in range(3)])
    finalists = [dict(name=name, sealed_scores=[score]*10, measurement_ids=ids,
                       unit_scores=[score]*4, backtest_scores=[score]*3,
                       sealed_predictions=[[score]*4]*10, backtest_predictions=[[score]*4]*3)
                  for name, score in [('a', 60), ('b', 61), ('c', 62)]]
    return dict(finalists=finalists, floor=50, n_candidates=len(trials), evidence=ev)


@pytest.fixture
def lane(tmp_path):
    lane = tmp_path/'lanes/test'
    lane.mkdir(parents=True)
    (lane/'INTAKE.toml').write_text('keywords = []\n')
    return lane


def cli(lane, *args):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(rh, 'taste_models', return_value=('fixture-taste',)), \
            patch.object(rh, 'session_model', return_value=None), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = rh.main(['--root', str(lane.parents[1])] + list(args))
    return rc, out.getvalue(), err.getvalue()


def current(lane):
    return list(rh.latest(rh.rows(lane/'IDEAS.jsonl')).values())[-1]


def add(lane):
    rc, out, err = cli(lane, 'ideas', 'add', lane.name, '--source-type', 'internal', '--claim', 'new method')
    assert rc == 0, err
    return out.strip()


def promote(lane, key, p=None, version=2):
    survey = prior_art(lane/'prior.json')
    args = ['ideas', 'promote', lane.name, key, '--board', 'board', '--test', 'paired subject score',
            '--bar', '>50', '--prior-art', str(survey), '--decided-by', 'fixture-taste', '--sealed-holdout']
    if version is not None:
        args += ['--policy-version', str(version)]
    if p is not None:
        path = lane/'protocol.json'; path.write_text(json.dumps(p))
        args += ['--selection-policy', str(path)]
    return cli(lane, *args)


def frozen(lane, p=None):
    key = add(lane)
    rc, _, err = promote(lane, key, protocol() if p is None else p)
    assert rc == 0, err
    return key


def count_trials(lane, key, data=None):
    data = data or evidence()
    for trial in data['evidence']['trials']:
        args = ['selection', 'trial', lane.name, key, '--trial-id', trial['id'],
                '--name', trial['name'], '--status', trial['status']]
        if 'tuned_score' in trial:
            args += ['--tuned-score', str(trial['tuned_score'])]
        rc, _, err = cli(lane, *args)
        assert rc == 0, err
    return data


def score(lane, key, data=None, name='decision'):
    path = lane/(name+'-inputs.json'); path.write_text(json.dumps(data or evidence()))
    return cli(lane, 'selection', 'score', lane.name, key, '--evidence', str(path),
               '--output', str(lane/(name+'.json')))


def audit(lane):
    return policy.audit(rh.rows(lane/'IDEAS.jsonl'), lane)


@pytest.mark.parametrize('field', ['split', 'split_reason', 'preprocessing_inside_folds', 'augmentation_used',
                                   'final_fit', 'n_trials_total', 'sealed_budget', 'sealed_set_id'])
def test_v2_freeze_requires_all_declarations_and_failed_command_appends_nothing(lane, field):
    key = add(lane); p = protocol(); del p[field]
    before = (lane/'IDEAS.jsonl').read_bytes()
    rc, _, err = promote(lane, key, p)
    assert rc == 2 and field.split('_')[0] in err
    assert (lane/'IDEAS.jsonl').read_bytes() == before
    assert promote(lane, key, protocol())[0] == 0
    assert not policy.frozen_error(current(lane))
    assert audit(lane) == ([], [])


def test_explicit_v2_required_and_legacy_freeze_receipt_bytes_stay_valid(lane):
    key = add(lane)
    assert promote(lane, key, None, version=None)[0] == 0
    row = current(lane)
    assert 'policy_version' not in row and 'selection_policy' not in row
    before = json.dumps(row['frozen_bar'], sort_keys=True)
    assert not policy.frozen_error(row)
    assert audit(lane) == ([], [])
    assert json.dumps(current(lane)['frozen_bar'], sort_keys=True) == before
    assert cli(lane, 'selection', 'trial', lane.name, key, '--trial-id', 'x', '--name', 'x', '--status', 'pruned')[0] == 2
    another = add(lane)
    rc, _, err = promote(lane, another, protocol(), version=1)
    assert rc == 2 and 'policy_version=2' in err
    assert promote(lane, another, None, version=2)[0] == 2


@pytest.mark.parametrize('field,value', [('split', 'time'), ('preprocessing_inside_folds', False),
                                        ('augmentation_used', True), ('final_fit', 'fold_ensemble'),
                                        ('sealed_budget', 100), ('sealed_set_id', 'other')])
def test_v2_declarations_are_hashed_and_immutable_across_history(lane, field, value):
    key = frozen(lane)
    row = deepcopy(current(lane)); row['selection_policy'][field] = value
    assert policy.frozen_error(row)
    # Rehashing/refreezing cannot erase the original protocol from history.
    if field not in ('preprocessing_inside_folds', 'augmentation_used'):
        policy.freeze(row, sealed=True)
        rh.append(lane/'IDEAS.jsonl', dict(row, revised=rh.now(), event='tampered'))
        errors, _ = audit(lane)
        assert any('original frozen bar' in e for e in errors)


def test_policy_downgrade_is_an_integrity_error(lane):
    frozen(lane); row = current(lane); row.pop('policy_version')
    rh.append(lane/'IDEAS.jsonl', dict(row, revised=rh.now(), event='tampered'))
    assert any('downgraded' in e for e in audit(lane)[0])


def test_practice_3_result_and_lint_detect_undeclared_augmentation(lane):
    key = frozen(lane)
    cfg = config(lane/'cfg.json', augmentation_used=True, augmentation=['jitter'])
    result = lane/'result.md'; result.write_text('metric=60')
    rc, _, err = cli(lane, 'ideas', 'result', lane.name, key, '--config-path', str(cfg), '--result-path', str(result))
    assert rc == 2 and 'augmentation' in err
    cfg.write_text(json.dumps(dict(seed=1, augmentation_used=False)))
    assert cli(lane, 'ideas', 'result', lane.name, key, '--config-path', str(cfg), '--result-path', str(result))[0] == 0
    assert audit(lane) == ([], [])
    row = current(lane)
    # A direct writer recomputes all config hashes. Lint still checks the frozen declaration.
    cfg.write_text(json.dumps(dict(seed=1, augmentation_used=False, augmentation=['jitter'])))
    a = row['attempt_records'][0]
    a['config_file_sha256'] = policy.file_hash(cfg)[1]
    a['config_sha256'] = policy.digest(json.loads(cfg.read_text()))
    rh.append(lane/'IDEAS.jsonl', dict(row, revised=rh.now(), event='import'))
    assert any('augmentation used without' in e for e in audit(lane)[0])


def test_declared_augmentation_can_record_a_result(lane):
    p = dict(protocol(), augmentation_used=True, augmentation_train_only=True,
             augmentation_label_preserving=True, augmentation_transforms=['label-preserving jitter'])
    key = frozen(lane, p)
    cfg = config(lane/'cfg.json', augmentation_used=True)
    result = lane/'result.md'; result.write_text('metric=60')
    assert cli(lane, 'ideas', 'result', lane.name, key, '--config-path', str(cfg), '--result-path', str(result))[0] == 0
    assert audit(lane) == ([], [])


def test_v2_configs_must_report_augmentation_boolean(lane):
    key = frozen(lane)
    cfg = config(lane/'cfg.json'); result = lane/'result.md'; result.write_text('metric=60')
    rc, _, err = cli(lane, 'ideas', 'result', lane.name, key, '--config-path', str(cfg), '--result-path', str(result))
    assert rc == 2 and 'augmentation_used boolean' in err


def test_practice_8_trial_cli_counts_pruned_and_prevents_history_replacement(lane):
    key = frozen(lane); count_trials(lane, key)
    row = current(lane)
    assert row['n_trials_total'] == 5 and row['trial_records'][-1]['status'] == 'pruned'
    assert audit(lane) == ([], [])
    assert cli(lane, 'selection', 'trial', lane.name, key, '--trial-id', 'pruned', '--name', 'x', '--status', 'pruned')[0] == 2
    row['trial_records'] = row['trial_records'][1:]; row['n_trials_total'] -= 1
    rh.append(lane/'IDEAS.jsonl', dict(row, revised=rh.now(), event='import'))
    assert any('trial history removed' in e for e in audit(lane)[0])


def test_freeze_can_include_existing_trials_and_preserves_them(lane):
    p = dict(protocol(), n_trials_total=5, trials=evidence()['evidence']['trials'])
    key = frozen(lane, p)
    assert current(lane)['n_trials_total'] == 5
    assert score(lane, key)[0] == 0
    assert audit(lane) == ([], [])


def test_cli_scores_records_optimism_and_lint_replays_without_writing(lane):
    key = frozen(lane); count_trials(lane, key)
    rc, out, err = score(lane, key)
    assert rc == 0, err
    decision = json.loads(out)
    assert decision['selected'] == 'c'
    assert current(lane)['status'] == 'testing'
    assert len(current(lane)['selection_records']) == 1
    paths = [lane/name for name in ('IDEAS.jsonl', 'SEALED_SCORING.jsonl', 'OPTIMISM.jsonl', 'decision.json')]
    before = {p: p.read_bytes() for p in paths}
    assert audit(lane) == ([], [])
    assert before == {p: p.read_bytes() for p in paths}
    rc, _, err = score(lane, key)
    assert rc == 2 and 'already exists' in err


@pytest.mark.parametrize('field,mutation', [('split', 'fresh'), ('trials', 'pruned'), ('units', 'missing')])
def test_cli_failed_checks_do_not_record_a_decision(lane, field, mutation):
    key = frozen(lane); data = count_trials(lane, key)
    if field == 'split': data['evidence']['fresh']['split_ids'][0] = 'tuned-split'
    elif field == 'trials': data['evidence']['trials'].pop()
    else: data['evidence'].pop('units')
    rc, _, _ = score(lane, key, data)
    assert rc == 2 and not current(lane).get('selection_records')
    assert not (lane/'decision.json').exists()


def test_practice_9_lint_fails_past_budget_and_scoring_does_not_change_status(lane):
    key = frozen(lane, dict(protocol(), sealed_budget=1)); data = count_trials(lane, key)
    assert score(lane, key, data)[0] == 0
    assert audit(lane) == ([], [])
    data['evidence']['scoring_id'] = 'second'
    rc, _, err = score(lane, key, data, 'second')
    assert rc == 2 and 'budget exceeded' in err
    assert any('budget exceeded' in e for e in audit(lane)[0])
    lint = rh.checked_lint(lane)
    assert any('budget exceeded' in f['message'] for f in lint['failures'])
    assert current(lane)['status'] == 'testing'


@pytest.mark.parametrize('filename', ['SEALED_SCORING.jsonl', 'OPTIMISM.jsonl'])
def test_lint_detects_truncated_or_tampered_ledgers(lane, filename):
    key = frozen(lane); count_trials(lane, key); assert score(lane, key)[0] == 0
    (lane/filename).write_text('')
    assert any('anchor missing' in e for e in audit(lane)[0])


@pytest.mark.parametrize('filename', ['decision-inputs.json', 'decision.json'])
def test_lint_binds_evidence_and_decision_bytes(lane, filename):
    key = frozen(lane); count_trials(lane, key); assert score(lane, key)[0] == 0
    p = lane/filename; data = json.loads(p.read_text()); data['extra'] = 'changed'; p.write_text(json.dumps(data))
    assert any('hash mismatch' in e for e in audit(lane)[0])


def test_lint_recomputes_decision_even_if_writer_rehashes_artifact(lane):
    key = frozen(lane); count_trials(lane, key); assert score(lane, key)[0] == 0
    p = lane/'decision.json'; data = json.loads(p.read_text()); data['selected'] = 'a'; p.write_text(json.dumps(data))
    row = current(lane); row['selection_records'][0]['decision_sha256'] = policy.file_hash(p)[1]
    # Audit this snapshot independently: full history also catches the replacement.
    errors = selection.audit([row], lane)
    assert any('decision replay mismatch' in e for e in errors)


@pytest.mark.parametrize('key,value', [('selection_min_measurements', 1), ('selection_min_measurements', True),
                                      ('selection_sealed_budget', 0), ('selection_submissions', 0)])
def test_invalid_lane_v2_limits_fail_configuration(key, value):
    with pytest.raises(ValueError, match=key): rh.validate_config({key: value})


def test_lane_limits_bind_at_freeze_and_lint(lane):
    (lane/'INTAKE.toml').write_text('selection_min_measurements = 12\nselection_sealed_budget = 4\nselection_submissions = 2\n')
    key = add(lane)
    assert promote(lane, key, protocol())[0] == 2
    p = dict(protocol(), min_measurements=12, sealed_budget=4, submissions=2)
    assert promote(lane, key, p)[0] == 0
    assert selection.audit(rh.rows(lane/'IDEAS.jsonl'), lane, rh.lane_config(lane)) == []
    cfg = dict(rh.lane_config(lane), selection_submissions=1)
    assert any('selection_submissions' in e for e in selection.audit(rh.rows(lane/'IDEAS.jsonl'), lane, cfg))


def test_ensemble_cli_adds_one_candidate_and_disallows_weights(lane):
    key = frozen(lane); data = count_trials(lane, key)
    data['simple_average_ensemble'] = True
    rc, out, err = score(lane, key, data)
    assert rc == 0, err
    assert json.loads(out)['n_finalists'] == 4
    assert audit(lane) == ([], [])
    data['weights'] = [.2, .3, .5]
    assert score(lane, key, data, 'weighted')[0] == 2


@pytest.mark.parametrize('name', ['restricted-a', 'restricted-b'])
def test_explicit_v2_applies_even_to_lanes_exempt_from_legacy_admission(tmp_path, name):
    lane = tmp_path/'lanes'/name; lane.mkdir(parents=True)
    (lane/'INTAKE.toml').write_text('keywords = []\n')
    key = frozen(lane); count_trials(lane, key)
    assert score(lane, key)[0] == 0
    assert audit(lane) == ([], [])
    row = current(lane); row['n_trials_total'] = 0
    rh.append(lane/'IDEAS.jsonl', dict(row, event='import', revised=rh.now()))
    assert any('n_trials_total' in e for e in audit(lane)[0])


def test_lint_all_preserves_legacy_status_and_includes_v2_errors(lane):
    key = frozen(lane); count_trials(lane, key)
    # The real CLI runs all normal lint checks and reports a selection defect,
    # without persisting lane statuses or modifying any source evidence.
    row = current(lane); row['n_trials_total'] = 0
    rh.append(lane/'IDEAS.jsonl', dict(row, event='import', revised=rh.now()))
    before = (lane/'IDEAS.jsonl').read_bytes()
    rc, out, err = cli(lane, 'lint', '--all', '--json')
    assert rc == 1, err
    results = json.loads(out)
    assert results[0]['status'] == 'FAIL'
    assert any('n_trials_total' in f['message'] for f in results[0]['failures'])
    assert (lane/'IDEAS.jsonl').read_bytes() == before


def test_lint_verifies_measured_prior_against_ledger_prefix(lane):
    key = frozen(lane); data = count_trials(lane, key)
    selection.contracts().record_optimism(lane/'OPTIMISM.jsonl', 'previous-competition', 'previous-score', [5]*3)
    assert score(lane, key, data)[0] == 0
    assert audit(lane) == ([], [])
    p = lane/'decision.json'; decision = json.loads(p.read_text())
    assert decision['optimism_prior'] == 5
    decision['optimism_prior'] = 0
    p.write_text(json.dumps(decision))
    row = current(lane); row['selection_records'][0]['decision_sha256'] = policy.file_hash(p)[1]
    assert any('prior differs' in e for e in selection.audit([row], lane))


def test_exempt_legacy_lane_still_checks_v2_frozen_hash(tmp_path):
    lane = tmp_path/'lanes/restricted-a'; lane.mkdir(parents=True)
    (lane/'INTAKE.toml').write_text('keywords = []\n')
    frozen(lane)
    row = current(lane); row['bar'] = '>0'
    rh.append(lane/'IDEAS.jsonl', dict(row, event='tampered', revised=rh.now()))
    assert any('frozen bar changed' in e for e in audit(lane)[0])


def test_v2_shipped_requires_a_verified_accepted_decision(lane):
    key = frozen(lane)
    row = current(lane); row['status'] = 'shipped'
    assert any('accepted selection decision' in e for e in selection.audit([row], lane))
    data = count_trials(lane, key)
    assert score(lane, key, data)[0] == 0
    row = current(lane); row['status'] = 'shipped'
    assert selection.audit([row], lane) == []
    for candidate in data['finalists']: candidate['unit_scores'] = [40]*4
    data['evidence']['scoring_id'] = 'rejected-score'
    assert score(lane, key, data, 'rejected')[0] == 0
    row = current(lane); row['status'] = 'shipped'
    assert any('accepted selection decision' in e for e in selection.audit([row], lane))


@pytest.mark.parametrize('data', [[], {'evidence': []}, {'evidence': None}])
def test_malformed_v2_evidence_is_a_cli_error_without_a_traceback(lane, data):
    key = frozen(lane)
    path = lane/'bad-input.json'; path.write_text(json.dumps(data))
    rc, _, err = cli(lane, 'selection', 'score', lane.name, key, '--evidence', str(path),
                     '--output', str(lane/'decision.json'))
    assert rc == 2 and 'evidence object' in err
    assert not (lane/'decision.json').exists()


@pytest.mark.parametrize('field,value', [('competition', 'forged'), ('scoring_ledger_sha256', '0'*64),
                                        ('optimism_ledger_sha256', '0'*64)])
def test_lint_replays_all_decision_metadata(lane, field, value):
    key = frozen(lane); count_trials(lane, key); assert score(lane, key)[0] == 0
    p = lane/'decision.json'; data = json.loads(p.read_text()); data[field] = value; p.write_text(json.dumps(data))
    row = current(lane); row['selection_records'][0]['decision_sha256'] = policy.file_hash(p)[1]
    assert any('decision replay mismatch' in e for e in selection.audit([row], lane))
