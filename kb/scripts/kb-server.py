#!/usr/bin/env python3
"""
kb-server.py: a tiny READ-ONLY web reader for the KB, so a digest can LINK into it and you can read
the wiki from your phone.

WHY. One-line digest items are too compressed to stand alone ("of course we're doing that", with no
way to find out what "that" was). Every substantial digest item links to its full wiki page, and
this server is what makes those links resolve.

- READ-ONLY: GET only. No writes, no deletes, no POST.
- Renders .md (tables, lists, code) and resolves [[wikilinks]] by page name; .txt raw sources in a
  readable frame; .html as-is; directories as listings; /search greps the wiki.
- LATEST BOX: living pages get appended at the bottom, so a link used to land on stale text. On long
  pages the NEWEST dated section ("## 2026-01-15 ..." headings) is shown first, whatever the file order.
- /home: a one-link landing page (search + latest digest + your own links) for a phone home screen.
  Links come from home-links.json at the KB root (gitignored): {"Section": {"Label": "url", ...}, ...}
- TOKEN GATE: every request except /healthz needs the token. The first hit carries the token as the "k" query parameter
  (put it in the digest's links); the server then sets an HttpOnly cookie. No token = 403.
  The token is generated on first run at $KB_TOKEN_FILE (default ~/.config/agent-kb/kb-server-token, 0600).
- BLOCKED paths (never served, never searched): .git plus $KB_BLOCKED (comma-separated, e.g. private originals).
- Binds 127.0.0.1 by default. Set KB_HOST=0.0.0.0 to reach it from other devices, and only behind
  the token, ideally on a private network overlay rather than shared wifi.

Run:  python3 scripts/kb-server.py          (uses the `markdown` package if installed, else a built-in renderer)
Env:  KB_ROOT, KB_HOST, KB_PORT (8787), KB_BLOCKED, KB_TOKEN_FILE
"""
import hmac, html, http.cookies, json, mimetypes, os, re, secrets, subprocess, time, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("KB_ROOT", Path(__file__).resolve().parent.parent)).resolve()
HOST = os.environ.get("KB_HOST", "127.0.0.1")
PORT = int(os.environ.get("KB_PORT", "8787"))
BLOCKED = {".git"} | {b.strip().strip("/") for b in os.environ.get("KB_BLOCKED", "").split(",") if b.strip()}
TOKEN_FILE = Path(os.environ.get("KB_TOKEN_FILE", Path.home() / ".config" / "agent-kb" / "kb-server-token"))
LATEST_MIN_WORDS = 1200    # shorter pages render as-is
LATEST_ONLY_WORDS = 15000  # longer pages serve only the Latest box unless ?v=full


def load_token():
    if TOKEN_FILE.exists() and TOKEN_FILE.read_text().strip():
        return TOKEN_FILE.read_text().strip()
    t = secrets.token_urlsafe(24)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(t)
    TOKEN_FILE.chmod(0o600)
    return t


TOKEN = load_token()

try:
    import markdown as _md

    def md_to_html(src):
        return _md.Markdown(extensions=["extra", "tables", "sane_lists"]).convert(src)
except ImportError:   # dependency-free fallback: headings, lists, code, quotes, tables, inline marks
    def _inline(s):
        s = html.escape(s, quote=False)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", s)

    def md_to_html(src):
        out, mode = [], None
        def close():
            nonlocal mode
            if mode:
                out.append({"ul": "</ul>", "table": "</table>"}.get(mode, ""))
            mode = None
        lines = src.split("\n")
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.strip().startswith("```"):
                close(); j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("```"):
                    j += 1
                out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>"); i = j + 1; continue
            m = re.match(r"^(#{1,6})\s+(.*)", ln)
            if m:
                close(); n = len(m.group(1)); out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            elif re.match(r"^\s*\|.*\|\s*$", ln):
                if re.match(r"^\s*\|[\s:|-]+\|\s*$", ln):
                    i += 1; continue
                if mode != "table":
                    close(); out.append("<table>"); mode = "table"
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            elif re.match(r"^\s*[-*]\s+", ln):
                if mode != "ul":
                    close(); out.append("<ul>"); mode = "ul"
                item = re.sub(r"^\s*[-*]\s+", "", ln)
                out.append(f"<li>{_inline(item)}</li>")
            elif ln.startswith(">"):
                close(); out.append(f"<blockquote>{_inline(ln.lstrip('> '))}</blockquote>")
            elif not ln.strip():
                close()
            else:
                close(); out.append(f"<p>{_inline(ln)}</p>")
            i += 1
        close()
        return "\n".join(out)

