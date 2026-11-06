#!/usr/bin/env python3
"""
state-hygiene.py: close state.md items that a NEWER entry already resolved. Run by the scheduled job,
before the digest reads state.md.

WHY. The digest surfaced "book the repair for X" two days after a closed item recorded X as fixed.
The only guard was a prose rule in the nightly prompt ("dedupe before surfacing"), and prose does
not hold. Text overlap alone cannot tell "resolved by" from "shares words with", so this is two
deterministic steps around one judgment:

  1. --candidates OUT.json   every (open, closed) pair that shares rarity-weighted words (and the same
                             leading marker, if your items start with one). High recall, many false
                             pairs. Each pair carries a stable id.
  2. the scheduled model judges each pair and writes DECISIONS.json:
                             [{"id": "...", "close": true|false, "why": "..."}]
  3. --apply DECISIONS.json  closes ONLY pairs judged close=true, appending the reason. Never deletes text.

A "closed" item is "- [x]" with a completion word (DONE, CLOSED, PAID, FIXED, RETURNED, ...).
"""
import collections, datetime as dt, hashlib, json, os, pathlib, re, sys

ROOT = pathlib.Path(os.environ.get("KB_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
STATE = ROOT / "state.md"
STOP = set("this that with from have been will your what when then they them their there were also into than "
           "only just more most over some such very after before about again still while which would could "
           "should today tomorrow done same".split())
DONE = re.compile(r"✅|\b(CLOSED|DONE|REPLACED|PAID|RETURNED|FIXED|SUBMITTED|MAILED|RESOLVED|CANCELLED)\b")
MIN_SCORE = float(os.environ.get("HYGIENE_MIN_SCORE", 0.6))


def words(s):
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", s)} - STOP


def marker(s):
    """The item's leading symbol run (e.g. an emoji category), if any; pairs must share it."""
    m = re.match(r"- \[[ x]\] \**([^\w\s*\[\]]+)", s)
    return m.group(1)[:2] if m else ""


def pid(a, b):
    return hashlib.sha1((a[:200] + "|" + b[:200]).encode()).hexdigest()[:12]


def candidates():
    items = [l for l in STATE.read_text(encoding="utf-8").split("\n") if l.startswith("- [")]
    df = collections.Counter(w for l in items for w in words(l[:600]))
    closed = [l for l in items if l.startswith("- [x] ") and DONE.search(l[:400])]
    out = []
    for l in items:
        if not l.startswith("- [ ] "):
            continue
        best = []
        for c in closed:
            if marker(c) != marker(l):
                continue
            shared = words(l[:600]) & words(c[:600])
            score = sum(1 / df[w] for w in shared)
            if score >= MIN_SCORE:
                best.append((score, c, sorted(shared, key=lambda w: df[w])[:8]))
        for score, c, shared in sorted(best, reverse=True)[:2]:
            out.append({"id": pid(l, c), "open": l[:500], "closed": c[:500], "shared": shared, "score": round(score, 2)})
    return out


def apply(decisions):
    yes = {d["id"]: d for d in decisions if d.get("close")}
    by_id = {p["id"]: p for p in candidates()}
    lines = STATE.read_text(encoding="utf-8").split("\n")
    n = 0
    for i, l in enumerate(lines):
        if not l.startswith("- [ ] "):
            continue
        for d_id, d in yes.items():
            p = by_id.get(d_id)
            if p and l[:500] == p["open"]:
                lines[i] = l.replace("- [ ] ", "- [x] ", 1) + f" (auto-closed {dt.date.today()} by state-hygiene: {d.get('why', 'superseded')})"
                n += 1
                print("CLOSED:", l[:100])
                break
    STATE.write_text("\n".join(lines), encoding="utf-8")
    print(f"{n} closed")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--candidates"] and len(sys.argv) > 2:
        c = candidates()
        pathlib.Path(sys.argv[2]).write_text(json.dumps(c, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{len(c)} candidate pairs -> {sys.argv[2]}")
    elif sys.argv[1:2] == ["--apply"] and len(sys.argv) > 2:
        apply(json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        print(__doc__)
        sys.exit(2)
