#!/usr/bin/env python3
"""
day-ledger.py: the INTRADAY LEDGER that makes daytime ingests visible to the next scheduled digest.

THE PROBLEM. A scheduled run (nightly digest) writes up what IT processed. Anything ingested in an
interactive session during the day (a pasted thread, a transcription, a batch you ran by hand) has no
route into that digest. Worse: if a daytime session drains a queue the nightly would have read, the
digest goes blind to the day. The digest has to be STATEFUL: sessions and the scheduled run must
coordinate through a file, not through anyone's memory.

THE MECHANISM (architecture, not prose):
  * Every ingest ends with ONE `day-ledger.py add ...` line: JSONL, append-only.
  * The scheduled run reads `day-ledger.py pending --json` BEFORE writing the digest and blends each
    entry into the section it belongs to (no separate "since yesterday" dump).
  * scripts/digest-lint.py --ledger-pending FILE hard-fails (L5) any entry whose pages / authors / pin
    text appear nowhere in the digest. --pages and --authors are the keys it matches on: always pass them.
  * The SHELL (not the model) consumes the ledger with a marker line, and only after a digest that
    passes L5 exists. Pending = everything after the last marker, so an unconsumed entry rolls into
    the next digest instead of vanishing.
  * .githooks/pre-commit refuses a commit that ADDS raw/ files without staging this ledger
    (KB_NIGHTLY=1 exempts the consumer; KB_NO_LEDGER=1 is the explicit backfill override).
  * The file is merge=union (.gitattributes), so parallel worktrees never conflict on it.

FILE: raw/_index/day-ledger.jsonl  (raw/_index/ is metadata, exempt from raw immutability)

USAGE
  day-ledger.py add --source transcription --summary "2h podcast on X; three claims routed" \\
      [--items 1] [--authors "alice,bob"] [--stance "one line on what it changes"] \\
      [--pages "Page Title A;Page Title B"] [--pins "one line the digest MUST carry"]
  day-ledger.py pending [--md|--json]        # entries since the last consume marker
  day-ledger.py count                        # number of pending entries (for the shell)
  day-ledger.py consume --digest YYYY-MM-DD  # append the marker (scheduled run only)

Suggested --source values: ingest, paste, screenshot, transcription, handoff, manual.
"""
import argparse, datetime, json, os, sys
from pathlib import Path

ROOT = Path(os.environ.get("KB_ROOT") or Path(__file__).resolve().parent.parent)
LEDGER = ROOT / "raw" / "_index" / "day-ledger.jsonl"


def load():
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"day-ledger: skipping malformed line: {line[:80]}", file=sys.stderr)
    return out


def append(obj):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def pending(entries):
    last = -1
    for i, e in enumerate(entries):
        if e.get("type") == "consumed":
            last = i
    return [e for e in entries[last + 1:] if e.get("type") == "entry"]


def split_list(s, seps):
    """pages split on ';' only (titles contain commas); authors on ';' or ','."""
    out = [s or ""]
    for sep in seps:
        out = [piece for chunk in out for piece in chunk.split(sep)]
    return [x.strip() for x in out if x.strip()]


def to_md(entries):
    if not entries:
        return "(no intraday entries pending)"
    lines = []
    for e in entries:
        bits = [f"**{e['ts'][:16].replace('T', ' ')}** - {e['source']}"]
        if e.get("items"):
            bits.append(f"{e['items']} items")
        lines.append("- " + " - ".join(bits) + f": {e['summary']}")
        if e.get("authors"):
            lines.append(f"  - authors: {', '.join(e['authors'])}")
        if e.get("stance"):
            lines.append(f"  - stance: {e['stance']}")
        if e.get("pages"):
            lines.append("  - pages: " + ", ".join(f"[[{p}]]" for p in e["pages"]))
        if e.get("pins"):
            lines.append(f"  - MUST carry: {e['pins']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--source", required=True)
    a.add_argument("--summary", required=True)
    a.add_argument("--items", type=int)
    a.add_argument("--authors")
    a.add_argument("--stance")
    a.add_argument("--pages")
    a.add_argument("--pins")
    p = sub.add_parser("pending")
    p.add_argument("--md", action="store_true")
    p.add_argument("--json", action="store_true")
    c = sub.add_parser("consume")
    c.add_argument("--digest", required=True)
    sub.add_parser("count")
    args = ap.parse_args()

    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    if args.cmd == "add":
        e = {"type": "entry", "ts": now, "source": args.source, "summary": args.summary.strip()}
        if args.items is not None:
            e["items"] = args.items
        for k, seps in (("authors", ";,"), ("pages", ";")):
            v = split_list(getattr(args, k), seps)
            if v:
                e[k] = v
        for k in ("stance", "pins"):
            v = getattr(args, k)
            if v:
                e[k] = v.strip()
        if not (e.get("pages") or e.get("authors") or e.get("pins")):
            print("day-ledger: warning: no --pages/--authors/--pins, so the digest lint cannot check this entry",
                  file=sys.stderr)
        append(e)
        print(f"day-ledger: added ({len(pending(load()))} pending)")
    elif args.cmd == "pending":
        pend = pending(load())
        print(json.dumps(pend, indent=2, ensure_ascii=False) if args.json else to_md(pend))
    elif args.cmd == "count":
        print(len(pending(load())))
    elif args.cmd == "consume":
        n = len(pending(load()))
        append({"type": "consumed", "ts": now, "digest": args.digest, "n": n})
        print(f"day-ledger: consumed {n} entries into digest {args.digest}")


if __name__ == "__main__":
    main()
