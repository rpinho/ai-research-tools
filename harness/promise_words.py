#!/usr/bin/env python3
"""Don't sell an unrefereed result. Report the verdict, or say plainly that nobody checked it.

Why. The first row in the corrections ledger (2026-11-02) is a friends email that ranked
unrefereed lane results as "most promising". Ricardo's rule, recorded as the memory
`report-referee-verdicts-not-unrefereed-promise`: never rank unrefereed results that way; give the
referee verdict, or say plainly it is unrefereed. By 11-06 that rule had been broken five times
and was still only prose, which is why it is code now.

The shape is deliberately the same as ops_mentions: a narrow vocabulary, checked per sentence,
with one way to satisfy it. A draft may use ranking language as much as it likes AS LONG AS the
text also says what refereed it, or admits that nothing did. That is the actual rule. It is not a
ban on enthusiasm, and a lint that banned enthusiasm would just be edited around.

What counts as a promise: ranking one result above others ("most promising", "the best of them",
"strongest result"), or language that implies a settled finding ("breakthrough", "we proved",
"confirms"). What clears it: naming a referee verdict, or an explicit unrefereed disclosure, in
the same text. Portuguese forms are included because half of these emails are in Portuguese.

Deliberately NOT flagged: "candidate", "we think", "it looks like", a bare number, a link to a
certificate. Those are already honest.
"""
import re

PROMISE = {
    'ranked-best': (r'most\s+promising|more\s+promising|best\s+(?:result|one|of\s+(?:them|these|the\s+\w+))|'
                    r'strongest\s+(?:result|one|candidate\s+by\s+far)|'
                    r'mais\s+promissor(?:a|es)?|melhor\s+resultado'),
    # 11-06: bare `confirma` was a false positive on a real draft, "confirma que o melhor
    # valor do artigo já era o ótimo" -- reporting what a computation shows about someone else's
    # paper, which is exactly the honest sentence we want. Portuguese is now scoped the same way
    # as the English: a first-person claim, or an explicit "isto confirma".
    'settled-finding': (r'breakthrough|we\s+(?:proved|solved|established)|'
                        r'this\s+(?:proves|confirms|settles)|'
                        r'(?:prov[aá]mos|resolvemos|estabelecemos)|'
                        r'(?:isto|isso|este\s+resultado)\s+(?:prova|confirma|resolve)'),
    'overstated-novelty': (r'(?:first|nobody)\s+(?:ever\s+)?(?:to\s+)?(?:found|did|has\s+done)\s+this|'
                           r'never\s+been\s+done|unprecedented'),
}

# Any one of these in the text clears the whole check: it is the disclosure the rule asks for.
DISCLOSED = (r'unrefereed|un-refereed|not\s+(?:yet\s+)?(?:been\s+)?refereed|no\s+referee|'
             r'(?:nobody|nothing|no\s+one)\s+has\s+(?:refereed|checked|reviewed|looked)|'
             r'not\s+(?:yet\s+)?(?:peer[- ])?reviewed|'
             r'referee[d]?\s*[:=]|verdict\s*[:=]|HOLDS|ACCEPT|REJECT|'
             r'n[aã]o\s+(?:foi\s+)?(?:revisto|revista|arbitrado)|sem\s+revis[aã]o')


def _word(pattern):
    """No \\b: it does not match between a letter and an underscore, and these appear in slugs."""
    return re.compile(r'(?<![A-Za-z0-9_])(?:' + pattern + r')(?![A-Za-z0-9_])', re.I)


MATCHERS = {name: _word(pattern) for name, pattern in PROMISE.items()}
DISCLOSURE = re.compile(DISCLOSED, re.I)


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?\n])\s+', text or '') if s.strip()]


def terms(text):
    """Which kinds of promise the text makes, as a set of names. Public: a caller may want to
    ask the question without getting findings, the way ops_mentions.terms is used."""
    return {name for name, matcher in MATCHERS.items() if matcher.search(text or '')}


def disclosed(text):
    return bool(DISCLOSURE.search(text or ''))


def findings(text, has_verdict=False):
    """-> (failures, warnings).

    `has_verdict` lets a caller that KNOWS the row carries a refereed verdict (because the ledger
    says so, not because the prose says so) clear the check without a text disclosure. Everything
    else has to say it in the words the recipient will read, since the recipient cannot see our
    ledger.
    """
    found = terms(text)
    if not found:
        return [], []
    if has_verdict or disclosed(text):
        return [], ['promise language ({}) with the verdict stated; keep the verdict next to the '
                    'claim it covers'.format(', '.join(sorted(found)))]
    return ['PROMISE_UNREFEREED: this ranks or settles a result ({}) without saying what refereed '
            'it. Give the referee verdict, or say plainly that it is unrefereed. Ricardo, 11-02, '
            'on an email that called unrefereed results "most promising": report the verdict or '
            'say nobody has checked it.'.format(', '.join(sorted(found)))], []


def vocabulary():
    return sorted(MATCHERS)
