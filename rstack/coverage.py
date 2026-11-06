#!/usr/bin/env python3
"""Does every rule you wrote have something that enforces it, or is it just prose?

A rule that lives only in prose is worth whatever the agent that happened to read it is worth. The
same sentence as a hook, lint, script or test is worth the same on every run. This refuses to let
the difference stay invisible.

What it checks, and what makes it FAIL. A FAIL is not a bug in this tool: it is the list of work
to do, which is the point.

  1. Every item from inventory.py maps to exactly one layer, or to an exclusion with a written
     reason. An unmapped item FAILS. There is no catch-all rule and the loader rejects one.
  2. A rule that matches nothing FAILS. Stale rules are how a map starts lying, and this is the
     check that catches drift: it found three rules I had written from memory for jobs that did
     not exist.
  3. A rule naming an `enforcer` path that does not exist FAILS. Pointing at a deleted script is
     worse than admitting the rule is prose.

Then, separately: enforced, or prose-only? A rule counts as ENFORCED only when it names a
mechanism that exists, either because the manifest's `enforcers` block declares one or because the
rule's own text names a real item or file. Naming a mechanism that is gone does not count.

Prose-only rules are ranked by how often they have actually been broken, read from the
`corrections` files: one JSON object per line, free text. A shared word only counts when it is
distinctive (present in a quarter or fewer of the corrections), and a rule needs two of those, so
the ranking is not just a count of common vocabulary. It prints the matching corrections so every
entry can be checked by hand. **It is a queue, not a measurement**, and the only entries worth
converting are the ones with a real failure behind them.

Usage:
  coverage.py --config layers.json
  coverage.py --config layers.json --unmapped     just what needs a rule
  coverage.py --config layers.json --top 10       just the prose-to-checks queue
  coverage.py --config layers.json --json
Exit 1 if anything FAILS.
"""
import argparse
import fnmatch
import glob
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inventory  # noqa: E402

STOP = set('the a an and or of to in on for with is are be not no never always every each it its '
           'this that his him he she they them our we us my you your do does did done use used '
           'using make makes made get gets got keep keeps kept one two three first new old same '
           'only just also than then when where what which who why how all any both few more '
           'most other some such own so too very can will before after into from about without '
           'still again once because been were there here while work working worked thing things '
           'must should would could need needs needed said told sent already have'.split())


def tokens(text):
    return {w for w in re.split(r'[^a-z0-9]+', str(text).lower())
            if len(w) >= 4 and w not in STOP}


def resolve(path, root):
    expanded = os.path.expanduser(str(path))
    if not os.path.isabs(expanded):
        expanded = str(Path(root) / expanded)
    return expanded


def load(path, root):
    cfg = inventory.load_config(path)
    errors = []
    layers = cfg.get('layers') or {}
    if not layers:
        errors.append('the manifest has no `layers`')
    for key, layer in layers.items():
        if not isinstance(layer, dict) or not layer.get('title') or not layer.get('playbook'):
            errors.append('layer "{}" needs a title and a playbook'.format(key))
    for where in ('rules', 'exclusions'):
        for rule in cfg.get(where) or []:
            match, any_of = rule.get('match', ''), rule.get('any_of') or []
            literal = re.sub(r'[*?\[\]]', '', str(match))
            if not (len(literal) >= 3 or any(len(k) >= 4 for k in any_of)):
                errors.append('{} rule {!r} is a catch-all: give a `match` with at least 3 literal '
                              'characters, or an `any_of` keyword of at least 4'.format(where, rule))
            if where == 'rules' and rule.get('layer') not in layers:
                errors.append('rule {!r} names an unknown layer'.format(rule))
            if where == 'exclusions' and not str(rule.get('reason', '')).strip():
                errors.append('exclusion {!r} has no written reason'.format(rule))
            if rule.get('enforcer') and not Path(resolve(rule['enforcer'], root)).exists():
                errors.append('rule {!r} names enforcer {} which does not exist'
                              .format(rule.get('match') or rule.get('any_of'), rule['enforcer']))
    for name, target in (cfg.get('enforcers') or {}).items():
        if not Path(resolve(target, root)).exists():
            errors.append('declared enforcer for "{}" is missing: {}'.format(name, target))
    return cfg, errors


