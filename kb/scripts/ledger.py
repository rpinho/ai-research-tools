#!/usr/bin/env python3
"""Idempotent processed-set ledger — makes ingestion resumable and re-runnable.

The idea: never re-process what you've already done. When you pull a batch of items
(each with an "id"), `filter` drops the ones already recorded, you process only the
NEW ones, and `add` records them — LAST, only after processing, so a crash never
marks unprocessed work as done.

Works on any JSON file shaped like:  {"data": [{"id": "...", ...}, ...]}

  python3 scripts/ledger.py filter <pulled.json> [out.json]  # write NEW-only items + print stats
  python3 scripts/ledger.py allseen <pulled.json>            # exit 0 if EVERY id is already known
  python3 scripts/ledger.py add <file.json>                  # record that file's ids (run AFTER processing)
  python3 scripts/ledger.py count                            # print ledger size
"""
import json, sys, os

LED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "raw", "_index", "processed.json")

def load():
    if not os.path.exists(LED):
        return {"processed_ids": [], "count": 0}, set()
    d = json.load(open(LED))
    return d, set(d.get("processed_ids", []))

def save(d, ids):
    d["processed_ids"] = sorted(ids)
    d["count"] = len(ids)
    os.makedirs(os.path.dirname(LED), exist_ok=True)
    json.dump(d, open(LED, "w"), separators=(",", ":"))

def ids_of(f):
    d = json.load(open(f))
    return d, [t["id"] for t in d.get("data", []) if "id" in t]

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "count":
    _, s = load(); print(len(s))
elif cmd == "filter":
    _, seen = load(); d, allids = ids_of(sys.argv[2])
    new = [t for t in d["data"] if t.get("id") not in seen]
    d["data"] = new
    out = sys.argv[3] if len(sys.argv) > 3 else None
    if out:
        json.dump(d, open(out, "w"), separators=(",", ":"))
    print(f"pulled={len(allids)} already_processed={len(allids)-len(new)} NEW={len(new)}"
          + (f" -> {out}" if out else ""))
elif cmd == "allseen":
    _, seen = load(); _, allids = ids_of(sys.argv[2])
    sys.exit(0 if allids and all(i in seen for i in allids) else 1)
elif cmd == "add":
    d, seen = load(); _, allids = ids_of(sys.argv[2])
    before = len(seen); seen.update(allids); save(d, seen)
    print(f"added {len(seen)-before} new ids (ledger now {len(seen)})")
else:
    print(__doc__); sys.exit(1)
