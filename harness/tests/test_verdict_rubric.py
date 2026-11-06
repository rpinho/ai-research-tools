"""A referee pass only counts when the verdict says what it checked.

From OpenAI's BioBasicsBench failure modes (11-06): the failures that matter are an assumption
outranking the controls, contradicting data being dropped, and a label believed because it was in
the file. Our own referee passed 98% of 528 claims and none of it was worth sending.

The thing these tests mostly protect is the unsupported "no". A referee that answers all three
questions with "no" has not done the work, and that is the easiest possible way to satisfy a
rubric, so it has to fail.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verdict_rubric', ROOT / 'verdict_rubric.py')
vr = importlib.util.module_from_spec(spec); spec.loader.exec_module(vr)

GOOD = ('CONTROLS: the scrambled-input control in runs/ctrl.json scores 0.51, so the signal is '
        'not the assumption. CONTRADICTING: nothing dropped, all 12 rows of data/all.csv are in '
        'the fit. LABELS: re-derived the stage labels from raw/atlas.tsv, 12 of 12 agree.')


def fails(text, referee='HOLDS'):
    return vr.findings(text, referee)[0]


def test_a_complete_verdict_passes():
    assert fails(GOOD) == []


def test_all_three_on_one_line_is_fine():
    """Referees write them inline as often as on separate lines."""
    assert len(vr.answers(GOOD)) == 3


def test_separate_lines_are_fine_too():
    text = ('HOLDS.\n'
            'CONTROLS: ctrl.json, 3 of 3 negative controls agree.\n'
            'CONTRADICTING: kept all 40 points; see fig/all.png.\n'
            'LABELS: checked 8 labels against raw/source.csv, all 8 match.\n')
    assert fails(text) == []


def test_a_missing_question_fails_and_names_it():
    text = 'CONTROLS: ctrl.json agrees. CONTRADICTING: nothing dropped, 40 of 40 kept.'
    out = fails(text)
    assert len(out) == 1 and 'LABELS' in out[0]


def test_an_unsupported_no_fails_every_question():
    """The whole point. "no" three times is the cheapest way to satisfy a rubric."""
    out = fails('CONTROLS: no. CONTRADICTING: no. LABELS: no.')
    assert len(out) == 3
    assert all('UNSUPPORTED' in o for o in out)


def test_hedging_without_evidence_still_fails():
    assert fails('CONTROLS: nothing. CONTRADICTING: none. LABELS: checked.')


@pytest.mark.parametrize('referee', ['NONE', 'FAILS', 'REJECT', '', 'n/a (credit wording)'])
def test_only_a_passing_verdict_is_held_to_the_rubric(referee):
    """A lane logging NONE is not claiming anything, so its ordinary logging must not change."""
    assert fails('', referee) == []


@pytest.mark.parametrize('referee', ['HOLDS', 'holds with caveats', 'ACCEPT v3', 'PASS'])
def test_every_passing_form_is_held_to_it(referee):
    assert fails('', referee)
    assert fails(GOOD, referee) == []


def test_the_template_names_all_three_and_does_not_pass_unfilled():
    """My first version of this test asserted the opposite, which was the wrong instinct: an
    unfilled template must FAIL, or pasting the block becomes the way to satisfy the rubric."""
    t = vr.template()
    assert all(name in t for name, _ in vr.CHECKS)
    assert len(fails(t)) == 3, 'a pasted, unfilled template answers nothing'


def test_a_bare_filename_is_evidence():
    """Rejected at first for having no slash, which would have failed the answer a referee
    most naturally writes."""
    assert fails('CONTROLS: ctrl.json agrees. CONTRADICTING: all rows kept, see all.csv. '
                 'LABELS: re-derived from labels.tsv and they match.') == []
