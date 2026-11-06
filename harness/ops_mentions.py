"""No infrastructure or operations in a message to a person (Ricardo, 11-06).

A message to a friend carries only what the friend needs. Our hosting provider, our pods,
a security incident, a key rotation, the harness and its watchdogs, and why something was
re-run are all ours to deal with and none of them are the result. The wording that works is
"re-checked on my own machine; the numbers match".

The rule came from one sentence, in an email of 11-05: "The first two ran on a RunPod server
that has since had a security incident." The recipient wrote back asking what that meant, and
Ricardo's read was "we shouldn't have said that. That was completely unnecessary to say." The
sentence cost a round trip, moved the subject from the result to our plumbing, and told a
friend about a host compromise he had no way to act on.

The vocabulary is deliberately narrow, because these are working scientists and several ops
words are ordinary words in their fields. Bare `incident` is an incident wave, `lane` is a
traffic lane, `compromised` is a compromised cell line, `modal` is a modal split and a modal
logic, and `key` is a key result. So this module matches provider names, unambiguous ops
nouns, and the multi-word phrases (`security incident`, `key rotation`, `server crashed`)
rather than their component words. Everything it can catch is a term we chose to write.

Two words need the rest of their sentence to mean anything, and are checked as pairs. `pod`
is a seed pod to a biologist and a rented machine to us, so it fails beside rented-machine or
incident wording and not in "each seed pod holds four to six ovules". And Ricardo names
Claude Code and Codex in his own public READMEs, so naming the tool that did the work is not
a leak: those fail only beside incident wording. "Codex lost its key" is ops, "Codex wrote
the first draft" is the story.

When the friend raised the subject themselves, a reply that answers them has to be able to
say the word. `rh outreach draft --asked-about runpod` names the term the friend asked about,
from this module's own vocabulary, and that term drops to a warning for that message. It
takes a term, never the friend's text: their words stay out of the ledger.
"""
import re

# One entry per term: the name an author passes to --asked-about, and what it matches.
TERMS = {
    'runpod': r'run\s?pod',
    'openrouter': r'open\s?router',
    'modal': r'modal\s+labs|modal\.com',
    'tmux': r'tmux',
    'watchdog': r'watch\s?dogs?',
    'egress': r'egress',
    'harness': r'harness(?:es)?',
    'credentials': r'credentials|credenciais',
    'security-incident': (r'security\s+(?:incident|breach|notice|warning|advisory)|'
                          r'(?:incidente|falha|problema|aviso|alerta)\s+de\s+seguran[cç]a'),
    'key-rotation': (r'(?:key|api\s*key|token|chave)\s+(?:rotation|revocation)|'
                     r'rotat(?:e|ed|ing)\s+(?:the\s+|our\s+|a\s+|my\s+)?'
                     r'(?:api\s*)?(?:key|keys|token|tokens|credentials?)|'
                     r'revok(?:e|ed|ing)\s+(?:the\s+|our\s+|a\s+|my\s+)?'
                     r'(?:api\s*)?(?:key|keys|token|tokens|credentials?)'),
    'server-down': (r'(?:server|servidor|machine|m[aá]quina)\s+'
                    r'(?:crashed|went\s+down|was\s+down|is\s+down|foi\s+abaixo|caiu)'),
}

# Incident wording. Bare `key` is left out: "the key result" is the commonest phrase in these
# emails, and the `key-rotation` term above already carries the compromising sense of it.
INCIDENT = (r'security|incident|breach|breached|compromis(?:e|ed)|rotat(?:e|ed|ion)|'
            r'revok(?:e|ed)|revocation|outage|crashed|hacked|leaked|'
            r'seguran[cç]a|incidente|comprometid[oa]')

# Rented-machine wording: what a sentence about our hardware says besides the word itself.
INFRA = (r'run\s?pod|rented?|renting|gpu|gpus|a100|h100|host|hosts|hosted|provider|'
         r'instances?|reaped|reap|spun\s+up|spin\s+up|ssh|cluster|volume|billing|'
         r'alugo|aluguei|alugado|servidor|servidores')

# Two terms need company before they mean anything. `pod` is a seed pod to a biologist and
# `Codex` is the tool that did the work -- Ricardo names Claude Code and Codex in his own
# public READMEs. Each fails only in a sentence that also carries the wording on the right.
NEAR = {
    'pod': (r'pods?', INFRA + r'|' + INCIDENT),
    'tool-incident': (r'codex|claude\s+code|claude', INCIDENT),
}

FIX = ('say "re-checked on my own machine; the numbers match", or answer in one plain line if '
       'the friend asked')


def _word(pattern):
    """Match the pattern as a whole word, so iPod, podcast and harnessing do not count."""
    return re.compile(r'(?<![A-Za-z0-9_])(?:' + pattern + r')(?![A-Za-z0-9_])', re.I)


MATCHERS = {name: _word(pattern) for name, pattern in TERMS.items()}
PAIRS = {name: (_word(term), _word(company)) for name, (term, company) in NEAR.items()}
# A sentence end, or a line break: a subject line and a bullet are their own sentences.
SENTENCE = re.compile(r'[^.!?\n]+')


def sentences(text):
    return [s for s in (m.group().strip() for m in SENTENCE.finditer(text)) if s]


def findings(text, asked_about=()):
    """Return (failures, warnings) for the no-ops rule.

    `asked_about` holds terms the friend themselves raised; each one is reported as a warning
    instead of a failure, so a reply that answers their question can pass. Only the term is
    ever reported, never the surrounding text: a lint result is written to the lane ledger and
    the draft body stays a private file.
    """
    found = {name for name, matcher in MATCHERS.items() if matcher.search(text)}
    blocks = sentences(text)
    found |= {name for name, (term, company) in PAIRS.items()
              if any(term.search(s) and company.search(s) for s in blocks)}
    failures, warnings = [], []
    asked = {str(a).strip().casefold() for a in asked_about or ()}
    for name in sorted(found):
        if name in asked:
            warnings.append('ops: {}; the friend asked about it, so answer in one plain line '
                            'and add nothing else about our setup'.format(name))
        else:
            failures.append('OPS_DETAIL: {}; an email carries only what the friend needs -- {}'
                            .format(name, FIX))
    return failures, warnings


def vocabulary():
    """The terms --asked-about accepts, so a typo cannot quietly open the gate."""
    return sorted(set(TERMS) | set(NEAR))
