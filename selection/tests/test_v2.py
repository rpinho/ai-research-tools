"""Behavioral acceptance for all ten prospective anti-overfitting practices."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from selection_gate import (decide, bootstrap_diff, backtests, backtest_summary,
                            simple_average_ensemble, write_receipt, verify_receipt,
                            paired_diff, forecast_diversity, t95)
from selection_gate.policy import (validate_policy, read_ledger, scoring_ledger_error,
                                   optimism_prior, record_optimism)


def protocol():
    return dict(policy_version=2, split='group', split_reason='The test holds out subjects.',
                preprocessing_inside_folds=True, augmentation_used=False, final_fit='fixed_steps',
                n_trials_total=0, sealed_budget=20, sealed_set_id='fixed-subjects-v1')


def inputs():
    ids = ['fresh-'+str(i) for i in range(10)]
    splits = ['window-'+str(i) for i in range(3)]
    trials = [dict(id=name, name=name, status='completed', tuned_score=score)
              for name, score in [('a', 70), ('b', 71), ('c', 72), ('d', 40)]]
    trials += [dict(id='pruned', name='pruned', status='pruned')]
    evidence = dict(competition='competition-1', scoring_id='sealed-1', trials=trials,
                    tuned_seed_ids=['tuned-seed'], tuned_split_ids=['tuned-split'],
                    fresh=dict(nobody_tuned_against=True, independent_measurements=True,
                               measurement_kind='seeds', measurement_ids=ids,
                               seed_ids=['seed-'+str(i) for i in range(10)], split_ids=splits),
                    baseline_unit_scores=[50]*4, units=['subject-1', 'subject-2', 'subject-3', 'subject-4'],
                    baseline_measurement_ids=ids, baseline_backtest_scores=[50]*3,
                    backtest_split_ids=splits,
                    backtest_splits=[dict(train_units=['initial']+['stage-'+str(j) for j in range(i)],
                                          test_units=['stage-'+str(i)]) for i in range(3)])
    finalists = [dict(name=name, sealed_scores=[score-.1, score+.1]*5, measurement_ids=ids,
                       unit_scores=[score]*4, backtest_scores=[score]*3,
                       sealed_predictions=[[v]*4 for v in [score-.1, score+.1]*5],
                       backtest_predictions=[[score]*4]*3)
                  for name, score in [('a', 60), ('b', 61), ('c', 62)]]
    return dict(finalists=finalists, floor=50, n_candidates=len(trials), evidence=evidence)


def run(tmp_path, data=None, policy=None, **kwargs):
    return decide(**(data or inputs()), policy_version=2, policy=policy or protocol(),
                  ledger_path=tmp_path/'SEALED_SCORING.jsonl', optimism_path=tmp_path/'OPTIMISM.jsonl', **kwargs)


def test_v2_all_checks_pass_and_receipt_stays_verifiable(tmp_path):
    d = run(tmp_path)
    assert d['accepted'] and d['selected'] == 'c'
    assert d['n_trials_total'] == 5 and d['n_finalists'] == 3
    assert all(r['bootstrap']['ci_low'] > 0 and r['backtests']['passed'] for r in d['finalists'])
    assert d['optimism_estimate'] == pytest.approx(10)
    file = tmp_path/'artifact'; file.write_bytes(b'predictions')
    import hashlib
    receipt = tmp_path/'receipt.json'
    write_receipt(receipt, d, hashlib.sha256(file.read_bytes()).hexdigest(), 'run', 'board')
    assert verify_receipt(receipt, file)['decision'] == d


@pytest.mark.parametrize('field', ['split', 'split_reason', 'preprocessing_inside_folds'])
def test_practice_1_split_and_fold_preprocessing_declarations(tmp_path, field):
    p = protocol(); del p[field]
    with pytest.raises(ValueError, match='split|preprocessing'):
        run(tmp_path, policy=p)
    assert run(tmp_path, policy=protocol())['accepted']


def test_practice_1_random_requires_shared_units_statement(tmp_path):
    p = dict(protocol(), split='random')
    with pytest.raises(ValueError, match='test_shares_units_statement'):
        run(tmp_path, policy=p)
    p['test_shares_units'] = True
    p['test_shares_units_statement'] = 'Test and training sample repeated observations from the same subjects.'
    assert run(tmp_path, policy=p)['accepted']
    p['preprocessing_inside_folds'] = False
    with pytest.raises(ValueError, match='preprocessing'):
        validate_policy(p)


def test_practice_2_ten_independent_measurements_and_explicit_waiver(tmp_path):
    data = inputs()
    for c in data['finalists']:
        c['sealed_scores'] = c['sealed_scores'][:2]
        c['measurement_ids'] = c['measurement_ids'][:2]
    fresh = data['evidence']['fresh']
    fresh['measurement_ids'] = fresh['measurement_ids'][:2]
    fresh['seed_ids'] = fresh['seed_ids'][:2]
    data['evidence']['baseline_measurement_ids'] = fresh['measurement_ids']
    with pytest.raises(ValueError, match='at least 10'):
        run(tmp_path/'fail', data)
    p = dict(protocol(), min_measurements=2)
    with pytest.raises(ValueError, match='waiver'):
        run(tmp_path/'fail2', data, p)
    p['measurement_waiver'] = dict(reason='This lane has a measured two-run compute ceiling.')
    d = run(tmp_path/'pass', data, p)
    assert d['measurement_waiver'] == p['measurement_waiver']
    # A two-measurement waiver has one degree of freedom, where 1.96 is not close: the t
    # multiplier is 12.706, so the margin is 6.4 times the old 2 SEM. This is the case
    # Hugo's 11-07 update is about, and the lane that waives its way down to two
    # measurements is the one that most needs the wider interval.
    row = d['finalists'][0]
    assert row['margin_quantile'] == 12.706
    assert row['margin'] == pytest.approx(12.706*row['sem'])
    assert row['margin'] > 6*2*row['sem']
    assert d['interval'] == 't95'
    fresh['seed_ids'] = ['same', 'same']
    with pytest.raises(ValueError, match='unique'):
        run(tmp_path/'duplicates', data, p)


@pytest.mark.parametrize('field', ['augmentation_train_only', 'augmentation_label_preserving', 'augmentation_transforms'])
def test_practice_3_augmentation_is_training_only_and_label_preserving(tmp_path, field):
    p = dict(protocol(), augmentation_used=True, augmentation_train_only=True,
             augmentation_label_preserving=True, augmentation_transforms=['within-subject jitter'])
    missing = deepcopy(p); del missing[field]
    with pytest.raises(ValueError, match='augmentation'):
        run(tmp_path, policy=missing)
    assert run(tmp_path, policy=p)['accepted']


def test_practice_4_final_fit_requires_non_harmful_retrain_backtest(tmp_path):
    p = protocol(); del p['final_fit']
    with pytest.raises(ValueError, match='final_fit'):
        run(tmp_path, policy=p)
    p['final_fit'] = 'retrain_epochs'
    with pytest.raises(ValueError, match='retrain_backtest'):
        run(tmp_path, policy=p)
    p['retrain_backtest'] = dict(fold_scores=[60, 61], retrain_scores=[60, 60])
    with pytest.raises(ValueError, match='hurt'):
        run(tmp_path, policy=p)
    p['retrain_backtest']['retrain_scores'] = [60, 62]
    assert run(tmp_path, policy=p)['accepted']


def test_practice_5_simple_average_scores_predictions_and_counts_toward_k(tmp_path):
    d = run(tmp_path, ensemble_scorer=lambda p: p)
    assert d['n_finalists'] == 4
    row = d['finalists'][-1]
    assert row['ensemble_method'] == 'simple_average'
    assert row['mean'] == pytest.approx(61)
    assert row['optimism'] == pytest.approx(row['sd']*math.sqrt(2*math.log(4)))
    data = inputs(); data['finalists'][0]['ensemble_weights'] = [.5, .2, .3]
    with pytest.raises(ValueError, match='weights'):
        run(tmp_path/'weighted', data, ensemble_scorer=lambda p: p)
    # Nonlinear metrics: score(mean predictions) differs from mean(scores).
    candidates = [dict(name=str(i), measurement_ids=['s1', 's2'],
                       sealed_predictions=[[v, v]]*2, backtest_predictions=[[v, v]]*2)
                  for i, v in enumerate([-1, 1])]
    ensemble = simple_average_ensemble(candidates, lambda p: -np.square(p))
    assert ensemble['sealed_scores'] == [0, 0]
    candidates[1]['sealed_predictions'] = [[1, 1]]
    with pytest.raises(ValueError, match='aligned|shapes'):
        simple_average_ensemble(candidates, lambda p: p)


def test_practice_6_expanding_backtests_use_disjoint_validation_and_report_worst(tmp_path):
    calls = []
    def score(candidate, train, test):
        calls.append((candidate, train[:], test[:]))
        assert set(train).isdisjoint(test)
        return 50 + (2 if candidate == 'challenger' else 0)
    result = backtests(dict(base='baseline', c='challenger'), [['a'], ['b'], ['c'], ['d']],
                       score, baseline='base', margin=1)
    assert result['scores']['c'] == [52]*3
    assert result['splits'][-1] == dict(train_units=['a', 'b', 'c'], test_units=['d'])
    assert result['summaries']['c']['passed']
    assert result['summaries']['c']['worst_difference'] == 2
    data = inputs()
    for candidate in data['finalists']:
        candidate['backtest_scores'] = [51, 49, 49]
    d = run(tmp_path/'fail', data)
    assert not d['accepted']
    assert d['finalists'][0]['backtests']['worst_split'] == 1
    for candidate in data['finalists']:
        candidate['backtest_scores'] = [51, 51, 49]
    assert run(tmp_path/'pass', data)['accepted']
    assert not backtest_summary([51, 50], [50, 50])['passed'] # strict majority
    assert not backtest_summary([51, 51], [50, 50], margin=1)['passed'] # strict margin
    with pytest.raises(ValueError, match='disjoint'):
        backtests(dict(base=0, c=1), [['a'], ['a'], ['b']], score, baseline='base')


def test_practice_6_backtests_can_supply_the_independent_measurements(tmp_path):
    data = inputs(); fresh = data['evidence']['fresh']
    fresh['measurement_kind'] = 'backtests'
    fresh['split_ids'] = fresh['measurement_ids'][:]
    fresh['seed_ids'] = ['one-fixed-seed']
    data['evidence']['baseline_backtest_scores'] = [50]*10
    data['evidence']['backtest_split_ids'] = fresh['measurement_ids'][:]
    data['evidence']['backtest_splits'] = [dict(train_units=['initial']+['stage-'+str(j) for j in range(i)],
        test_units=['stage-'+str(i)]) for i in range(10)]
    for c in data['finalists']:
        c['backtest_scores'] = c['sealed_scores'][:]
    assert run(tmp_path/'pass', data)['accepted']
    data['finalists'][0]['backtest_scores'][0] += 1
    with pytest.raises(ValueError, match='per-split'):
        run(tmp_path/'fail', data)


def test_practice_7_bootstrap_resamples_independent_units_never_rows():
    # One subject has 100 rows. Its row count must not dominate either the
    # estimate or the CI: resampling yields the same answer as 1 row per unit.
    ci = bootstrap_diff([10]*100+[-2], [0]*101, ['subject-a']*100+['subject-b'])
    compressed = bootstrap_diff([10, -2], [0, 0], ['subject-a', 'subject-b'])
    assert ci == compressed
    assert ci['difference'] == 4 and ci['ci_low'] < 0 < ci['ci_high']
    with pytest.raises(ValueError, match='two independent'):
        bootstrap_diff([1, 1], [0, 0], ['same', 'same'])


def test_practice_7_both_ci_and_lower_bound_are_required(tmp_path):
    data = inputs()
    for c in data['finalists']:
        c['unit_scores'] = [55, 45, 55, 45]
    d = run(tmp_path/'ci-fails', data)
    assert not d['accepted'] and all(r['lower_bound'] > 50 for r in d['finalists'])
    for c in data['finalists']:
        c['unit_scores'] = [55]*4
        c['sealed_scores'] = [49, 53]*5
    d = run(tmp_path/'bound-fails', data)
    assert not d['accepted'] and all(r['bootstrap']['ci_low'] > 0 for r in d['finalists'])
    assert run(tmp_path/'both-pass')['accepted']


def test_practice_8_count_pruned_trials_and_penalize_expected_score(tmp_path):
    data = inputs(); data['n_candidates'] -= 1
    with pytest.raises(ValueError, match='pruned'):
        run(tmp_path/'fail', data)
    d = run(tmp_path/'pass')
    assert d['expected'] == pytest.approx(62-d['search_optimism'])
    assert d['expected'] < 62
    assert read_ledger(tmp_path/'pass/OPTIMISM.jsonl')[0]['drop'] == pytest.approx(10)
    next_data = inputs(); next_data['evidence'].update(competition='next', scoring_id='sealed-2')
    next_decision = run(tmp_path/'pass', next_data)
    assert next_decision['optimism_prior'] == pytest.approx(10)
    assert next_decision['expected'] == pytest.approx(52)
    assert optimism_prior(tmp_path/'pass/OPTIMISM.jsonl', 'competition-1') == pytest.approx(10)


@pytest.mark.parametrize('mutation', ['too_few', 'not_best', 'reused_seed', 'reused_split', 'tuned_on_fresh', 'no_independence'])
def test_practice_8_best_three_to_five_rerun_on_untouched_seeds_and_splits(tmp_path, mutation):
    data = inputs()
    if mutation == 'too_few': data['finalists'] = data['finalists'][:2]
    elif mutation == 'not_best': data['evidence']['trials'][3]['tuned_score'] = 100
    elif mutation == 'reused_seed': data['evidence']['fresh']['seed_ids'][0] = 'tuned-seed'
    elif mutation == 'reused_split': data['evidence']['fresh']['split_ids'][0] = 'tuned-split'
    elif mutation == 'tuned_on_fresh': data['evidence']['fresh']['nobody_tuned_against'] = False
    elif mutation == 'no_independence': data['evidence']['fresh']['independent_measurements'] = False
    with pytest.raises(ValueError): run(tmp_path/'fail', data)
    assert run(tmp_path/'pass')['accepted']


def test_practice_9_every_scoring_is_ledgered_budget_and_fixed_set_enforced(tmp_path):
    p = dict(protocol(), sealed_budget=1)
    assert run(tmp_path, policy=p)['accepted']
    with pytest.raises(ValueError, match='already consumed'):
        run(tmp_path, policy=p)
    data = inputs(); data['evidence']['scoring_id'] = 'second'
    with pytest.raises(ValueError, match='budget exceeded'):
        run(tmp_path, data, p)
    ledger = tmp_path/'SEALED_SCORING.jsonl'
    assert len(read_ledger(ledger)) == 2 # over-budget attempt remains visible to lint
    assert 'budget exceeded' in scoring_ledger_error(ledger, 1)
    other = tmp_path/'other'
    run(other)
    data['evidence']['scoring_id'] = 'changed-set'
    with pytest.raises(ValueError, match='hidden set changed'):
        run(other, data, dict(protocol(), sealed_set_id='different-hidden-set'))
    # A failed bootstrap acceptance is still a consumed scoring.
    for c in data['finalists']: c['unit_scores'] = [40]*4
    d = run(tmp_path/'rejected', data)
    assert not d['accepted'] and len(read_ledger(tmp_path/'rejected/SEALED_SCORING.jsonl')) == 1


def test_practice_10_submissions_rank_by_mean_minus_t_sem(tmp_path):
    data = inputs()
    data['finalists'][2]['sealed_scores'] = [59, 65]*5  # highest mean, highest uncertainty
    d = run(tmp_path, data, dict(protocol(), submissions=2))
    eligible = sorted((r for r in d['finalists'] if r['accepted']), key=lambda r: -r['submission_score'])
    assert d['selected_submissions'] == [r['name'] for r in eligible[:2]]
    # With 1.96 this was ['b', 'c']: the noisiest candidate kept the second slot. On ten
    # measurements the multiplier is 2.262, which costs c more than its higher mean gains,
    # and the steadier 'a' takes the slot. The ranking is the only thing that changed; c is
    # still the highest mean and still not the one submitted first.
    assert d['selected_submissions'] == ['b', 'a']
    assert d['selected'] == 'b' and d['best'] == 'c'
    assert all(r['margin_quantile'] == 2.262 for r in d['finalists'])
    with pytest.raises(ValueError, match='submissions'):
        validate_policy(dict(protocol(), submissions=0))
    assert len(run(tmp_path/'default')['selected_submissions']) == 1


def test_v2_is_explicit_and_legacy_result_shape_unchanged():
    old = decide([dict(name='a', sealed_scores=[60, 62])], 50, 100)
    assert 'policy_version' not in old and 'expected' not in old
    assert decide([dict(name='a', sealed_scores=[60, 62])], 50, 100, policy_version=1) == old
    with pytest.raises(ValueError, match='explicit'):
        decide([], 0, 1, policy=protocol())
    with pytest.raises(ValueError, match='unsupported'):
        decide([], 0, 1, policy_version=True)


@pytest.mark.parametrize('scores,expected', [([60, 60], 'candidate'), ([50, 50], 'floor'), ([49, 51], 'floor')])
def test_synthetic_legacy_decisions_are_stable(scores, expected):
    result = decide([dict(name='candidate', sealed_scores=scores)], 50, 100)
    assert result['selected'] == expected
    assert decide([dict(name='candidate', sealed_scores=scores)], 50, 100, policy_version=1) == result


@pytest.mark.parametrize('mutation', ['overlap', 'shrink', 'no_manifest', 'wrong_length'])
def test_expanding_window_manifest_is_checked_in_v2(tmp_path, mutation):
    data = inputs(); splits = data['evidence']['backtest_splits']
    if mutation == 'overlap': splits[1]['test_units'] = splits[0]['test_units'][:]
    elif mutation == 'shrink': splits[1]['train_units'] = ['initial']
    elif mutation == 'no_manifest': data['evidence'].pop('backtest_splits')
    else: splits.pop()
    with pytest.raises(ValueError, match='backtest'):
        run(tmp_path/'fail', data)
    assert run(tmp_path/'pass')['accepted']


def test_pre_scoring_accounting_survives_scorer_failure_and_denies_overspend(tmp_path):
    from selection_gate import score_and_decide
    calls = []
    def broken():
        calls.append('called')
        raise RuntimeError('sealed scorer failed')
    data, p = inputs(), dict(protocol(), sealed_budget=1)
    kwargs = dict(policy=p, evidence=data['evidence'], ledger_path=tmp_path/'sealed.jsonl',
                  optimism_path=tmp_path/'OPTIMISM.jsonl')
    with pytest.raises(RuntimeError, match='scorer failed'):
        score_and_decide(broken, 50, data['n_candidates'], **kwargs)
    assert calls == ['called'] and len(read_ledger(kwargs['ledger_path'])) == 1
    data['evidence']['scoring_id'] = 'second'
    with pytest.raises(ValueError, match='budget exceeded'):
        score_and_decide(broken, 50, data['n_candidates'], **kwargs)
    assert calls == ['called']  # budget check happens before accessing hidden targets
    data['evidence']['scoring_id'] = 'third'
    kwargs['ledger_path'] = tmp_path/'fresh-ledger.jsonl'
    d = score_and_decide(lambda: data['finalists'], 50, data['n_candidates'], **kwargs)
    assert d['accepted']


def test_gate_cannot_raise_a_lane_budget_between_calls(tmp_path):
    run(tmp_path, policy=dict(protocol(), sealed_budget=1))
    data = inputs(); data['evidence']['scoring_id'] = 'second'
    with pytest.raises(ValueError, match='budget changed'):
        run(tmp_path, data, dict(protocol(), sealed_budget=100))


def test_nonlinear_ensemble_scorer_and_variable_backtest_sizes():
    candidates = [dict(name=str(i), measurement_ids=['a', 'b'],
                       sealed_predictions=[[v, v]]*2,
                       backtest_predictions=[[v], [v, v, v]]) for i, v in enumerate((-1, 1))]
    def metric(prediction):
        return dict(score=-float(np.square(prediction).sum()), unit_scores=-np.square(prediction))
    result = simple_average_ensemble(candidates, metric)
    assert result['sealed_scores'] == [0, 0] and result['backtest_scores'] == [0, 0]
    assert result['unit_scores'] == [0, 0]


def test_prior_is_equal_weight_per_previous_competition_and_time_bounded(tmp_path):
    p = tmp_path/'OPTIMISM.jsonl'
    record_optimism(p, 'old-a', 'a1', [2, 2, 2])
    record_optimism(p, 'old-a', 'a2', [4, 4, 4])
    first_b = record_optimism(p, 'old-b', 'b1', [9, 9, 9])
    assert optimism_prior(p, 'new') == 6  # mean(3, 9), not mean(2, 4, 9)
    assert optimism_prior(p, 'old-b', before_sha256=first_b['sha256']) == 3
    record_optimism(p, 'future', 'f1', [100, 100, 100])
    assert optimism_prior(p, 'old-b', before_sha256=first_b['sha256']) == 3


def test_numpy_unit_ids_are_bootstrapped_as_units():
    result = bootstrap_diff(np.array([1., 2., 3.]), np.zeros(3), np.array([1, 1, 2]))
    assert result['n_units'] == 2 and result['difference'] == 2.25
    assert bootstrap_diff([1, 2], [0, 0], np.array(['a', 'b']))['n_units'] == 2
    with pytest.raises(ValueError, match='paired'):
        bootstrap_diff([1, 2], [0, 0], None)


def test_prior_snapshot_remains_valid_during_concurrent_competitions(tmp_path):
    from selection_gate.policy import optimism_snapshot
    path = tmp_path/'OPTIMISM.jsonl'
    prior, head = optimism_snapshot(path, 'current')
    assert prior == 0 and head == ''
    # Another scorer finishes while this scorer is doing its numerical work.
    record_optimism(path, 'concurrent', 'other', [20]*3)
    saved = record_optimism(path, 'current', 'current', [10]*3, prior_sha256=head)
    assert optimism_prior(path, 'current', through_sha256=saved['prior_sha256']) == prior
    assert optimism_prior(path, 'current') == 20


# Hugo Fernandes' two updates of 2026-11-07 to his own ten practices.

def test_t_quantile_replaces_196_when_measurements_are_few():
    assert t95(2) == 12.706 and t95(3) == 4.303 and t95(10) == 2.262
    assert t95(11) == 2.228 and t95(31) == 2.042
    assert t95(36) == pytest.approx(2.0315, abs=1e-3)   # interpolated between df 30 and 40
    assert t95(400) == 1.96                             # the normal case, within 1%
    assert all(t95(n) >= 1.96 for n in range(2, 500))   # never narrower than the old gate
    with pytest.raises(ValueError):
        t95(1)


def test_paired_comparison_on_matched_seeds_is_narrower():
    """Hugo measured 3 to 10 times narrower. A shared per-seed effect is why: here the two
    candidates move together seed by seed, and the difference is steady even though each
    one's own scores are not."""
    shared = np.array([0., 9., -6., 4., -7., 11., -3., 2., 5., -8.])
    jitter = np.array([.02, -.01, .03, 0., -.02, .01, .02, -.03, .01, -.01])
    candidate = 61 + shared + jitter
    reference = 60 + shared
    paired = paired_diff(candidate, reference)
    assert paired['difference'] == pytest.approx(1, abs=.01)
    assert paired['sd'] < .03
    assert paired['ci_low'] > 0 and paired['ci_high'] > 0
    assert paired['quantile'] == 2.262
    # Unpaired, the same evidence cannot separate them at all.
    unpaired_low = (candidate.mean()-reference.mean()
                    - 2.262*math.sqrt(candidate.var(ddof=1)/10 + reference.var(ddof=1)/10))
    assert unpaired_low < 0
    assert paired['narrowing'] > 10
    with pytest.raises(ValueError, match='same measurements'):
        paired_diff([1, 2, 3], [1, 2])