CSS = """
:root{--paper:#faf9f6;--card:#fff;--ink:#1c1b18;--muted:#6f6a5c;--line:#e6e2d8;--accent:#a2621b;--chip:#f0ece2;--hl:#fffaf0}
@media (prefers-color-scheme:dark){:root{--paper:#14151a;--card:#1c1e24;--ink:#e9e7e1;--muted:#95907f;--line:#2b2d35;--accent:#d59a46;--chip:#252831;--hl:#24201a}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.62 -apple-system,system-ui,sans-serif}
.bar{position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.bar a{color:var(--accent);text-decoration:none;font-weight:600;font-size:14px}.bar form{margin-left:auto;display:flex;gap:6px}
input{background:var(--card);border:1px solid var(--line);color:var(--ink);border-radius:8px;padding:7px 10px;font-size:15px;min-width:0}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:7px 13px;font-weight:600}
main{max-width:820px;margin:0 auto;padding:24px 16px 80px}h1,h2,h3{font-family:Georgia,serif;line-height:1.25}
a{color:var(--accent)}code{background:var(--chip);padding:.1em .35em;border-radius:4px;font-size:.9em}
pre{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:14px;overflow-x:auto}pre code{background:none}
blockquote{margin:1em 0;padding-left:14px;border-left:3px solid var(--accent);color:var(--muted)}
table{border-collapse:collapse;display:block;overflow-x:auto;font-size:15px}th,td{border:1px solid var(--line);padding:7px 9px;vertical-align:top}
.meta{color:var(--muted);font-size:13.5px}.missing{color:var(--muted);border-bottom:1px dotted}
.latest{border:2px solid var(--accent);border-radius:10px;padding:12px 16px;margin:0 0 22px;background:var(--hl)}
.ls{list-style:none;padding:0}.ls li{padding:7px 0;border-bottom:1px solid var(--line)}
.hit{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:10px 12px;margin:10px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.tile{display:block;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--card);text-decoration:none;font-weight:600}
@media (max-width:380px){.grid{grid-template-columns:1fr}}
"""

_idx = {"t": 0.0, "map": {}}


def is_blocked(p: Path) -> bool:
    try:
        rel = p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return True
    return any(rel == b or rel.startswith(b + "/") for b in BLOCKED)


def wiki_index():
    if time.time() - _idx["t"] > 60 or not _idx["map"]:
        m = {}
        for p in (ROOT / "wiki").rglob("*.md") if (ROOT / "wiki").is_dir() else []:
            if not is_blocked(p):
                m.setdefault(p.stem.lower(), p.relative_to(ROOT).as_posix())
        _idx.update(t=time.time(), map=m)
    return _idx["map"]


def wikilinks(text):
    idx = wiki_index()
    def repl(m):
        name = m.group(1).split("|")[0].strip()
        t = idx.get(name.lower())
        return (f'<a href="/{urllib.parse.quote(t)}">{html.escape(name)}</a>' if t
                else f'<span class="missing" title="no page yet">{html.escape(name)}</span>')
    return re.sub(r"\[\[([^\]]+)\]\]", repl, text)


def shell(title, body, sub=""):
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class="bar">'
            f'<a href="/home">home</a><a href="/wiki">wiki</a><a href="/index.md">index</a><a href="/state.md">state</a>'
            f'<a href="/outbox">outbox</a><form action="/search"><input name="q" placeholder="search"><button>Go</button></form></div>'
            f'<main><h1>{html.escape(title)}</h1>{f"<p class=meta>{sub}</p>" if sub else ""}{body}</main></body></html>')


def latest_section(src):
    """(heading, body, date) of the newest section whose heading carries YYYY-MM(-DD)."""
    heads = list(re.finditer(r"^(#{2,3})\s+(.*)$", src, flags=re.M))
    best = None
    for i, h in enumerate(heads):
        m = re.search(r"(20\d\d-\d\d(?:-\d\d)?)", h.group(2))
        if not m:
            continue
        end = next((n.start() for n in heads[i + 1:] if len(n.group(1)) <= len(h.group(1))), len(src))
        if best is None or m.group(1) > best[2]:
            best = (h.group(2).strip(), src[h.end():end].strip(), m.group(1))
    return best


def render_md(p, full=False):
    raw = p.read_text(encoding="utf-8", errors="replace")
    src = re.sub(r"^#\s+.*\n", "", raw, count=1)
    body = wikilinks(md_to_html(src))
    n = len(raw.split())
    lat = latest_section(src) if (n >= LATEST_MIN_WORDS and not full) else None
    if lat:
        head, sec, d = lat
        box = (f'<div class="latest"><div class="meta">LATEST - {html.escape(d)} - newest dated section shown first</div>'
               f'<h2>{wikilinks(html.escape(head))}</h2>{wikilinks(md_to_html(sec))}')
        if n >= LATEST_ONLY_WORDS:
            body = box + f'<p class="meta">Page is {n:,} words; only the newest section is shown. <a href="?v=full">Full page</a></p></div>'
        else:
            body = box + f'<p class="meta">Full page below ({n:,} words). <a href="?v=full">Hide this box</a></p></div>' + body
    mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return shell(p.stem, body, f"{p.relative_to(ROOT)} - updated {mtime}")


def render_dir(p):
    kids = sorted([c for c in p.iterdir() if not c.name.startswith(".") and not is_blocked(c)],
                  key=lambda c: (not c.is_dir(), c.name.lower()))
    items = "".join(f'<li><a href="/{urllib.parse.quote(c.relative_to(ROOT).as_posix())}">{html.escape(c.name)}{"/" if c.is_dir() else ""}</a></li>'
                    for c in kids)
    return shell(p.name or "KB", f'<ul class="ls">{items}</ul>', f"{len(kids)} entries")


