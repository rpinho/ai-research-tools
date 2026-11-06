#!/usr/bin/env python3
"""What actually runs on this machine, read from disk, never from memory.

Names only. This is the input to coverage.py, which is the thing with opinions; everything here
is a plain read, so the two questions stay separate: what exists, and what enforces it. A map
written by the same pass that grades it cannot be checked.

Where to look comes from the manifest's `sources` block, so this file contains no paths of its
own. Each source names a type and its globs:

    "sources": {
      "hook":  {"globs": ["~/.config/myagent/hooks/*.py"]},
      "skill": {"globs": ["~/.config/myagent/skills/*/SKILL.md"], "name_from": "parent"},
      "rule":  {"globs": ["~/notes/rules/*.md"]}
    }

An item's name is its filename stem, or its parent directory with `name_from: "parent"` (for the
`dir/SKILL.md` shape). A markdown file's YAML frontmatter, if it has one, supplies `name` and
`description`; `description` is what coverage.py matches keyword rules against, so it is worth
writing.

Usage:
  inventory.py --config layers.json            a names-only table, grouped by type
  inventory.py --config layers.json --json     the same, for coverage.py
  inventory.py --config layers.json --type hook
"""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import sys

FRONTMATTER = re.compile(r'^\s*(name|description|type)\s*:\s*(.+?)\s*$')


def load_config(path):
    try:
        cfg = json.loads(Path(path).read_text())
    except OSError as exc:
        raise SystemExit('cannot read {}: {}'.format(path, exc))
    except ValueError as exc:
        raise SystemExit('{} is not valid JSON: {}'.format(path, exc))
    if not isinstance(cfg, dict):
        raise SystemExit('{} must be a JSON object'.format(path))
    return cfg


def frontmatter(text):
    """The YAML-ish frontmatter of a markdown file, if it opens with one."""
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    out = {}
    for line in text[3:end if end > 0 else len(text)].splitlines():
        m = FRONTMATTER.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def describe(path, read_text=True):
    """-> (name_from_frontmatter, description). Only markdown is read."""
    if not read_text or path.suffix.lower() not in ('.md', '.markdown'):
        return None, ''
    try:
        text = path.read_text(errors='replace')
    except OSError:
        return None, ''
    fm = frontmatter(text)
    if fm.get('description'):
        return fm.get('name'), fm['description'][:300]
    # No frontmatter: the first non-heading, non-blank line is the best summary available.
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(('#', '---', '<!--')):
            return fm.get('name'), line[:300]
    return fm.get('name'), ''


def collect(cfg, types=None, root=None):
    rows = []
    root = Path(root or '.')
    for kind, source in sorted((cfg.get('sources') or {}).items()):
        if types and kind not in types:
            continue
        if not isinstance(source, dict):
            raise SystemExit('sources.{} must be an object'.format(kind))
        seen = set()
        for pattern in source.get('globs') or []:
            expanded = os.path.expanduser(str(pattern))
            if not os.path.isabs(expanded):
                expanded = str(root / expanded)
            for found in sorted(glob.glob(expanded)):
                path = Path(found)
                if path in seen or not path.exists():
                    continue
                seen.add(path)
                if source.get('name_from') == 'parent':
                    name = path.parent.name
                else:
                    name = path.stem
                fm_name, description = describe(path)
                rows.append(dict(type=kind, name=fm_name or name, where=str(path),
                                 description=description))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', default='layers.json', help='the manifest (default: layers.json)')
    p.add_argument('--root', default='.', help='resolve relative globs against this directory')
    p.add_argument('--json', action='store_true')
    p.add_argument('--type', action='append')
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    rows = collect(cfg, a.type, a.root)
    if a.json:
        json.dump(rows, sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    if not rows:
        print('nothing found. Check the `sources` globs in ' + a.config)
        return 0
    for kind in sorted({r['type'] for r in rows}):
        group = [r for r in rows if r['type'] == kind]
        print('{} ({})'.format(kind, len(group)))
        for r in group:
            print('    ' + r['name'])
        print()
    print('total items:', len(rows))
    return 0


if __name__ == '__main__':
    sys.exit(main())