def hits(rule, row):
    if rule.get('type') and rule['type'] != row['type']:
        return False
    match = rule.get('match')
    if match and not fnmatch.fnmatch(row['name'], match):
        return False
    any_of = rule.get('any_of')
    if any_of:
        # Name and description only. Matching a whole file body makes every keyword rule match
        # almost everything, which turns rule ORDER into the entire map: a catch-all by the back
        # door. Give each rule a description worth matching instead.
        hay = ' '.join([row['name'], row.get('description', '')]).lower()
        if not any(k.lower() in hay for k in any_of):
            return False
    return bool(match or any_of)


def assign(cfg, rows):
    layer_of, excluded, unmapped, contested, used = {}, {}, [], [], set()
    for row in rows:
        key = (row['type'], row['name'], row.get('where', ''))
        placed = []
        for n, rule in enumerate(cfg.get('rules') or []):
            if hits(rule, row):
                used.add(('rules', n))
                placed.append(rule['layer'])
        if placed:
            if len(set(placed)) > 1:
                contested.append((key, placed))
            layer_of[key] = placed[0]
            continue
        for n, rule in enumerate(cfg.get('exclusions') or []):
            if hits(rule, row):
                used.add(('exclusions', n))
                excluded[key] = rule['reason']
                break
        else:
            unmapped.append(row)
    unused = [(cfg.get(w) or [])[n] for w in ('rules', 'exclusions')
              for n in range(len(cfg.get(w) or [])) if (w, n) not in used]
    return layer_of, excluded, unmapped, contested, unused


def find_enforcer(row, declared, names, root):
    if row['name'] in declared:
        target = Path(resolve(declared[row['name']], root))
        return (declared[row['name']], 'declared') if target.exists() else (None, 'missing')
    text = row.get('description', '') or ''
    for token in re.findall(r'[\w./~-]{4,}', text):
        base = os.path.basename(token.rstrip('.,);:'))
        if base in names and base.lower() != row['name'].lower():
            return base, names[base]
        if token.startswith(('~/', '/', './')):
            target = Path(resolve(token.rstrip('.,);:'), root))
            if target.is_file():
                return token, 'file'
    return None, 'prose-only'


def break_counts(rows, cfg, root):
    corrections = []
    for pattern in cfg.get('corrections') or []:
        for found in sorted(glob.glob(resolve(pattern, root))):
            try:
                lines = Path(found).read_text(errors='replace').splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    text = ' '.join(str(v) for k, v in r.items() if k not in ('utc', 'date'))
                    corrections.append((str(r.get('utc', ''))[:10], tokens(text)))
    df = {}
    for _, toks in corrections:
        for tok in toks:
            df[tok] = df.get(tok, 0) + 1
    # A floor of 2 makes a word appearing in 3 of 4 corrections 'boilerplate', which is
    # sensible at scale and silly on a small ledger. 3 only changes the tiny-N case.
    ceiling = max(3, int(0.25 * len(corrections))) if corrections else 0
    counts = {}
    for row in rows:
        own = tokens(row['name']) | tokens(row.get('description', ''))
        found = []
        for date, toks in corrections:
            shared = {w for w in own & toks if df.get(w, 0) <= ceiling}
            if len(shared) >= 2:
                found.append((date, sorted(shared)[:4]))
        counts[row['name']] = found
    return counts, len(corrections)


