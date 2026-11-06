# Conventions — Structure, Routing, and Operations

Structural rules for the knowledge base. For how to fetch external sources, see the [`ingest`](.agents/skills/ingest/SKILL.md) skill.

---

## Conventions

### Source files (`raw/`)
- Organized by **author**, not by topic. One folder per person/publication: `Name - @handle/`.
- File naming: `YYYY-MM-DD - Author - Title.txt`.
- Topics are tracked via **tags in `index.md`**, not via folder names.
- Sources are **immutable** — never edit a raw file, only add new ones.

**Author-folder naming (when a handle doesn't apply):**
- Person with a known handle: `Name - @handle/`
- Person/publisher writing under their own domain: `Name - domain.tld/` (e.g. `Some Org - example.com/`)
- Never create show-named or topic-named folders. File a podcast under the **guest's** folder, with the show in the filename: `2026-01-15 - Guest Name - Episode Title (Show Name).txt`.
- **Content belongs to its author, not whoever shared it.** If someone recommends a video by another person, file the raw under the *actual* author, not the curator.

### Wiki articles (`wiki/`)
- Organized by **domain** (life-area / topic), not by author.
- **One-to-many.** Each article compiles MANY raw sources; each ingest updates MANY articles. The wiki is never a 1:1 mirror of raw.
- **Full routing, no lazy dumping.** Route every piece to its *proper* page. Don't dump everything into one catch-all "notes" file. This is the single most important discipline — a catch-all dump destroys the wiki's value.
- **Cite raw sources** for provenance, and **cross-link** between wiki pages with relative markdown links.
- **No artificial length limits.** Extract everything valuable; match the depth of the material. A good wiki page is comprehensive enough that reading it replaces reading the raw files.
- **Multi-speaker transcripts need speaker attribution** — most transcript tools don't tag speakers. Add a short attribution guide in the raw header, and flag genuinely ambiguous passages as "shared" rather than guessing.

### Filename hygiene
- **No Unicode** in filenames: straight quotes, hyphens, no em dashes, no emojis, no arrows/ellipsis.
- **No numbered prefixes** on folders (dates in filenames handle ordering).
- Folder names: Title Case or lowercase, spaces OK.

### Tags
Tags live in `index.md`, not in folder names. Define your own set for your domains; keep it small and reuse it.

---

## Routing — where new content goes

The routing table is *yours to define* per domain, but the shape is always the same. Examples:

| Kind of content | Where it goes |
|---|---|
| New raw source (transcript, thread, article) | `raw/sources/Author - @handle/YYYY-MM-DD - Author - Title.txt` |
| Objective data points (measurements, results, facts) | the domain's timeline/data page, e.g. `wiki/<domain>/Timeline.md` |
| Interpretation / analysis (what the data *means*) | a topical analysis page, e.g. `wiki/<domain>/<Topic>.md` |
| Active decisions / next steps / open items | `wiki/<domain>/Action Plan.md` **and** a line in `state.md` |
| Distilled lessons / principles | `wiki/<domain>/Learnings.md` |
| A recurring entity's profile (person, tool, ticker) | `wiki/<domain>/<Entity>.md`, updated over time |
| A durable fact stated in conversation (measurement, price, date, decision) | the domain page where someone would look for it, as TEXT, in the same turn (never only a commit message, a screenshot, or `log.md`) |
| A correction to something already filed | fix it at the source page with a dated `Corrected YYYY-MM-DD:` line, then propagate to every file that states the old claim (`scripts/coherence.py --corrections-only` finds stragglers) |
| A deliverable (digest, report, printable sheet) | `outbox/`; printable hand-over docs go through `scripts/print.sh` |

**Define the routing for each domain up front**, so ingestion is mechanical. A useful mental model for a domain's file set:
- one page = *the facts / timeline*
- one page = *the interpretation*
- one page = *the open loops / action plan*
- one page = *the distilled principles*

When a source touches multiple domains, the article goes in the **primary** domain and cross-links the others.

---

## Operations

### Ingest
1. Fetch raw content (see the `ingest` skill) and save to `raw/sources/Author - @handle/`.
2. Chase every linked pointer (transcript, article, quoted item) before compiling.
3. **Compile** into the wiki: integrate into the existing knowledge graph, not a 1:1 summary beside the raw file.
4. Update `index.md` (source row, file count, tags) and append to `log.md`.
5. Update `state.md` if an open loop opened or moved.
6. Record in the processed ledger (`scripts/ledger.py add`), last of the content steps.
7. Record in the intraday ledger so the next scheduled digest sees it:
   `python3 scripts/day-ledger.py add --source <kind> --summary "..." --pages "Page A;Page B" --authors "a,b"`
   (`--pages`/`--authors` are what the digest lint matches on).
8. Commit by explicit path, from the session worktree.

### Query
Read `index.md` first (the session's injected title index lists every page), then drill into wiki/raw. Verify specific claims against local `raw/`+`wiki/` **before** the web. File good answers back into `wiki/`. Never conclude "we don't have X" from a truncated search.

### Lint (periodic)
- `python3 scripts/coherence.py` writes a review queue to `outbox/` (entity joins, unlinked mentions, stale open items, waiting-but-answered items, unpropagated corrections). Nothing is edited automatically.
- `python3 scripts/state-hygiene.py --candidates FILE` / `--apply DECISIONS` closes open items a newer closed item resolved, after a judgment step.
- By hand: contradictions, stale claims, orphan pages, raw sources never compiled to wiki.
