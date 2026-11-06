#!/usr/bin/env python3
"""
coherence.py: a deterministic pass that finds the OBVIOUS links the KB forgot to make.

Not a knowledge graph. No similarity, no embeddings, no "related pages". Every check fires only
when it can NAME the shared noun; if it cannot, it stays quiet. The output is a review queue, never
a gate.

WHY. Two files about one event, one of which knew something the other didn't:
  - an open item in state.md named two things together; the wiki page that "owns" the first never
    mentioned the second, and a document written from that page was wrong (check 1);
  - an open item said "if X has NOT arrived by <date>, chase it" for weeks after a wiki page had
    recorded X arriving, and the digest kept nagging (check 4);
  - a correction was recorded on one page ("Corrected YYYY-MM-DD: A did NOT do B") while state.md
    and the index kept stating the old claim for weeks, and a session repeated it (check 5).

CHECKS
  1. entity-join  an open state.md item names entities A and B; A's owning page never mentions B
  2. unlinked     a page contains another page's exact (long) title without a [[wikilink]]
  3. stale-open   an item is still "- [ ]" but its own first line says DONE / CLOSED / RESOLVED
  4. waiting      an open item is WAITING on something ("awaiting", "has not arrived", "watch for")
                  while a wiki page records the same IDENTIFIER (letters+digits: order no., plate,
                  ticket id) next to an explicit completion marker
  5. corrections  "Corrected YYYY-MM-DD ... <Name> did NOT <verb>" in one file, while another file in
                  the same folder (or state.md / index.md) still says <Name> ... <verb>

Configure OWNER below (entity -> owning page) for the entities that matter most to you; without it,
check 1 still works for entities that appear in page titles.

OUTPUT  outbox/Coherence Report - YYYY-MM-DD.md (+ one summary line on stdout). Nothing is edited.
USAGE   python3 scripts/coherence.py [--date YYYY-MM-DD] [--top N] [--corrections-only]
        --corrections-only prints check 5 only and exits 1 if anything is flagged (nightly use).
"""
import argparse, datetime, os, re, sys
from collections import defaultdict
from pathlib import Path

KB = Path(os.environ.get("KB_ROOT", Path(__file__).resolve().parent.parent))

# Tokens that look like entities but are noise. Extend with your own jargon.
STOP = set("""KB PDF HTML JSON API URL CSS PNG JPG CSV ID IDS OK YES NO NOT AND THE FOR NEW OLD ALL ANY ONE TWO
PM AM ET UTC EOD TODO DONE OPEN NEXT WATCH NOTE ASK ASKS AI ML LLM GPU CPU RAM SSD USB USD EUR GBP
LOW HIGH MED MEDIUM UP DOWN READ ROW ROWS CARD DIGEST NIGHTLY PENDING VERIFIED CONFIRMED README WIP FAQ RSS
MON TUE WED THU FRI SAT SUN JAN FEB MAR APR MAY JUN JUL AUG SEP SEPT OCT NOV DEC""".split())

# Entities whose owning page is not literally titled with the token, e.g.
#   "TAXES": ["wiki/finance/Finance - Action Plan.md"],
OWNER = {}

ACRO = re.compile(r"\b[A-Z][A-Z0-9-]{1,}[A-Z0-9]\b")


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def wiki_pages():
    return {p.relative_to(KB).as_posix(): read(p) for p in (KB / "wiki").rglob("*.md")}


def open_items(state_text):
    """[(section, first_line, full_text)] for every unchecked item, with its indented continuation."""
    out, section, cur, sec_cur, buf = [], "", None, "", []
    def flush():
        if cur is not None:
            out.append((sec_cur, cur, "\n".join(buf)))
    for ln in state_text.splitlines():
        s = ln.strip()
        if ln.startswith("#"):
            flush(); cur, buf = None, []
            section = ln.strip("# ").strip()
        elif s.startswith("- [ ]"):
            flush(); cur, buf, sec_cur = s[5:].strip(), [s], section
        elif s.startswith("- ["):
            flush(); cur, buf = None, []
        elif cur is not None and ln[:1] in (" ", "\t") and s:
            buf.append(s)
        elif cur is not None and s:
            flush(); cur, buf = None, []
    flush()
    return out


