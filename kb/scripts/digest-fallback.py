#!/usr/bin/env python3
"""
digest-fallback.py: build a digest with NO model involved.

WHY. The digest is the deliverable, and a scheduled job once failed to deliver it five mornings in a
row, for five different reasons (credential unavailable; a probe misreporting the cause; a probe
running without the real run's flags; unescaped backticks killing the script; a run that exhausted
itself one step before writing). Every fix addressed one cause and the next morning found another.
This file stops trying to prevent failure and guarantees an OUTPUT: it reads files on disk and emits
a real, plain digest deterministically. Ugly compared to the model-written one; never absent.

SECTIONS (each only if it has content):
  Alerts            open alerts from scripts/alert.py (the problems a job already detected)
  Coming up         dated open items in state.md due within LOOKAHEAD_DAYS, with a COUNTDOWN.
                    A sweep that only shows items due today surfaces the year's most important
                    appointment on the morning it happens. That is not a reminder.
  Since last digest intraday ledger entries (scripts/day-ledger.py pending)
  Changed overnight wiki pages modified today

Usage: digest-fallback.py [--date YYYY-MM-DD] [--out PATH]
Env:   KB_ROOT, KB_READER_BASE (link base for the KB reader, default http://localhost:8787)
"""
import argparse, datetime, html, json, os, re, subprocess, sys, urllib.parse
from pathlib import Path

KB = Path(os.environ.get("KB_ROOT", Path(__file__).resolve().parent.parent))
BASE = os.environ.get("KB_READER_BASE", "http://localhost:8787").rstrip("/")
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", 14))
DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def esc(t):
    return html.escape(t, quote=False)


def link(rel, label):
    return f'<a href="{BASE}/{urllib.parse.quote(rel)}">{esc(label)}</a>'


def md_inline(t):
    t = esc(t)
    t = re.sub(r"\[\[([^\]|]+)\]\]", lambda m: link("wiki/" + m.group(1), m.group(1)), t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


def coming_up(day):
    """Open '- [ ]' items in state.md carrying a date within [day - 7, day + LOOKAHEAD_DAYS]."""
    p = KB / "state.md"
    if not p.exists():
        return []
    today = datetime.date.fromisoformat(day)
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        t = line.strip()
        if not t.startswith("- [ ]"):
            continue
        dates = []
        for d in DATE.findall(t):
            try:
                dates.append(datetime.date.fromisoformat(d))
            except ValueError:
                pass
        due = [d for d in dates if -7 <= (d - today).days <= LOOKAHEAD_DAYS]
        if not due:
            continue
        n = (min(due) - today).days
        tag = "OVERDUE" if n < 0 else "TODAY" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
        out.append((n, f"<b>{tag}</b> - {md_inline(t[5:].strip()[:220])}"))
    return [s for _, s in sorted(out)][:10]


def run_json(args):
    try:
        r = subprocess.run([sys.executable] + args, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, KB_ROOT=str(KB)))
        return r.stdout
    except Exception:
        return ""


def alerts():
    out = run_json([str(KB / "scripts" / "alert.py"), "list"])
    return [esc(l.split("\t", 2)[-1]) for l in out.splitlines() if l.strip()]


def ledger():
    try:
        entries = json.loads(run_json([str(KB / "scripts" / "day-ledger.py"), "pending", "--json"]) or "[]")
    except json.JSONDecodeError:
        return []
    rows = []
    for e in entries:
        pages = ", ".join(f"[[{p}]]" for p in e.get("pages", []))
        rows.append(md_inline(f"**{e.get('source', '')}**: {e.get('summary', '')}" + (f" ({pages})" if pages else "")))
    return rows


def changed_pages(day):
    out = []
    for p in (KB / "wiki").rglob("*.md") if (KB / "wiki").exists() else []:
        try:
            if datetime.date.fromtimestamp(p.stat().st_mtime).isoformat() == day:
                out.append(p.relative_to(KB).as_posix())
        except OSError:
            pass
    return sorted(out)[:15]


def build(day):
    pretty = datetime.date.fromisoformat(day).strftime("%A %d %B %Y")
    def section(title, rows):
        if not rows:
            return ""
        return (f'<h2 style="font:bold 12px Georgia,serif;letter-spacing:1.5px;text-transform:uppercase;color:#a2621b;'
                f'border-bottom:2px solid #a2621b;padding-bottom:4px;margin:22px 0 8px">{title}</h2>'
                + "".join(f'<p style="margin:0 0 10px">{r}</p>' for r in rows))
    pages = [link(p, Path(p).stem) for p in changed_pages(day)]
    body = (section("Alerts", alerts()) + section("Coming up", coming_up(day))
            + section("Since last digest", ledger()) + section("Changed overnight", pages))
    if not body:
        body = "<p>Nothing on file for today: no alerts, nothing due in the next two weeks, no intraday ingests.</p>"
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="subject" content="KB digest {day} (fallback: the scheduled run did not write one)">'
            f'<title>Digest {day}</title></head><body style="margin:0;background:#f4f2ee">'
            '<div style="max-width:640px;margin:0 auto;background:#fff;padding:22px 24px;font:15px/1.6 Georgia,serif;color:#241f1a">'
            f'<div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#a2621b">{pretty}</div>'
            '<div style="font-size:26px;font-weight:bold;padding-top:4px">Digest</div>'
            '<p style="color:#6a635a;font-size:13px">Built by the deterministic fallback: the scheduled run did not produce '
            'a digest. Content below is real but unsynthesised.</p>'
            f'{body}<p style="margin-top:24px;font-size:12px;color:#8a8178">{link("index.md", "Index")} - '
            f'{link("state.md", "State")}</p></div></body></html>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--out")
    a = ap.parse_args()
    out = Path(a.out) if a.out else KB / "outbox" / f"Digest - {a.date}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(a.date), encoding="utf-8")
    print(f"fallback digest written: {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
