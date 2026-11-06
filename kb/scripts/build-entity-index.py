#!/usr/bin/env python3
"""Build a complete entity (author/handle) index across ALL of raw/.

Answers "do we have @X, and how much?" instantly, without grepping the whole tree
every time. Covers BOTH structured JSON sources (author_id -> handle via includes.users)
AND plain-text compilations (where handles appear as paste markers or @mentions), which
a JSON-only scan would miss.

Output: raw/_index/entities.json  ->  { handle_lower: {authored_json, authored_txt, footprint_files, files, snippets} }
Run after any ingest:  python3 scripts/build-entity-index.py
Query:                 python3 scripts/whois.py <handle>
"""
import json, re, os, glob
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
idx = defaultdict(lambda: {"authored_json": 0, "authored_txt": 0, "files": set(), "snippets": []})

def rel(p):
    return os.path.relpath(p, ROOT)

# --- JSON sources: author_id -> handle via includes.users ---
for f in glob.glob(os.path.join(RAW, "**", "*.json"), recursive=True):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    users = {x["id"]: x.get("username", "") for x in d.get("includes", {}).get("users", [])}
    for t in d.get("data", []):
        if not isinstance(t, dict):
            continue
        h = users.get(t.get("author_id"), "").lower()
        txt = (t.get("note_tweet", {}) or {}).get("text") or t.get("text", "")
        if h:
            e = idx[h]
            e["authored_json"] += 1
            e["files"].add(rel(f))
            if len(e["snippets"]) < 3 and txt:
                e["snippets"].append(txt.replace("\n", " ")[:160])
        for m in set(re.findall(r"@(\w{2,30})", txt)):   # mentions -> findable even where only quoted
            idx[m.lower()]["files"].add(rel(f))

# --- Text sources: authored markers + @mentions ---
AUTH_TXT = re.compile(r"\[@(\w{2,30})\]")            # compilation marker: [@handle]
PASTE = re.compile(r"(?m)^@(\w{2,30})\s*\n[.·•]")  # copy-paste header: "@handle\n·"
MENTION = re.compile(r"@(\w{2,30})")
for f in glob.glob(os.path.join(RAW, "**", "*.txt"), recursive=True):
    try:
        t = open(f, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    for h in AUTH_TXT.findall(t) + PASTE.findall(t):
        e = idx[h.lower()]; e["authored_txt"] += 1; e["files"].add(rel(f))
    for h in set(MENTION.findall(t)):
        idx[h.lower()]["files"].add(rel(f))

out = {h: {"authored_json": v["authored_json"], "authored_txt": v["authored_txt"],
           "footprint_files": len(v["files"]), "files": sorted(v["files"]),
           "snippets": v["snippets"]}
       for h, v in idx.items()}
os.makedirs(os.path.join(RAW, "_index"), exist_ok=True)
json.dump(out, open(os.path.join(RAW, "_index", "entities.json"), "w"),
          ensure_ascii=False, separators=(",", ":"), sort_keys=True)
tot = sum(1 for v in out.values() if v["authored_json"] or v["authored_txt"])
print(f"indexed {len(out)} handles ({tot} with authored content) -> raw/_index/entities.json")
