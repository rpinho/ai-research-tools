"""The no-ops-in-friend-email rule, and the field words it must not mistake for ops."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ops_mentions


def fails(text, asked=()):
    failures, _ = ops_mentions.findings(text, asked)
    return [f.split(';')[0].removeprefix('OPS_DETAIL: ') for f in failures]


# The sentence this rule came from, and the shapes it generalises to.
@pytest.mark.parametrize('text,expected', [
    ('The first two ran on a RunPod server that has since had a security incident. '
     'So I re-solved 20 instances on my Mac Mini.', ['runpod', 'security-incident']),
    ('Corri outra vez porque houve um incidente de seguranca no servidor.', ['security-incident']),
    ('Os primeiros dois correram num servidor RunPod, que depois teve um aviso de seguranca.',
     ['runpod', 'security-incident']),
    ('I rotated the API key afterwards, so the numbers are safe.', ['key-rotation']),
    ('The pods were reaped before the sweep finished.', ['pod']),
    ('I rented a pod with an H100 for the big instances.', ['pod']),
    ('Our OpenRouter budget ran out mid-run.', ['openrouter']),
    ('The server crashed, so the last batch is missing.', ['server-down']),
    ('O servidor foi abaixo a meio da corrida.', ['server-down']),
    ('I was watching it in tmux when the watchdog killed it.', ['tmux', 'watchdog']),
    ('The harness refused to send it.', ['harness']),
    ('That run went through Modal Labs instead.', ['modal']),
])
def test_an_ops_sentence_fails(text, expected):
    assert fails(text) == sorted(expected)


# These are the recipients' own vocabulary. A lint that fired on them would be turned off.
@pytest.mark.parametrize('text', [
    'The incident wave peaks at 3.2 and the detector key is in Table 2.',
    'A modal split of 40% walking changes the lane assignment in the routing model.',
    'The cell line was compromised, so we repeated the assay.',
    'Each seed pod holds four to six ovules, and iPod-era sampling is in the appendix.',
    'The key result is that the bound holds for every n, which is the key point.',
    'Harnessing the symmetry gives a shorter proof, and the lane capacity is fixed.',
    'Podcast episode 14 covers the same instance family.',
    'We rotated the image 90 degrees and the optimum did not move.',
    'Claude Code wrote the first draft and Codex checked it against your table.',
])
def test_a_scientists_own_words_are_not_ops(text):
    assert fails(text) == []


def test_the_two_ambiguous_words_fail_only_in_ops_company():
    """`pod` is a seed pod to a biologist; `Codex` is the tool Ricardo names in public."""
    assert fails('Each seed pod holds four to six ovules.') == []
    assert fails('I rented a pod for it.') == ['pod']
    assert fails('Codex wrote the first pass and I checked it.') == []
    assert fails('Codex lost its key when the host was compromised.') == ['tool-incident']
    # Sentence by sentence, so a word in one line and its company in the next do not combine.
    assert fails('Codex wrote the first pass.\nThe detector was compromised in 2019.') == []
    assert fails('Each seed pod holds four to six ovules.\nI rented the microscope.') == []


def test_a_term_the_friend_raised_is_a_warning_so_a_reply_can_answer_them():
    """The 11-06 answer to the question the 11-05 email provoked has to be sendable."""
    answer = ('O RunPod, onde alugo as maquinas, mandou um aviso de seguranca, e por precaucao '
              'voltei a correr uma amostra no meu computador. Os numeros batem todos certo.')
    assert fails(answer) == ['runpod', 'security-incident']
    failures, warnings = ops_mentions.findings(answer, ['runpod', 'security-incident'])
    assert failures == []
    assert [w.split(';')[0] for w in warnings] == ['ops: runpod', 'ops: security-incident']
    # The exemption is per term: it does not open the door to the rest of our setup.
    assert fails(answer + ' O watchdog matou o resto.', ['runpod', 'security-incident']) == ['watchdog']


def test_only_our_own_term_is_reported_never_the_draft_text():
    """A lint result goes to the lane ledger; the draft body stays a private file."""
    secret = 'We moved it off RunPod because of the incident at 4am on the shared host.'
    failures, _ = ops_mentions.findings(secret)
    assert failures and not any('4am' in f or 'shared host' in f for f in failures)
    for f in failures:
        assert f.split(';')[0].removeprefix('OPS_DETAIL: ') in ops_mentions.vocabulary()


def test_the_vocabulary_is_what_asked_about_accepts():
    assert 'runpod' in ops_mentions.vocabulary() and 'tool-incident' in ops_mentions.vocabulary()
    assert ops_mentions.vocabulary() == sorted(ops_mentions.vocabulary())
    # An unknown term changes nothing, so a typo cannot quietly turn the check off.
    assert fails('We moved it off RunPod.', ['runpodd']) == ['runpod']