# ---------------------------------------------------------------- 1. entity join
def check_entity_join(state_text, pages):
    titles_by_key = defaultdict(list)
    for rel in pages:
        for tok in ACRO.findall(Path(rel).stem):
            if tok.upper() not in STOP:
                titles_by_key[tok.upper()].append(rel)
    flags = []
    for section, first, full in open_items(state_text):
        ents = {m.group(0).upper() for m in ACRO.finditer(full)}
        ents = {e for e in ents if e not in STOP and not e.isdigit() and len(e) <= 24 and (e in OWNER or e in titles_by_key)}
        if len(ents) < 2:
            continue
        for a in ents:
            for rel in [r for r in OWNER.get(a, titles_by_key.get(a, [])) if r in pages]:
                missing = [b for b in ents if b != a and not re.search(re.escape(b), pages[rel], re.I)]
                if missing:
                    flags.append((section, first[:110], a, rel, missing))
    return flags


# ---------------------------------------------------------------- 2. unlinked mentions
def check_unlinked(pages):
    def prose(text):   # file paths, code and existing links are references, not unlinked mentions
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", " ", text)
        text = re.sub(r"\[[^\]\n]+\]\([^)\n]+\)", " ", text)
        return re.sub(r"https?://\S+|\S+\.md\b", " ", text)
    titles = [(Path(r).stem, r) for r in pages]
    titles = [(t, r) for t, r in titles if len(t) >= 14 and len(t.split()) >= 3]
    body = {rel: prose(t) for rel, t in pages.items()}
    low = {rel: t.lower() for rel, t in body.items()}
    flags = []
    for t, r in titles:
        pat = re.compile(r"(?<!\[\[)" + re.escape(t) + r"(?!\]\])", re.I)
        for rel, text in body.items():
            if rel == r or t.lower() not in low[rel]:
                continue
            n = len(pat.findall(text))
            if n and ("[[" + t.lower()) not in text.lower():
                flags.append((rel, t, n))
    return flags


# ---------------------------------------------------------------- 3. stale open items
def check_stale(state_text):
    done = re.compile(r"\b(DONE|CLOSED|RESOLVED|COMPLETED?)\b[.:!*]", re.I)
    return [(sec, first[:140]) for sec, first, _ in open_items(state_text) if done.search(first[:200])]


# ---------------------------------------------------------------- 4. waiting vs answered
WAITING = re.compile(
    r"has(?:n'|’)?t\s+(?:yet\s+)?(?:arrived|come|shown up|cleared|been received)|"
    r"\bnot\s+(?:yet\s+)?(?:arrived|received|issued|delivered)|\bawaiting\b|\bstill waiting\b|"
    r"\bwatch for\b|\bwaiting (?:on|for)\b|ha(?:s|ve) NOT .{0,40}\bby\b|\bchase (?:it|them)\b", re.I)
RESOLVED = re.compile(r"✅|\b(?:ARRIVED|RECEIVED|DELIVERED|REFUNDED|CLEARED|SETTLED|ISSUED|COLLECTED|IN HAND)\b")
IDENT = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{5,24}\b")
MAX_PAGES_PER_IDENT = 4   # an identifier on half the wiki is not an identifier


def _idents(text):
    return {t.upper() for t in IDENT.findall(text) if not re.fullmatch(r"\d+[A-Za-z]{0,2}", t) and t.upper() not in STOP}


def check_waiting(state_text, pages):
    where = defaultdict(set)
    for rel, text in pages.items():
        for t in _idents(text):
            where[t].add(rel)
    flags, seen = [], set()
    for section, first, full in open_items(state_text):
        if not WAITING.search(full):
            continue
        for ident in sorted(_idents(full)):
            rels = where.get(ident, ())
            if not rels or len(rels) > MAX_PAGES_PER_IDENT:
                continue
            for rel in sorted(rels):
                for m in re.finditer(re.escape(ident), pages[rel], re.I):
                    ctx = pages[rel][max(0, m.start() - 250): m.end() + 250]
                    if RESOLVED.search(ctx) and (first[:60], ident, rel) not in seen:
                        seen.add((first[:60], ident, rel))
                        flags.append((section, first[:110], ident, rel, " ".join(ctx.split())[:180]))
                        break
    return flags


# ---------------------------------------------------------------- 5. corrections not propagated
CORR = re.compile(r"\b(?:Corrected|CORRECTED|Correction|CORRECTION)\b[^\n]{0,80}?20\d\d-\d\d-\d\d")
NEG_SV = re.compile(r"\b([A-Z][a-z]{2,})\b(?:\s+[\w'’]+){0,2}?\s+(?:did\s+NOT|did\s+not|didn['’]t|never)\s+([a-z]{4,})")
NOT_NAMES = set("We He She It They This That There The His Her Our Their You Note Both None Nobody".split())
ALREADY = re.compile(r"\bnot\b|\bnever\b|\bvoid\b|\bcorrect(?:ed|ion)\b|didn['’]t|did not|supersed|cancel|"
                     r"instead|no longer|wasn['’]t|isn['’]t|stale|~~", re.I)
