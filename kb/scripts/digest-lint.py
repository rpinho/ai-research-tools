#!/usr/bin/env python3
"""
digest-lint.py: the COMPILER for a scheduled digest. Deterministic and mandatory: the runner calls it
after the model writes the digest and before anything is delivered.

WHY. A length rule lived in the digest prompt for a week, and the digest shipped at more than twice the
limit anyway. Prose rules do not hold an LLM's output; a compiler does. The runner
(scripts/nightly/run.sh) lints, and on failure runs ONE repair pass with the lint report as the
prompt, then lints again. If the repair still fails, the digest is delivered anyway (silence is never
acceptable) and the failure is logged loudly.

HARD RULES (exit 2 on any failure):
  L1  total words              <= DIGEST_MAX_WORDS          (default 1000)
  L3  any section              <= DIGEST_MAX_SECTION_WORDS  (default 250)
  L4  any single row/paragraph <= DIGEST_MAX_ROW_WORDS      (default 70)
  L5  --ledger-pending FILE    every intraday entry (scripts/day-ledger.py pending --json) is BLENDED
                               in: one of its pages, authors or pin text appears somewhere
  L6  --must-mention REGEX     (repeatable) the digest must match it; the runner adds these from
                               deterministic detectors ("a source you always want covered posted tonight")
  L7  --order a,b,c            section kinds must appear in this order (kinds from KIND_RULES below);
                               default "today,read,other,asks". Unknown kinds count as "other".
  L10 permission-slip asks     a line in the Asks section requesting permission for work the agent
                               already has standing permission for (see AUTO_ALLOWED) fails: the fix
                               is to DO the thing and delete the ask, never to reword it
  S1  tool-leak fingerprints   citation artifacts pasted from other tools: zero tolerance

SOFT REPORT (never gates; density, not presence): AI-tell lexical score per 1k words, negative
parallelism ("not X but Y"), participle tails, tricolons, em-dash density, sentence-length spread.
A number in the run log gives the next prompt edit something to aim at. Style is reported, not gated,
because gating style makes the repair pass rewrite good prose into worse prose.

SECTIONS are found as headings: <h2>/<h3> in HTML, or "## " lines in Markdown. A block between
<!-- LINT:EXCLUDE:BEGIN --> and <!-- LINT:EXCLUDE:END --> (e.g. a shell-built index of items the
reader has already seen) is ignored by every cap and earns no must-mention credit.

USAGE
  scripts/digest-lint.py outbox/Digest-2026-01-15.html            # human report, exit 0/2
  scripts/digest-lint.py --json FILE                               # machine report
  scripts/digest-lint.py --ledger-pending pend.json --must-mention 'Some Name' FILE
"""
import argparse, html, json, os, re, statistics, sys, urllib.parse

MAX_WORDS = int(os.environ.get("DIGEST_MAX_WORDS", 1000))
MAX_SECTION_WORDS = int(os.environ.get("DIGEST_MAX_SECTION_WORDS", 250))
MAX_ROW_WORDS = int(os.environ.get("DIGEST_MAX_ROW_WORDS", 70))
EXCLUDE = re.compile(r"<!-- LINT:EXCLUDE:BEGIN -->.*?<!-- LINT:EXCLUDE:END -->", re.S)

# Section-name -> kind. Edit to match your digest's headings.
KIND_RULES = [
    ("today", r"^(do )?today\b|^due\b"),
    ("read", r"^(the )?read\b|^verdict|^summary\b|^tl;?dr"),
    ("unread", r"not yet (watched|read)|unread|to watch"),
    ("asks", r"^asks?\b|^questions? for you"),
]

LEAKS = [":contentReference[oaicite", "turn0search", "[cite: ", "[span_", "grok_render_citation",
         "【", "[attached_file:", "utm_source=chatgpt.com"]

# Standing permission: the agent does these without asking. An Ask naming one is a permission slip.
# Tune to YOUR standing permissions; keep real questions (purchases, sends, irreversible acts) passing.
AUTO_ALLOWED = re.compile(
    r"\b(?:should i|shall i|do you want me to|want me to|ok to|okay to|permission to|may i)\b|"
    r"\bre-?export\b|\bre-?run\b|\bre-?index\b|\broute (?:it|this|them) (?:in)?to\b|"
    r"\bwork (?:it|this|them) into\b|\badd (?:it|this|them) to the (?:\w+ )?pages?\b|"
    r"\bverify (?:it|this|the claim)\??$|\bchase (?:it|this)\??$",
    re.I)

