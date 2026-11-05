"""The mixed-language rule: English science, the recipient's language for the personal lines.

Every fixture here is invented. No real name, address or correspondence appears in this repo.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mixed_language as ml

GREETING_PT = 'Olá Alpha, tudo bem? Espero que o trabalho esteja a correr bem por aí.'
GREETING_EN = 'Hello Alpha, I hope you are well and that the work is going along nicely.'
SCIENCE_EN = ('We measured a correlation of 0.68 between length and the mean score, so the '
              'ranking tracks length. The control run gives 0.11 with the same dataset.')
SCIENCE_PT = ('Medimos uma correlação de 0.68 entre o comprimento e a média, portanto a '
              'ordenação segue o comprimento. O controlo dá 0.11 com os mesmos dados.')
SIGNOFF_PT = 'Obrigado pelo teu tempo e um abraço, até breve.'
SIGNOFF_EN = 'Thanks very much for your time and best regards.'


def email(*parts):
    return '\n\n'.join(parts)


def test_language_of_calls_each_language_and_abstains_when_unsure():
    assert ml.language_of(GREETING_PT) == 'pt'
    assert ml.language_of(SCIENCE_EN) == 'en'
    assert ml.language_of(SCIENCE_PT) == 'pt'
    assert ml.language_of('Obrigado') is None, 'too short to call'
    assert ml.language_of('') is None


def test_code_treats_unset_and_english_alike_and_rejects_nonsense():
    assert ml.code(None) is None and ml.code('') is None
    assert ml.code('en') is None and ml.code('en-GB') is None
    assert ml.code('pt') == 'pt' and ml.code('PT') == 'pt' and ml.code('pt-PT') == 'pt'
    with pytest.raises(ValueError):
        ml.code('Portuguese please')


def test_english_recipient_is_never_judged():
    """Unset or 'en' means an ordinary English email: this rule has nothing to say about it."""
    for language in (None, '', 'en'):
        assert ml.findings(email(GREETING_EN, SCIENCE_PT, SIGNOFF_EN), language) == ([], [])


def test_portuguese_science_fails_and_names_the_paragraph():
    failures, warnings = ml.findings(email(GREETING_PT, SCIENCE_PT, SIGNOFF_PT), 'pt')
    assert len(failures) == 1 and 'paragraph 2' in failures[0] and 'English' in failures[0]
    assert warnings == []


def test_the_rule_followed_passes_clean():
    assert ml.findings(email(GREETING_PT, SCIENCE_EN, SIGNOFF_PT), 'pt') == ([], [])


def test_english_opening_and_closing_fail():
    """The shape is fixed (11-05): Portuguese opening, English science, Portuguese close."""
    failures, warnings = ml.findings(email(GREETING_EN, SCIENCE_EN, SIGNOFF_EN), 'pt')
    assert len(failures) == 2
    assert 'paragraph 1' in failures[0] and 'opening' in failures[0]
    assert 'paragraph 3' in failures[1] and 'closing' in failures[1]
    assert warnings == []


def test_short_edges_are_judged_by_their_greeting_words():
    """"Hi Alpha," and "Até breve" are decided outright, below the word-count threshold."""
    assert ml.edge_language('Hi Alpha,') == 'en'
    assert ml.edge_language('Olá Alpha,') == 'pt'
    assert ml.edge_language('Thanks, and best regards.') == 'en'
    assert ml.edge_language('Abraço, até breve.') == 'pt'
    failures, _ = ml.findings(email('Hi Alpha,', SCIENCE_EN, 'Abraço,'), 'pt')
    assert len(failures) == 1 and 'opening' in failures[0]


def test_a_middle_personal_line_in_english_only_warns():
    failures, warnings = ml.findings(
        email(GREETING_PT, 'By the way, I hope the family is well and the holidays went nicely.',
              SCIENCE_EN, SIGNOFF_PT), 'pt')
    assert failures == []
    assert len(warnings) == 1 and 'paragraph 2' in warnings[0]


@pytest.mark.parametrize('line', [
    "I agree with all of it, and I won't call it an erratum.",
    "On the arXiv note with a data record: I would like to add one.",
    'The argument holds for the general case, which is the part worth saying out loud.',
])
def test_english_discussion_in_the_middle_is_never_flagged(line):
    """The rule puts discussion and summary in English with the numbers, so English prose in
    the middle is right, not a lapse. Warning on it nagged at correct drafts (friends, 11-05).
    """
    assert ml.findings(email(GREETING_PT, line, SCIENCE_EN, SIGNOFF_PT), 'pt') == ([], [])


def test_an_all_english_email_with_numbers_in_one_paragraph_fails_the_shape():
    failures, _ = ml.findings(SCIENCE_EN, 'pt')
    assert len(failures) == 1 and 'own English paragraph' in failures[0]


def test_a_personal_line_with_a_number_is_not_science():
    """"obrigado pelos 20 minutos" must never fail as a Portuguese science block."""
    failures, warnings = ml.findings(email('Obrigado pelos 20 minutos de ontem, foi muito útil para nós.',
                                           SCIENCE_EN, SIGNOFF_PT), 'pt')
    assert failures == [] and warnings == []


def test_statistics_make_a_paragraph_science_even_without_two_numbers():
    text = 'A correlação é significativa (p < 0.01) e o desvio mantém-se igual entre as amostras.'
    assert ml.is_science(text)
    assert len(ml.findings(email(GREETING_PT, text, SIGNOFF_PT), 'pt')[0]) == 1


def test_attachments_must_be_english(tmp_path):
    good, bad = tmp_path / 'summary.md', tmp_path / 'resumo.md'
    good.write_text('# Summary\n\n' + SCIENCE_EN)
    bad.write_text('# Resumo\n\n' + SCIENCE_PT)
    clean = email(GREETING_PT, SCIENCE_EN, SIGNOFF_PT)
    assert ml.findings(clean, 'pt', [good]) == ([], [])
    failures, _ = ml.findings(clean, 'pt', [bad])
    assert len(failures) == 1 and 'resumo.md' in failures[0] and 'always English' in failures[0]


def test_unreadable_or_binary_attachments_are_left_alone(tmp_path):
    missing, binary = tmp_path / 'gone.md', tmp_path / 'figure.png'
    binary.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 32)
    clean = email(GREETING_PT, SCIENCE_EN, SIGNOFF_PT)
    assert ml.findings(clean, 'pt', [missing, binary]) == ([], [])
