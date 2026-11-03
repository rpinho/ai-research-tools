#!/usr/bin/env python3
"""Deterministically extract model and benchmark references from a user-supplied bookmark archive. Prints coverage denominators; outputs are local private working files."""
import argparse, collections, glob, json, os, re, sys
from pathlib import Path

KB = None

# A tweet is "about models" if it names a model/benchmark in a computing sense.
MODEL = re.compile(
    r"swe-?bench|terminal-?bench|tau-?bench|τ-bench|livebench|arc-agi|aider\b|vulcanbench|artificial analysis|"
    r"\bastra\b|claude fable|\bfable [0-9]|\bfable\b(?=.{0,40}(model|claude|anthropic|opus|sonnet|token))|"
    r"\bmythos\b|opus [45]|sonnet [45]|deepseek|\bkimi\b|glm-?\d|\bqwen\b|grok ?[45]|gemini ?[23]|"
    r"gpt-?[56]|5\.6 (sol|terra|luna)|gpt-?6 (sol|luna)|opus 5\.5|sonnet 5\.5|\b(sol|terra|luna) (model|tier)\b|"
    r"(?:model|llm|coding|agentic|harness).{0,30}benchmark|benchmark.{0,30}(?:model|llm|coding|agentic)|"
    r"per million tokens|\$/task|tokens per second|context window",
    re.I)

# An archive may also contain non-model uses of model-like tickers. Drop crypto tweets unless they ALSO name a lab model.
CRYPTO = re.compile(r"\bsolana\b|\$sol\b|\bmemecoin|\bairdrop|\bdegen|pump\.fun|\bmarket cap|\bmcap\b|"
                    r"\bticker\b|\bwallet\b|\bchart\b|\bape[ds]?\b|\bbags?\b|\bDEX\b", re.I)
LAB = re.compile(r"swe-?bench|opus [45]|claude|anthropic|openai|gpt-?[56]|deepseek|gemini ?[23]|"
                 r"grok ?[45]|\bkimi\b|glm-?\d|\bqwen\b|\bastra\b|\bmythos\b", re.I)

# "Hard number" = something you could put in a price or benchmark column.
NUM = re.compile(r"\$\s?\d|\d+(?:\.\d+)?\s?%|\d+(?:\.\d+)?\s*(?:/|per)\s*(?:m\b|million|task)|"
                 r"\d+\s*(?:bn|billion|k|m)\s*tokens|\d{2,}\s*turns", re.I)


def scan_bookmarks(since):
    seen, rows = set(), []
    files = sorted(glob.glob(str(KB / "raw" / "bookmarks" / "*.json")))
    total = 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        users = {u["id"]: u.get("username", "?") for u in d.get("includes", {}).get("users", [])}
        for t in d.get("data", []):
            total += 1
            tid = t.get("id")
            if not tid or tid in seen:
                continue
            txt = (t.get("note_tweet") or {}).get("text") or t.get("text", "")
            if not MODEL.search(txt):
                continue
            if CRYPTO.search(txt) and not LAB.search(txt):
                continue
            date = (t.get("created_at") or "")[:10]
            if since and date and date < since:
                continue
            seen.add(tid)
            rows.append({"id": tid, "date": date, "author": users.get(t.get("author_id"), "?"),
                         "num": bool(NUM.search(txt)), "src": os.path.basename(f), "text": txt})
    return rows, len(files), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.environ.get("TMPDIR", "/tmp") + "/model-mine")
    ap.add_argument("--since", default=None, help="ignore items before this YYYY-MM-DD")
    ap.add_argument("--archive", type=Path, required=True, help="root containing raw/bookmarks/*.json")
    a = ap.parse_args()
    global KB
    KB = a.archive.expanduser().resolve()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    rows, nfiles, ntweets = scan_bookmarks(a.since)
    rows.sort(key=lambda r: r["date"])
    withnum = [r for r in rows if r["num"]]

    def dump(rs, stem):
        json.dump(rs, open(out / f"{stem}.json", "w"))
        with open(out / f"{stem}.txt", "w") as fh:
            for r in rs:
                fh.write(f"=== {r['date']} @{r['author']} ({r['id']})\n{r['text']}\n\n")

    dump(rows, "model_all")
    dump(withnum, "model_numbers")

    # monthly slices, so a processing pass can fan out with guaranteed full coverage
    by_month = collections.defaultdict(list)
    for r in rows:
        by_month[r["date"][:7] or "undated"].append(r)
    for month, rs in sorted(by_month.items()):
        dump(rs, f"slice_{month}")

    # THE CENSUS — this is the point of the script: a future run cannot silently under-read.
    print(f"model-mine: scanned {nfiles} bookmark files / {ntweets:,} tweets")
    print(f"            {len(rows):,} about models or benchmarks ({len(rows)/max(ntweets,1)*100:.1f}%)")
    print(f"            {len(withnum):,} of those carry a hard number (price / score / tokens)")
    print(f"            slices: {', '.join(sorted(by_month))}")
    print(f"            → {out}")
    top = collections.Counter(r["author"] for r in withnum).most_common(10)
    print("            top number-bearing authors: " + ", ".join(f"@{a}({n})" for a, n in top))


if __name__ == "__main__":
    main()
