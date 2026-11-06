#!/usr/bin/env python3
"""Render the whole wiki/ tree to a single browsable wiki.html (dependency-free).

Walks wiki/, converts each markdown file with a small built-in markdown subset
(headings, bold/italic, inline code, code fences, links, lists, blockquotes,
paragraphs), and emits one self-contained HTML file with a domain-grouped sidebar.
No external libraries — pure standard library.

  python3 scripts/build-wiki-html.py            # -> wiki.html
"""
import os, re, html, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.path.join(ROOT, "wiki")
OUT = os.path.join(ROOT, "wiki.html")

def md_inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"<em>\1</em>", s)
    return s

def md_to_html(text):
    out, in_code, in_list = [], False, False
    def close_list():
        nonlocal in_list
        if in_list: out.append("</ul>"); in_list = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            if in_code: out.append("</pre>"); in_code = False
            else: close_list(); out.append("<pre>"); in_code = True
            continue
        if in_code:
            out.append(html.escape(line)); continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            close_list(); n = len(m.group(1))
            out.append(f"<h{n}>{md_inline(m.group(2))}</h{n}>"); continue
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list: out.append("<ul>"); in_list = True
            item = re.sub(r"^\s*[-*]\s+", "", line)
            out.append(f"<li>{md_inline(item)}</li>"); continue
        if line.strip().startswith(">"):
            close_list(); out.append(f"<blockquote>{md_inline(line.strip()[1:].strip())}</blockquote>"); continue
        if not line.strip():
            close_list(); continue
        close_list(); out.append(f"<p>{md_inline(line)}</p>")
    if in_code: out.append("</pre>")
    close_list()
    return "\n".join(out)

files = sorted(glob.glob(os.path.join(WIKI, "**", "*.md"), recursive=True))
by_domain = {}
for f in files:
    domain = os.path.relpath(os.path.dirname(f), WIKI) or "."
    by_domain.setdefault(domain, []).append(f)

nav, body = [], []
for domain in sorted(by_domain):
    nav.append(f"<div class=dom>{html.escape(domain)}</div>")
    for f in by_domain[domain]:
        anchor = re.sub(r"\W+", "-", os.path.relpath(f, WIKI)).strip("-")
        title = os.path.splitext(os.path.basename(f))[0]
        nav.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
        body.append(f'<section id="{anchor}">' + md_to_html(open(f, encoding="utf-8").read()) + "</section>")

CSS = """body{margin:0;font:16px/1.6 -apple-system,system-ui,sans-serif;color:#1a1a1a}
nav{position:fixed;top:0;left:0;width:260px;height:100vh;overflow:auto;padding:1rem;
box-sizing:border-box;background:#f6f7f9;border-right:1px solid #e2e4e8}
nav a{display:block;padding:2px 0;color:#2563eb;text-decoration:none;font-size:14px}
nav .dom{margin:1rem 0 .25rem;font-weight:700;font-size:12px;text-transform:uppercase;color:#666}
main{margin-left:260px;padding:2rem 3rem;max-width:820px}
section{border-bottom:1px solid #eee;padding-bottom:2rem;margin-bottom:2rem}
pre{background:#f6f7f9;padding:1rem;border-radius:6px;overflow-x:auto;font-size:13px}
code{background:#f0f1f3;padding:1px 4px;border-radius:3px;font-size:13px}
blockquote{border-left:3px solid #ccc;margin:0;padding-left:1rem;color:#555}
@media(prefers-color-scheme:dark){body{background:#0e0f11;color:#e6e6e6}
nav{background:#16181c;border-color:#2a2d33}main section{border-color:#2a2d33}
pre,code{background:#1c1f24}nav a{color:#6ea8fe}}"""

open(OUT, "w", encoding="utf-8").write(
    f"<!doctype html><meta charset=utf-8><title>Wiki</title><style>{CSS}</style>"
    f"<nav>{''.join(nav)}</nav><main>{''.join(body)}</main>")
print(f"rendered {len(files)} pages -> {os.path.relpath(OUT, ROOT)}")