def render_search(q):
    if not q.strip():
        return shell("Search", "<p>Type a query above.</p>")
    try:
        out = subprocess.run(["grep", "-rinF", "--include=*.md", "-m", "3", "--", q, "wiki", "index.md", "state.md"],
                             cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        return shell("Search", f"<p>search failed: {html.escape(str(e))}</p>")
    hits = []
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or is_blocked(ROOT / parts[0]):
            continue
        snip = html.escape(parts[2].strip()[:400])
        hits.append(f'<div class="hit"><div class="meta"><a href="/{urllib.parse.quote(parts[0])}">{html.escape(parts[0])}</a> - line {parts[1]}</div>{snip}</div>')
        if len(hits) >= 120:
            break
    return shell(f'Search: "{q}"', "".join(hits) or "<p>No matches.</p>", f"{len(hits)} matching lines (max 3 per file)")


def render_home():
    def newest(pattern):
        files = sorted(ROOT.glob(pattern), key=lambda f: (re.findall(r"20\d\d-\d\d-\d\d", f.name) or [""])[-1] + f.name)
        return files[-1] if files else None
    tiles = []
    d = newest("outbox/*Digest*20*.html")
    if d:
        tiles.append(f'<a class="tile" href="/{urllib.parse.quote(d.relative_to(ROOT).as_posix())}">Latest digest<div class="meta">{html.escape(d.stem)}</div></a>')
    tiles.append('<a class="tile" href="/state.md">Open items<div class="meta">state.md</div></a>')
    body = ('<form action="/search" style="display:flex;gap:8px;margin-bottom:18px"><input name="q" style="flex:1;font-size:18px" '
            'placeholder="Search the wiki" autofocus><button>Search</button></form>'
            f'<div class="grid">{"".join(tiles)}</div>')
    links_file = ROOT / "home-links.json"
    if links_file.exists():
        try:
            for section, links in json.loads(links_file.read_text()).items():
                body += f'<h3>{html.escape(section)}</h3><div class="grid">' + "".join(
                    f'<a class="tile" href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(label)}</a>'
                    for label, u in links.items()) + "</div>"
        except Exception as e:
            body += f'<p class="meta">home-links.json unreadable: {html.escape(str(e))}</p>'
    return shell("Home", body)


class Handler(BaseHTTPRequestHandler):
    server_version = "kb-server/2"

    def log_message(self, *a):
        pass

    def send(self, body, code=200, ctype="text/html; charset=utf-8", cookie=False):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", f"kb={TOKEN}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax")
        self.end_headers()
        self.wfile.write(data)

    def authed(self, parsed):
        k = urllib.parse.parse_qs(parsed.query).get("k", [None])[0]
        if k and hmac.compare_digest(k, TOKEN):
            return "set"
        try:
            c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return "kb" in c and hmac.compare_digest(c["kb"].value, TOKEN)
        except Exception:
            return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path == "/healthz":   # unauthenticated and reveals nothing: lets a scheduled job check liveness
            return self.send(b"ok", ctype="text/plain")
        auth = self.authed(parsed)
        if not auth:
            return self.send(shell("Locked", "<p>This reader needs a token. Open it from a digest link, which carries one.</p>"), 403)
        ck = auth == "set"
        if path in ("/", "/home", "/home/"):
            return self.send(render_home(), cookie=ck)
        if path == "/search":
            return self.send(render_search(urllib.parse.parse_qs(parsed.query).get("q", [""])[0]), cookie=ck)
        target = (ROOT / path.lstrip("/")).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            return self.send(shell("Forbidden", "<p>Outside the KB.</p>"), 403)
        if is_blocked(target):
            return self.send(shell("Blocked", "<p>This path is not served.</p>"), 403)
        if not target.exists():
            hit = wiki_index().get(Path(path).stem.lower())
            if hit:
                self.send_response(302)
                self.send_header("Location", "/" + urllib.parse.quote(hit))
                self.end_headers()
                return
            return self.send(shell("Not found", f"<p>No such path: <code>{html.escape(path)}</code></p>"), 404)
        if target.is_dir():
            return self.send(render_dir(target), cookie=ck)
        suf = target.suffix.lower()
        if suf == ".md":
            return self.send(render_md(target, full="v=full" in (parsed.query or "")), cookie=ck)
        if suf in (".txt", ".json", ".jsonl", ".py", ".sh"):
            raw = target.read_text(encoding="utf-8", errors="replace")
            return self.send(shell(target.stem, f"<pre>{html.escape(raw)}</pre>", f"{target.relative_to(ROOT)} - {len(raw.split()):,} words"), cookie=ck)
        if suf in (".html", ".htm"):
            return self.send(target.read_bytes(), cookie=ck)
        return self.send(target.read_bytes(), ctype=mimetypes.guess_type(target.name)[0] or "application/octet-stream", cookie=ck)


if __name__ == "__main__":
    print(f"kb-server on http://{HOST}:{PORT} serving {ROOT} (token auth on; token file {TOKEN_FILE})", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
