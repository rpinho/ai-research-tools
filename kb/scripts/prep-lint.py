#!/usr/bin/env python3
"""
prep-lint.py: refuse a hand-over document whose claims of ABSENCE contradict the KB's open items.

WHY. A printed reference sheet, written from the usual shape of an answer, said a topic had "nothing
outstanding". state.md had carried a live open item on exactly that topic for months. The sheet was
relied on in a formal setting, and the error had to be corrected afterwards. A memory note was
written; it would not have stopped the next one. This script does.

WHAT IT DOES. For a document that will be printed, handed over or read under stress (reference
sheets, checklists, cheat sheets, cover letters, form prep), it finds every phrase that asserts
ABSENCE ("nothing owed", "none pending", "not required", "N/A", "no outstanding", "paid in full"),
takes the topic words in the surrounding context (taxes, loan, lease, insurance, ...), and greps the
OPEN items (unchecked "- [ ]" lines) of state.md and of any extra pages you list for the same topic.
A hit means the document says "nothing" about something the KB says is open: the lint fails and
prints the conflicting line, so the author can put the true state on the page.

Context, not sentences: table labels like "Taxes |" sit in a different cell from "Nothing owed", so
sentence splitting is exactly the wrong unit. Each claim is judged with ~140 chars before it.

ESCAPE HATCH. A claim that was actually verified is marked within ~200 chars of it:
    <!-- verified: state.md 2026-01-15 -->      (HTML)
    (verified: state.md 2026-01-15)             (text / markdown)
The marker names its own evidence, and it is per-claim, not per-file.

CONFIGURE: edit TOPICS below for your life admin, and PREP_LINT_PAGES (env, ';'-separated globs
relative to the KB root) for extra pages whose open items count, e.g. "wiki/finance/*.md".

USAGE
    python3 scripts/prep-lint.py FILE [FILE...]        # .html .md .txt (.pdf if pypdf is installed)
    python3 scripts/prep-lint.py --stdin < text
Exit 0 = clean, 1 = conflicts found, 2 = usage/IO error.
"""
import html, os, re, sys
from pathlib import Path

KB = Path(os.environ.get("KB_ROOT", Path(__file__).resolve().parent.parent))
EXTRA_PAGES = [g for g in os.environ.get("PREP_LINT_PAGES", "").split(";") if g.strip()]

# Phrases asserting absence. Deliberately broad: a false positive costs a marker; a false negative
# costs whatever the document was for.
ABSENCE = re.compile(
    r"\b(nothing|none|never|no|not|n/?a|zero|nil|clear|cleared|settled|paid\s+in\s+full|paid\s+off|up\s+to\s+date)\b"
    r"[^.;\n<>]{0,60}?"
    r"\b(owed?|owing|owes|due|overdue|pending|outstanding|balance|debt|debts|liabilit\w+|required|open|"
    r"missing|late|arrears|unpaid|payment|payments|plan|obligation\w*|claim\w*|case\w*)\b"
    r"|\b(owe|owes|owed|balance|debt|pending|outstanding|overdue)\b[^.;\n<>]{0,40}?\b(nothing|none|zero|nil|\$0|no)\b",
    re.I,
)

# topic -> (words that put a claim ON this topic, words that mark a KB open item as this topic)
TOPICS = {
    "taxes":     (r"\btax\w*|\breturn\b|\bwithh\w+", r"\btax\w*|\binstallment|\bpayment plan"),
    "credit":    (r"\bcredit\s*card\w*|\bcard\s+balance|\bapr\b", r"\bcredit\s*card|\bapr\b|\bcard balance"),
    "loan":      (r"\bloan\w*|\bmortgage|\bfinanc\w+", r"\bloan\b|\bmortgage|\bfinanc\w+"),
    "lease":     (r"\blease\w*|\bcar payment|\brent\b", r"\blease\b|\brent\b"),
    "insurance": (r"\binsuran\w+|\bclaim\w*|\bpremium\w*", r"\binsuran\w+|\bclaim\b"),
    "legal":     (r"\bcourt\w*|\blawsuit|\blegal\b|\bticket\w*|\bfine\w*|\bcitation", r"\bcourt\b|\blawsuit|\bticket\b|\bcitation|\bfine\b"),
    "medical":   (r"\bmedical\b|\bhospital|\bbill\w*|\binvoice\w*", r"\bmedical bill|\bhospital bill|\binvoice"),
    "immigration": (r"\bvisa\b|\bimmigra\w+|\bresiden\w+ permit|\bcitizenship", r"\bvisa\b|\bimmigra\w+|\bcitizenship"),
    "documents": (r"\bpassport\w*|\blicen[cs]e\b|\bregistration", r"\bpassport|\blicen[cs]e\b|\bregistration"),
}

VERIFIED = re.compile(r"(<!--\s*verified:[^>]*-->|\(verified:[^)]*\))", re.I)


def load_text(p: Path) -> str:
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader   # optional dependency
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    raw = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() in (".html", ".htm"):
        raw = re.sub(r"<style.*?</style>|<script.*?</script>", " ", raw, flags=re.S | re.I)
        # keep verified-comments as text so the proximity check still sees them
        raw = re.sub(r"<!--\s*(verified:[^>]*?)\s*-->", r"(\1)", raw, flags=re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = html.unescape(raw)
    return raw


def open_items():
    out, files = [], [KB / "state.md"]
    for g in EXTRA_PAGES:
        files += sorted(KB.glob(g.strip()))
    for f in files:
        if not f.exists():
            continue
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.lstrip().startswith("- [ ]"):
                out.append((f.relative_to(KB).as_posix(), ln.strip()))
    return out


def lint_text(text, items):
    problems, seen = [], set()
    for m in ABSENCE.finditer(text):
        ctx = text[max(0, m.start() - 140): m.end() + 80]
        if VERIFIED.search(text[m.start(): m.end() + 220]) or VERIFIED.search(text[max(0, m.start() - 220): m.end()]):
            continue
        low = ctx.lower()
        for topic, (doc_pat, kb_pat) in TOPICS.items():
            if not re.search(doc_pat, low):
                continue
            key = (topic, m.start() // 200)
            if key in seen:
                continue
            hits = sorted(((len(re.findall(kb_pat, ln, re.I)), src, ln) for src, ln in items if re.search(kb_pat, ln, re.I)),
                          key=lambda h: -h[0])
            if hits:
                seen.add(key)
                claim = text[max(0, m.start() - 60): m.end() + 40].replace("\n", " ").strip()
                problems.append((topic, claim[:160], [(s, l) for _, s, l in hits[:3]]))
    return problems


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    items = open_items()
    if argv == ["--stdin"]:
        docs = [("<stdin>", sys.stdin.read())]
    else:
        docs = []
        for a in argv:
            p = Path(a)
            if not p.exists():
                print(f"prep-lint: no such file: {a}", file=sys.stderr)
                return 2
            docs.append((a, load_text(p)))
    total = 0
    for label, text in docs:
        for topic, claim, hits in lint_text(text, items):
            total += 1
            print(f"\nx {label}: claims absence about [{topic}] but the KB has it OPEN")
            print(f"   doc:  \"{claim}\"")
            for src, ln in hits:
                print(f"   {src}: {ln[:200]}")
    if total:
        print(f"\nprep-lint: {total} conflict(s). Put the true state on the page, or mark a checked claim "
              f"with <!-- verified: state.md YYYY-MM-DD --> within 200 chars of it.", file=sys.stderr)
        return 1
    print(f"prep-lint: clean ({len(docs)} doc(s), {len(items)} open items checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