def test_paired_baseline_scores_become_an_acceptance_condition(tmp_path):
    data = inputs()
    ids = data['evidence']['fresh']['measurement_ids']
    # A baseline that beats every candidate on the matched seeds, while its mean is lower.
    data['evidence']['baseline_sealed_scores'] = [99]*len(ids)
    d = run(tmp_path/'loses', data)
    assert not d['accepted']
    assert all(r['paired_baseline']['ci_high'] < 0 for r in d['finalists'])
    # Supplying nothing keeps the old behaviour, and says so rather than implying a pass.
    plain = run(tmp_path/'absent')
    assert plain['accepted'] and all(r['paired_baseline'] is None for r in plain['finalists'])
    data['evidence']['baseline_sealed_scores'] = [50]*len(ids)
    good = run(tmp_path/'beats', data)
    assert good['accepted'] and good['finalists'][0]['paired_baseline']['ci_low'] > 0
    data['evidence']['baseline_sealed_scores'] = [50]*3
    with pytest.raises(ValueError, match='baseline sealed scores'):
        run(tmp_path/'ragged', data)


def test_the_ranking_against_the_leader_is_reported_on_matched_seeds(tmp_path):
    d = run(tmp_path)
    leader = d['best']
    rows = {r['name']: r for r in d['finalists']}
    assert rows[leader]['paired_vs_best'] is None
    for name, row in rows.items():
        if name != leader:
            assert row['paired_vs_best']['n_measurements'] == row['n_measurements']
            assert row['paired_vs_best']['difference'] < 0
    # c beats b by 1 on every one of the ten matched seeds, so the ranking is real.
    assert d['best_separated_from_runner_up'] is True