def near_duplicates(rows, threshold=0.5):
    out = []
    items = [r for r in rows if r['type'] == 'rule']
    for i, a in enumerate(items):
        ta = tokens(a['name'])
        for b in items[i + 1:]:
            tb = tokens(b['name'])
            if ta and tb:
                j = len(ta & tb) / float(len(ta | tb))
                if j >= threshold:
                    out.append((round(j, 2), a['name'], b['name']))
    return sorted(out, reverse=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', default='layers.json')
    p.add_argument('--root', default='.')
    p.add_argument('--top', type=int, default=10)
    p.add_argument('--unmapped', action='store_true')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)

    cfg, map_errors = load(a.config, a.root)
    rows = inventory.collect(cfg, None, a.root)
    layer_of, excluded, unmapped, contested, unused = assign(cfg, rows)

    names = {}
    for r in rows:
        names[os.path.basename(r.get('where', '')) or r['name']] = r['type']
        names.setdefault(r['name'], r['type'])

    declared = cfg.get('enforcers') or {}
    rules = [r for r in rows if r['type'] == 'rule']
    enforced, prose = [], []
    for row in rules:
        who, _ = find_enforcer(row, declared, names, a.root)
        (enforced if who else prose).append(row)

    counts, n_corrections = break_counts(prose, cfg, a.root)
    ranked = sorted(prose, key=lambda r: (-len(counts[r['name']]), r['name']))

    failures = list(map_errors)
    failures += ['unmapped {}: {}'.format(r['type'], r['name']) for r in unmapped]
    failures += ['rule matches nothing (stale): {}'.format(r) for r in unused]

    if a.unmapped:
        for row in unmapped:
            print('{:9} {}'.format(row['type'], row['name']))
        print('\n{} unmapped'.format(len(unmapped)))
        return 1 if unmapped else 0

    if a.json:
        json.dump(dict(items=len(rows), failures=failures, enforced=len(enforced),
                       prose_only=len(prose), excluded=len(excluded),
                       by_type={t: sum(1 for r in rows if r['type'] == t)
                                for t in sorted({r['type'] for r in rows})},
                       top=[dict(name=r['name'], breaks=len(counts[r['name']]))
                            for r in ranked[:a.top]],
                       duplicates=near_duplicates(rows)), sys.stdout, indent=1)
        print()
        return 1 if failures else 0

    print('rstack coverage\n' + '=' * 68)
    print('\n{:10} {:>6} {:>8} {:>9}'.format('type', 'items', 'mapped', 'excluded'))
    for t in sorted({r['type'] for r in rows}):
        group = [r for r in rows if r['type'] == t]
        m = sum(1 for r in group if (r['type'], r['name'], r.get('where', '')) in layer_of)
        x = sum(1 for r in group if (r['type'], r['name'], r.get('where', '')) in excluded)
        print('{:10} {:6} {:8} {:9}'.format(t, len(group), m, x))
    print('{:10} {:6} {:8} {:9}'.format('TOTAL', len(rows), len(layer_of), len(excluded)))

    if rules:
        print('\nrules: {} enforced, {} prose-only, of {}'
              .format(len(enforced), len(prose), len(rules)))
    print('\nby layer')
    for key in sorted(cfg.get('layers') or {}):
        n = sum(1 for v in layer_of.values() if v == key)
        print('  {:22} {:4}  {}'.format(key, n, cfg['layers'][key]['title']))

    if prose:
        print('\nprose-only, ranked by times actually broken ({} corrections read)'
              .format(n_corrections))
        for i, row in enumerate(ranked[:a.top], 1):
            found = counts[row['name']]
            print('{:3}. {:3} break(s)  {}'.format(i, len(found), row['name']))
            if row.get('description'):
                print('              {}'.format(row['description'][:100]))
            for date, shared in found[:3]:
                print('              broken {}  ({})'.format(date, ', '.join(shared)))

    if contested:
        print('\nitems more than one rule claims ({}; the first rule wins)'.format(len(contested)))
        for key, placed in contested[:8]:
            print('  {:9} {:36} {}'.format(key[0], key[1][:36], ' > '.join(placed)))

    dups = near_duplicates(rows)
    if dups:
        print('\noverlapping rules ({})'.format(len(dups)))
        for j, x, y in dups[:12]:
            print('  {:.2f}  {}  <->  {}'.format(j, x, y))

    print('\n' + '=' * 68)
    if failures:
        print('FAIL ({}):'.format(len(failures)))
        for f in failures[:40]:
            print('  ' + f)
        if len(failures) > 40:
            print('  ... and {} more'.format(len(failures) - 40))
        return 1
    print('PASS: every item is mapped to a layer or excluded with a reason.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
