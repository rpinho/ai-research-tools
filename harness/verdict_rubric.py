#!/usr/bin/env python3
"""Three questions a referee has to answer, with evidence, before its pass counts.

Why (2026-11-06, from OpenAI's BioBasicsBench failure modes). The failures that matter are not
arithmetic. They are an assumption quietly outranking the controls, data that disagrees being
dropped, and a label believed because it was in the file. A referee can pass a result without ever
looking at any of the three, and ours did: 528 claims went through a referee that passed 98% of
them, and nothing it produced was worth sending.

So a verdict now has to say:

  CONTROLS:     did an assumption override what the controls show?
  CONTRADICTING: was data that contradicts the conclusion dropped or ignored?
  LABELS:       was a label or annotation trusted without checking it against the raw data?

Each answered with EVIDENCE, not with "no". An unsupported "no" is the exact move the three
questions exist to stop, so a bare yes/no fails and the message says so. Evidence means something
a reader can go and check: a path, a file, a number, a command, a quoted line, a count.

The questions are also in `tools/RULES.md` and `harness/LANE_RULES_COMMON.md`, so every job prompt
and every lane's rules carry them. This module is the half that cannot be skipped by not reading.
"""
import re

CHECKS = (
    ('CONTROLS',
     'did an assumption override what the controls show?'),
    ('CONTRADICTING',
     'was data that contradicts the conclusion dropped or ignored?'),
    ('LABELS',
     'was a label or annotation trusted without checking it against the raw data?'),
)

# A referee verdict that counts as a pass. NONE and FAILS need no rubric: nothing is being claimed.
PASSING = re.compile(r'^\s*(?:HOLDS|ACCEPT|PASS)', re.I)

# Something a reader can check: a path, a bare filename, a DOI or URL, a number, a command, or a
# quoted line. A bare `ctrl.json` counts: it was rejected at first for having no slash, which would
# have failed the most natural answer a referee writes.
EVIDENCE = re.compile(r'(?:[\w./~-]*/[\w./~-]+)|(?:[\w-]+\.[A-Za-z]{2,6}\b)|'
                      r'(?:https?://|doi:|10\.\d{4,9}/)|\d|`[^`]+`|"[^"]+"')
# ...but a placeholder is not evidence, so a pasted, unfilled template still fails.
PLACEHOLDER = re.compile(r'<[^>]*>')
BARE = re.compile(r'^\s*(?:no|yes|none|n/?a|ok|fine|checked|nothing)\s*[.!]?\s*$', re.I)


def passing(referee):
    return bool(PASSING.match(str(referee or '')))


def answers(text):
    """-> {CHECK: answer text}.

    A marker is `CONTROLS:` anywhere, not only at the start of a line: referees write all three
    on one line as often as on three, and a parser that demanded line starts silently read such a
    verdict as answering one question out of three. The answer runs to the next marker or the end.
    """
    text = str(text or '')
    marks = []
    for name, _ in CHECKS:
        for match in re.finditer(r'(?i)(?<![A-Za-z_])' + name + r'\s*[:\-]\s*', text):
            marks.append((match.start(), match.end(), name))
    marks.sort()
    found = {}
    for i, (start, after, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[after:end].strip().rstrip('.').strip()
        if name not in found or len(body) > len(found[name]):
            found[name] = body
    return found


def findings(text, referee='HOLDS'):
    """-> (failures, warnings) for a verdict. Only a passing verdict is held to the rubric."""
    if not passing(referee):
        return [], []
    given = answers(text)
    failures = []
    for name, question in CHECKS:
        answer = given.get(name)
        if answer is None:
            failures.append('RUBRIC_MISSING {}: a passing verdict must answer "{}" on a line '
                            'starting "{}:". (BioBasicsBench failure modes, 11-06.)'
                            .format(name, question, name))
        elif BARE.match(answer) or PLACEHOLDER.search(answer) or not EVIDENCE.search(answer):
            failures.append('RUBRIC_UNSUPPORTED {}: "{}" answers the question without evidence. '
                            'An unsupported no is the move this check exists to stop. Name what '
                            'you looked at: a file, a command, a count, or a quoted line.'
                            .format(name, answer[:60]))
    return failures, []


def template():
    """The block a referee fills in. Printed by the prompt templates and the refusal message."""
    return '\n'.join('{}: <answer, with the file, command, count or line you checked>  # {}'
                     .format(name, question) for name, question in CHECKS)