def test_a_ranking_too_close_to_call_is_reported_not_refused(tmp_path):
    data = inputs()
    # b and c differ by a hair, with the per-seed noise far bigger than the gap.
    data['finalists'][1]['sealed_scores'] = [55, 67]*5
    data['finalists'][2]['sealed_scores'] = [56, 66]*5
    d = run(tmp_path, data)
    assert d['accepted'], 'an unseparated ranking is still a result'
    assert d['best_separated_from_runner_up'] is False


def test_diversity_comes_from_the_forecasts_not_the_labels():
    members = {'a': [1., 2., 3., 4.], 'b': [1., 2., 3., 4.], 'c': [4., 3., 2., 1.]}
    identical = forecast_diversity({'a': members['a'], 'b': members['b']})
    assert identical['disagreement'] == 0 and identical['error_correlation'] is None
    assert identical['source'] == 'forecasts'
    different = forecast_diversity({'a': members['a'], 'c': members['c']})
    assert different['disagreement'] > 0
    # Errors against the truth are what predicts ensemble gain: these two are anticorrelated.
    labelled = forecast_diversity({'a': members['a'], 'c': members['c']}, labels=[2.5]*4)
    assert labelled['error_correlation'] == pytest.approx(-1)
    assert labelled['error_correlation_pairs'] == 1
    # No labels needed at all, which is the point: it works on unlabeled data.
    with pytest.raises(ValueError, match='two members'):
        forecast_diversity({'a': members['a']})
    with pytest.raises(ValueError, match='match the forecast items'):
        forecast_diversity({'a': members['a'], 'c': members['c']}, labels=[1, 2])


def test_the_ensemble_reports_measured_diversity_and_equal_weights(tmp_path):
    d = run(tmp_path, ensemble_scorer=lambda p: p)
    row = d['finalists'][-1]
    assert row['ensemble_method'] == 'simple_average'
    assert row['weights_fixed_before_scores'] is True
    assert row['diversity']['members'] == ['a', 'b', 'c']
    assert row['diversity']['source'] == 'forecasts'
    assert row['diversity']['disagreement'] > 0


def test_diversity_cannot_be_declared_or_argued_from_subgroups(tmp_path):
    for key in ('diversity', 'subgroup_scores', 'diversity_by_subgroup'):
        data = inputs()
        data['evidence'][key] = {'group-1': 0.9, 'group-2': 0.4}
        with pytest.raises(ValueError, match='subgroup performance|measured from the member'):
            run(tmp_path/('declared-'+key), data)
