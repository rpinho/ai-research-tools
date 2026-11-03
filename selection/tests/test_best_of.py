import math

import pytest

from selection_gate.v2 import best_of_choice


def test_no_downside_prefers_the_higher_upside():
    # A steady candidate just under the current best gains little; a noisier one with the same mean gains more.
    steady = dict(name='steady', sealed_scores=[0.49, 0.50, 0.51])
    noisy = dict(name='noisy', sealed_scores=[0.40, 0.50, 0.60])
    out = best_of_choice([steady, noisy], current_best=0.55, n_finalists=1)
    assert out['selected'] == 'noisy'
    assert all(r['expected_gain'] >= 0 for r in out['finalists'])


def test_matches_closed_form_for_one_finalist():
    out = best_of_choice([dict(name='a', sealed_scores=[0.0, 2.0])], current_best=1.0, n_finalists=1)
    row = out['finalists'][0]
    sd = math.sqrt(2.0)
    assert row['expected_gain'] == pytest.approx(sd / math.sqrt(2 * math.pi))  # z = 0


def test_optimism_shrinks_the_mean_with_more_finalists():
    a = dict(name='a', sealed_scores=[0.5, 0.7])
    one = best_of_choice([a], current_best=0.6, n_finalists=1)['finalists'][0]
    three = best_of_choice([a], current_best=0.6, n_finalists=3)['finalists'][0]
    assert three['shrunk_mean'] < one['shrunk_mean'] and three['expected_gain'] < one['expected_gain']


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        best_of_choice([dict(name='a', sealed_scores=[1.0])], current_best=0.0)
    with pytest.raises(ValueError):
        best_of_choice([], current_best=float('nan'))


def test_current_from_sealed_panel_and_exactly_one_source():
    a = dict(name='a', sealed_scores=[0.5, 0.7])
    by_panel = best_of_choice([a], current_sealed_scores=[0.55, 0.65], n_finalists=1)
    by_value = best_of_choice([a], current_best=0.6, n_finalists=1)
    assert by_panel['current_best'] == pytest.approx(0.6)
    assert by_panel['finalists'][0]['expected_gain'] == pytest.approx(by_value['finalists'][0]['expected_gain'])
    with pytest.raises(ValueError):
        best_of_choice([a], current_best=0.6, current_sealed_scores=[0.6])
    with pytest.raises(ValueError):
        best_of_choice([a])