NEAR = 100


def _same_domain(src, rel):
    hubs = ("state.md", "index.md")
    if src in hubs:
        return rel in hubs
    return rel in hubs or Path(src).parts[:2] == Path(rel).parts[:2]


def check_corrections(state_text, pages, index_text):
    files = dict(pages, **{"state.md": state_text, "index.md": index_text})
    flags, seen = [], set()
    for src, text in files.items():
        for line in text.splitlines():
            if not CORR.search(line):
                continue
            for m in NEG_SV.finditer(line[:1500]):
                name, verb = m.group(1), m.group(2)
                if name in NOT_NAMES:
                    continue
                base = verb
                for suf in ("ing", "ed", "es", "s", "e"):
                    if base.endswith(suf) and len(base) - len(suf) >= 4:
                        base = base[: -len(suf)]
                        break
                stem = re.escape(base) + r"\w{0,4}\b"
                pat = re.compile(rf"\b{re.escape(name)}\b.{{0,60}}?\b{stem}|\b{stem}.{{0,60}}?\b{re.escape(name)}\b", re.I)
                for rel, other in files.items():
                    if rel == src or not _same_domain(src, rel):
                        continue
                    for ln in other.splitlines():
                        for hit in pat.finditer(ln):
                            window = ln[max(0, hit.start() - NEAR): hit.end() + NEAR]
                            if not ALREADY.search(window) and (rel, ln[:80]) not in seen:
                                seen.add((rel, ln[:80]))
                                flags.append((src, f"{name} did not {verb}", rel,
                                              " ".join(ln[max(0, hit.start() - 90): hit.end() + 110].split())))
                                break
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--corrections-only", action="store_true")
    a = ap.parse_args()
    state = read(KB / "state.md") if (KB / "state.md").exists() else ""
    index_text = read(KB / "index.md") if (KB / "index.md").exists() else ""
    pages = wiki_pages()
    cr = check_corrections(state, pages, index_text)
    if a.corrections_only:
        for src, claim, rel, ln in cr:
            print(f"- `{rel}` still says: \"{ln}\"  <- contradicts the correction in `{src}` ({claim})")
        print(f"coherence/corrections: {len(cr)} unpropagated correction(s)")
        sys.exit(1 if cr else 0)
    ej, ul, st, wt = check_entity_join(state, pages), check_unlinked(pages), check_stale(state), check_waiting(state, pages)
    ul.sort(key=lambda f: -f[2])
    T = a.top
    out = [f"# Coherence Report - {a.date}", "",
           f"Deterministic pass over state.md ({len(open_items(state))} open items) and {len(pages)} wiki pages. "
           "Nothing was edited. Each line names the shared noun; if a rule can't, it doesn't fire.", "",
           f"## 1. Entity join - {len(ej)} gap(s)", ""]
    out += [f"- **[{s}]** *\"{f}...\"* names **{e}** and {', '.join(sorted(m))}; `{r}` never mentions {', '.join(sorted(m))}."
            for s, f, e, r, m in ej[:T]]
    out += ["", f"## 2. Unlinked mentions - {len(ul)}", ""]
    out += [f"- `{r}` mentions **{t}** {n}x: add `[[{t}]]`." for r, t, n in ul[:T]]
    out += ["", f"## 3. Stale open items - {len(st)} say DONE but are unchecked", ""]
    out += [f"- **[{s}]** {f}" for s, f in st]
    out += ["", f"## 4. Waiting on something a page already answered - {len(wt)}", ""]
    out += [f"- **[{s}]** *\"{f}...\"* is WAITING, but `{r}` records **{i}** as resolved: \"...{c}...\"" for s, f, i, r, c in wt[:T]]
    out += ["", f"## 5. Corrections not propagated - {len(cr)}", ""]
    out += [f"- `{r}` still says *\"{ln}\"*, but `{s}` records a correction (**{c}**)." for s, c, r, ln in cr[:T]]
    out += ["", "---", "*Generated by `scripts/coherence.py`. No similarity, no inference.*"]
    dest = KB / "outbox" / f"Coherence Report - {a.date}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"coherence: {len(ej)} entity gaps, {len(ul)} unlinked, {len(st)} stale, {len(wt)} waiting-but-answered, "
          f"{len(cr)} unpropagated corrections -> {dest.relative_to(KB)}")


if __name__ == "__main__":
    main()
