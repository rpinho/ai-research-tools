#!/usr/bin/env python3
"""Instant "do we have <entity>, and how much?" lookup against the entity index.

Reads raw/_index/entities.json (built by build-entity-index.py) and prints how much
authored content exists for a handle plus its full file footprint. Use this BEFORE
grepping the whole raw/ tree.

  python3 scripts/whois.py <handle>
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "raw", "_index", "entities.json")

if len(sys.argv) < 2:
    print(__doc__); sys.exit(1)

h = sys.argv[1].lstrip("@").lower()
if not os.path.exists(IDX):
    print("no index yet — run: python3 scripts/build-entity-index.py"); sys.exit(1)

data = json.load(open(IDX))
e = data.get(h)
if not e:
    print(f"@{h}: NOT in index."); sys.exit(0)

print(f"@{h}: authored={e['authored_json']} (json) + {e['authored_txt']} (txt) "
      f"| footprint={e['footprint_files']} files")
for s in e.get("snippets", []):
    print(f"   • {s}")
for f in e.get("files", []):
    print(f"   - {f}")
