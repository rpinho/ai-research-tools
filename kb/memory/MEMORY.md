# Memory Index

Durable facts the agent should recall across sessions. **This file is the index** — it's loaded at the start of each session. Each real memory is its own file in `memory/`, holding **one fact**, with frontmatter. Add a one-line pointer here when you write one.

## Format of a memory file

```markdown
---
name: <short-kebab-case-slug>
description: <one-line summary — used to decide relevance during recall>
metadata:
  type: user | feedback | project | reference
---

<the fact. For feedback/project, follow with **Why:** and **How to apply:** lines.>
<Link related memories with [[their-name]].>
```

**Types:**
- `user` — who the human is (role, expertise, preferences).
- `feedback` — guidance on how you should work (corrections and confirmed approaches); include the *why*.
- `project` — ongoing work, goals, or constraints not derivable from the files; convert relative dates to absolute.
- `reference` — pointers to external resources (URLs, dashboards, tickets).

**When to write one:** you learn something durable about the human's preferences, a recurring correction, or a project constraint that the files themselves don't already capture. **Don't** save what the repo already records (structure, history, conventions) — save what was non-obvious.

**Before saving:** check for an existing file that already covers it and update that instead of duplicating. Delete memories that turn out to be wrong.

**Keeping the index useful as it grows:**
- One line per memory, under ~200 characters: a link plus the rule in a phrase. The index is loaded every session; the detail lives in the file.
- Lead the line with the rule, not the story ("Dates come from the human, not from commit timestamps", not "On Tuesday I inferred...").
- When a new datapoint confirms an existing memory, append it to that file (dated) rather than writing a sibling.
- Memory is for *how to work with this human*. Facts about the world, their projects and their life go in `wiki/` (and `state.md` for open loops), where they are indexed and routed. A memory that restates a wiki fact will drift from it.
- **A memory that keeps being needed is a rule asking to be promoted.** If the same correction recurs, build the mechanism (a hook, a lint, a script check; see AGENTS.md -> Enforce with architecture) and shrink the memory to a pointer at it.

---

## Index

- [Example: how this human likes summaries](example-preference.md) — lead with the outcome; prose over bullet-fragments. *(Delete this once you have real memories.)*
