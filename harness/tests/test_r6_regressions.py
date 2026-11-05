"""Round-6 reviewer bypass repros against the 11-05 linter changes; each must now FAIL.

The target check must fail, and each control must fail too.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_publish_lint import (
    packet, check, credited, own_diff_credit, unchanged_update, live_snapshot,
)


def assert_bypass(packet, name, revision=None):
    assert packet.lint()['checks'][name]['status'] == 'FAIL'

@pytest.mark.parametrize('expression', ['0.25 <= 1 * C', 'C * 1 >= 0.25'])
def test_numeric_operand_is_only_part_of_expression(packet, expression):
    unchanged_update(packet, 'The upper bound is C <= 0.25 (rigorous, exact.py).')
    packet.write('PROOF.md', f'We establish {expression} (rigorous, exact.py).\n')
    assert_bypass(packet, 'replay')
    # Control: algebraically identical, with the multiplication by one removed.
    packet.write('PROOF.md', 'We establish C >= 0.25 (rigorous, exact.py).\n')
    check(packet, 'replay', False)


def test_live_union_allows_explicit_direction_flip(packet):
    live = ('The rigorous lower bound is C >= 0.25 (exact.py).\n\n'
            'The rigorous upper bound is C <= 0.3 (exact.py).\n')
    comparison = '\n0.25 < 0.3.\n'
    unchanged_update(packet, live + comparison)
    packet.claims.append(dict(value='0.3', kind='rigorous', producer='exact.py',
                              arithmetic='exact', new_bound=True))
    packet.body.write_text(live + comparison)
    packet.write('README.md', live)
    packet.write('PROOF.md', 'The rigorous upper bound is C <= 0.25 (exact.py).\n')
    assert_bypass(packet, 'replay')
    # Control: remove only the true numeric comparison from BOTH bodies.
    packet.obj('sources/live_pr42.json', live_snapshot(42, live))
    packet.body.write_text(live)
    check(packet, 'replay', False)


@pytest.mark.parametrize('credit_line', [
    'This result is not by V. Patankar.',
    'Background: a textbook by V. Patankar.',
])
def test_diff_credit_does_not_establish_authorship(packet, credit_line):
    own_diff_credit(packet, '--- a/README.md\n+++ b/README.md\n+' + credit_line + '\n')
    assert_bypass(packet, 'credit')
    # Control: the same text on a removed line supplies no author evidence.
    own_diff_credit(packet, '--- a/README.md\n+++ b/README.md\n-' + credit_line + '\n')
    check(packet, 'credit', False)


def test_diff_credit_accepts_a_different_full_name(packet):
    own_diff_credit(packet, '--- a/README.md\n+++ b/README.md\n+by [V. Patankar-Smith](https://example.org/x)\n')
    assert_bypass(packet, 'credit')
    # Control: a different name whose suffix starts with a word character blocks.
    own_diff_credit(packet, '--- a/README.md\n+++ b/README.md\n+by [V. Patankarov](https://example.org/x)\n')
    check(packet, 'credit', False)


def test_sentence_merge_hides_changed_bound(packet):
    unchanged_update(packet, 'The upper bound is C <= 0.25 (rigorous, exact.py).')
    packet.write('PROOF.md', 'The earlier section defines C. We establish C >= 0.25 (rigorous, exact.py).\n')
    assert_bypass(packet, 'replay')
    # Control: a paragraph break restores the true sentence boundary.
    packet.write('PROOF.md', 'The earlier section defines C.\n\nWe establish C >= 0.25 (rigorous, exact.py).\n')
    check(packet, 'replay', False)


def test_sentence_merge_lets_exact_producer_cover_float_proof(packet):
    packet.write('PROOF.md', 'The identity is verified by exact.py for C. The bound is verified by float.py.\n')
    assert_bypass(packet, 'rigor')
    packet.write('PROOF.md', 'The identity is verified by exact.py for C.\n\nThe bound is verified by float.py.\n')
    check(packet, 'rigor', False)


def test_sentence_merge_lets_other_sentence_supply_credit_name(packet):
    credited(packet)
    packet.body.write_text('Ada Lovelace studies C. I credit Mallory in #17.\n')
    assert_bypass(packet, 'credit')
    packet.body.write_text('Ada Lovelace studies C.\n\nI credit Mallory in #17.\n')
    check(packet, 'credit', False)
