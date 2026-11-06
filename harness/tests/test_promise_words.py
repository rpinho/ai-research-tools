"""Don't sell an unrefereed result.

The corrections ledger's first row (2026-11-02) is a friends email that called unrefereed lane
results "most promising". By 11-06 the rule had been broken five times and was still prose.

The two things these tests are really protecting: the check must fire on the actual 11-02 wording,
and it must NOT fire on the honest prose we send constantly, because a lint that cries wolf on
ordinary sentences gets worked around within a day.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('promise_words', ROOT / 'promise_words.py')
pw = importlib.util.module_from_spec(spec); spec.loader.exec_module(pw)


def fails(text, **kw):
    return pw.findings(text, **kw)[0]


def test_the_1102_wording_fails():
    assert fails('These are the most promising results from the lane so far.')


def test_naming_the_verdict_clears_it():
    text = ('These are the most promising results so far. The referee verdict on both is HOLDS '
            'WITH CAVEATS.')
    f, w = pw.findings(text)
    assert f == [] and w, 'a stated verdict clears the check and leaves a warning'


def test_saying_it_is_unrefereed_clears_it():
    assert fails('This is the best result of them. Nothing has refereed it.') == []
    assert fails('O melhor resultado que temos. Ainda não foi revisto por ninguém.') == []


def test_a_ledger_verdict_clears_it_without_prose():
    assert fails('The most promising one is the second.', has_verdict=True) == []


def test_breakthrough_and_we_proved_fail():
    assert fails('This is a breakthrough for the field.')
    assert fails('We proved the bound is tight.')
    assert fails('Provámos que o limite é exacto.')


def test_honest_prose_does_not_fail():
    """Every one of these is a sentence we actually send. None may trip the check."""
    for text in [
        'C86 <= 1.8117251, unrefereed, certificate and checker linked below.',
        'This is a candidate upper bound. An exact checker accepted it; no human has looked.',
        'I think this improves your 0.25357, but I could be wrong about the normalisation.',
        'Four candidate signals, three of which were already in the community tables.',
        'The numbers match on my own machine. I re-checked them.',
        'It looks like the shorter peptides bind the groove even more, which is the opposite '
        'of what we wanted.',
        'Encontrei uma melhoria pequena no teu limite. Mando o certificado.',
    ]:
        assert fails(text) == [], text


def test_reporting_what_a_computation_shows_is_not_a_promise():
    """Caught by running the check over 40 real drafts: the only hit was a false positive.

    "confirma que o melhor valor do artigo ja era o otimo" reports what our computation says about
    someone else's published value. That is the honest sentence the rule wants, and bare `confirma`
    is ordinary Portuguese for it. Same mistake as a bare `pod` matching a seed pod.
    """
    assert fails('Nas outras onze, confirma que o melhor valor do artigo ja era o otimo.') == []
    assert fails('The checker confirms the paper value for eleven of them.') == []
    # ...while a first-person claim about our own result still fails.
    assert fails('Provamos que o limite e exacto.')
    assert fails('Isto confirma a conjectura.')


def test_the_word_promising_alone_is_not_enough():
    """"a promising direction" is a judgement about what to try next, not a ranked result."""
    assert fails('This looks like a promising direction to try next.') == []


def test_candidate_is_never_flagged():
    assert pw.terms('A candidate bound, not yet checked.') == set()


def test_underscores_do_not_hide_a_match():
    """\\b does not match between a letter and an underscore, which is how slugs smuggle words."""
    assert fails('see most_promising_result.json') == [] , 'a filename is not a sentence to a reader'
    assert fails('It is the most promising result we have.')


def test_vocabulary_is_stable_and_narrow():
    assert pw.vocabulary() == ['overstated-novelty', 'ranked-best', 'settled-finding']
