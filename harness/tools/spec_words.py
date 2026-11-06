#!/usr/bin/env python3
"""Count the job-specific words in a spec, for the goal-drift word caps.

Why (2026-11-06): the cap exists so the goal cannot turn into process -- a spec that grows
layered rules is how the friends lane's goal drifted. It was counting the wrong words. The
sweep writes its generated specs with the lane's whole RULES.md and INTAKE.toml embedded
verbatim, plus a shared-lessons block, so c5's `0-search` spec came to 3,047 words of which
only 472 were the actual ask. launch.sh refused it at the 1,000-word cap, and the lane's
generated jobs could not run at all. Lane rules grow over time, so this gets worse on its own;
the no-ops rule added to LANE_RULES_COMMON.md earlier the same day put another ~130 words into
every lane's RULES.md and therefore into every generated spec.

So the cap measures what somebody actually asked for, and not what the harness injected around
it. Blocks that the harness itself wrote into the spec are excluded:

  --- BEGIN VERBATIM LANE POLICY: <path> ---  ...  --- END VERBATIM LANE POLICY ---
  ## Shared lessons                           ...  up to the next heading

Shared material added to the *prompt* rather than the spec -- the prior-art block, GOAL.md,
`rh lessons for LANE` -- is appended after this check runs and is never counted.

Unbalanced markers count everything, so a malformed spec gets the strict budget rather than an
unlimited one. These markers are written by the harness's own sweep; a hand-written spec that
pastes them in to buy itself room is circumventing a hard rule, which is not what this guards.
"""
import argparse
import re
import sys
from pathlib import Path

BEGIN = re.compile(r'^---\s*BEGIN VERBATIM LANE POLICY\b.*---\s*$')
END = re.compile(r'^---\s*END VERBATIM LANE POLICY\s*---\s*$')
LESSONS = re.compile(r'^##\s+Shared lessons\s*$')
HEADING = re.compile(r'^#{1,6}\s+\S')


def job_specific(text):
    """The spec text with harness-injected blocks removed."""
    if sum(bool(BEGIN.match(l)) for l in text.splitlines()) != \
       sum(bool(END.match(l)) for l in text.splitlines()):
        return text  # unbalanced: fail closed, count the whole file
    kept, in_policy, in_lessons = [], False, False
    for line in text.splitlines():
        if BEGIN.match(line):
            in_policy = True
            continue
        if END.match(line):
            in_policy = False
            continue
        if in_policy:
            continue
        if LESSONS.match(line):
            in_lessons = True
            continue
        if in_lessons:
            # The lessons block runs to the next heading of any level.
            if HEADING.match(line):
                in_lessons = False
            else:
                continue
        kept.append(line)
    return '\n'.join(kept)


def count(path):
    text = Path(path).read_text(errors='replace')
    return len(job_specific(text).split())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('spec', type=Path)
    a = p.parse_args(argv)
    try:
        print(count(a.spec))
    except OSError as e:
        print('spec unreadable: {}'.format(e), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