# AI-tell vocabulary, weighted. Dated: this vocabulary turns over; refresh it every few months.
WORDLIST_VERSION = "2026-08"
TIER = {3: r"delv(e|es|ed|ing)|underscor(e|es|ed|ing)|showcas\w*|intricate|intricacies|meticulous(ly)?|tapestry|"
           r"palpable|\brealm\b|garnered|groundbreaking|testament|amidst",
        2: r"commendable|noteworthy|invaluable|pivotal|nuanced|multifaceted|interplay|bolster\w*|unveil\w*|crucial|"
           r"deep dive|robust|leverag\w*|seamless",
        1: r"\bpotential\b|\bcomprehensive\b|\bparticularly\b|foster\w*|\blandscape\b|\bnavigat\w*|\bvibrant\b"}
NEG_PARALLEL = r"\bnot (just|only|merely) [^.;:]{2,60}\bbut\b|\bit'?s not [^.;:]{2,60}, it'?s\b"
PARTICIPLE_TAIL = r",\s+(highlighting|underscoring|showcasing|emphasizing|reflecting|signaling|suggesting|demonstrating)\b[^.]*\."
TRICOLON = r"\b\w+, \w+,? and \w+\b"


def text_of(fragment):
    t = re.sub(r"<style.*?</style>|<script.*?</script>", " ", fragment, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def words(t):
    return len(t.split())


def kind_of(name):
    n = name.lower().strip()
    for kind, pat in KIND_RULES:
        if re.search(pat, n):
            return kind
    return "other"


def sections(src, is_html):
    """[(name, body)] split on headings."""
    if is_html:
        heads = list(re.finditer(r"<h[23][^>]*>(.*?)</h[23]>", src, flags=re.S | re.I))
    else:
        heads = list(re.finditer(r"^#{2,3}\s+(.*)$", src, flags=re.M))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(src)
        out.append((text_of(m.group(1)), src[m.end():end]))
    return out


def rows(body, is_html):
    if is_html:
        parts = re.findall(r"<(?:p|li|td)[^>]*>(.*?)</(?:p|li|td)>", body, flags=re.S)
        return [text_of(p) for p in parts if "<td" not in p]
    return [p.strip() for p in re.split(r"\n\s*\n|\n(?=\s*[-*] )", body) if p.strip()]


def lint(path, must_mention=(), ledger_pending=None, order="today,read,other,asks"):
    raw = open(path, encoding="utf-8", errors="replace").read()
    is_html = path.lower().endswith((".html", ".htm")) or "<html" in raw[:500].lower()
    src = EXCLUDE.sub(" ", raw)
    full = text_of(src) if is_html else re.sub(r"\s+", " ", src)
    total = words(full)
    hard, rep = [], {"file": path, "words": total, "caps": [MAX_WORDS, MAX_SECTION_WORDS, MAX_ROW_WORDS]}

    if total > MAX_WORDS:
        hard.append(f"L1 total {total} words > {MAX_WORDS}")
    for leak in LEAKS:
        if leak in raw:
            hard.append(f"S1 tool-leak fingerprint present: {leak!r}")

    secs = sections(src, is_html)
    rep["sections"] = {}
    for name, body in secs:
        w = words(text_of(body) if is_html else body)
        rep["sections"][name] = w
        if w > MAX_SECTION_WORDS:
            hard.append(f"L3 section '{name}' {w} words > {MAX_SECTION_WORDS}")
        for r in rows(body, is_html):
            if words(r) > MAX_ROW_WORDS:
                hard.append(f"L4 row {words(r)} words > {MAX_ROW_WORDS}: \"{r[:70]}...\"")

    # L7 order
    want = [k.strip() for k in order.split(",") if k.strip()]
    seen = [kind_of(n) for n, _ in secs]
    rep["section_kinds"] = seen
    rank = {k: i for i, k in enumerate(want)}
    last = -1
    for (name, _), k in zip(secs, seen):
        r = rank.get(k, rank.get("other", len(want)))
        if r < last:
            hard.append(f"L7 section '{name}' ({k}) is out of order; expected order {' > '.join(want)}")
        last = max(last, r)

    # L6 must-mention
    for pat in must_mention:
        if not re.search(pat, full, re.I):
            hard.append(f"L6 required mention missing: /{pat}/")

    # L10 permission slips in Asks
    rep["permission_slips"] = []
    for name, body in secs:
        if kind_of(name) != "asks":
            continue
        for r in rows(body, is_html):
            if AUTO_ALLOWED.search(r):
                rep["permission_slips"].append(r[:120])
                hard.append(f"L10 Ask requests permission already given (do it, then delete the ask): \"{r[:100]}\"")

    # L5 intraday ledger blended in
    entries, missing = [], []
    if ledger_pending:
        try:
            entries = json.load(open(ledger_pending, encoding="utf-8"))
        except Exception as e:
            hard.append(f"L5 could not read ledger file {ledger_pending}: {e}")
    hay = (full + " " + html.unescape(urllib.parse.unquote(src))).lower()
    for e in entries:
        keys = [k for k in (e.get("pages") or []) + (e.get("authors") or []) if k]
        keys += [k.split(" - ")[0].strip() for k in keys if " - " in k and len(k.split(" - ")[0].strip()) >= 3]
        if (e.get("pins") or "").strip():
            keys.append(e["pins"].strip()[:40])
        if keys and not any(k.lower() in hay for k in keys):
            missing.append({"ts": e.get("ts"), "source": e.get("source"), "keys": keys[:4]})
    rep["ledger_entries"], rep["ledger_missing"] = len(entries), missing
    rep["ledger_ok"] = not missing
    for m in missing:
        hard.append(f"L5 intraday entry not blended in ({m['source']} {str(m['ts'])[:16]}): mention one of {m['keys']}")

    # soft style report
    per_k = 1000 / max(total, 1)
    lex, hits = 0, {}
    for wgt, pat in TIER.items():
        found = [m.group(0).lower() for m in re.finditer(pat, full, flags=re.I)]
        if found:
            hits[str(wgt)] = sorted(set(found))[:12]
            lex += wgt * len(found)
    sents = [x for x in re.split(r"(?<=[.!?])\s+", full) if words(x) > 2]
    lens = [words(x) for x in sents] or [0]
    rep["style"] = {
        "wordlist_version": WORDLIST_VERSION,
        "lexical_score_per_1k": round(lex * per_k, 1), "lexical_hits": hits,
        "negative_parallelism": len(re.findall(NEG_PARALLEL, full, flags=re.I)),
        "participle_tails": len(re.findall(PARTICIPLE_TAIL, full, flags=re.I)),
        "tricolons": len(re.findall(TRICOLON, full)),
        "em_dashes_per_1k": round(full.count("—") * per_k, 1),
        "sentences": len(sents), "sentence_len_mean": round(statistics.mean(lens), 1),
        "sentence_len_stdev": round(statistics.pstdev(lens), 1) if len(lens) > 1 else 0,
    }
    rep["hard_fails"] = hard
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--must-mention", action="append", default=[], help="regex that must appear (repeatable)")
    ap.add_argument("--ledger-pending", help="JSON from `day-ledger.py pending --json` (L5)")
    ap.add_argument("--order", default=os.environ.get("DIGEST_ORDER", "today,read,other,asks"))
    a = ap.parse_args()
    r = lint(a.file, a.must_mention, a.ledger_pending, a.order)
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"digest-lint: {r['words']} words (cap {MAX_WORDS}); kinds {' > '.join(r['section_kinds']) or '-'}")
        for k, v in r["sections"].items():
            print(f"  section {k!r}: {v} words" + ("  (over cap)" if v > MAX_SECTION_WORDS else ""))
        if r["ledger_entries"]:
            print(f"  ledger: {r['ledger_entries']} intraday entries, {len(r['ledger_missing'])} not blended in")
        st = r["style"]
        print(f"  style: lexical {st['lexical_score_per_1k']}/1k {st['lexical_hits']} - neg-parallel "
              f"{st['negative_parallelism']} - participle-tails {st['participle_tails']} - tricolons {st['tricolons']} - "
              f"em-dash {st['em_dashes_per_1k']}/1k - sentences {st['sentences']} mean {st['sentence_len_mean']} "
              f"sd {st['sentence_len_stdev']}")
        if r["hard_fails"]:
            print("  HARD FAILS:")
            for h in r["hard_fails"]:
                print("    -", h)
        else:
            print("  passes the hard rules")
    sys.exit(2 if r["hard_fails"] else 0)


if __name__ == "__main__":
    main()
