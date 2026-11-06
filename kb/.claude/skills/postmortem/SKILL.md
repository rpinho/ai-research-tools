---
name: postmortem
description: Run a structured post-mortem on an outcome (good or bad), extract the durable pattern behind it, and write it back as a memory/wiki entry so the KB can protect the human next time. This is how the protector function LEARNS its patterns.
---

# postmortem — turn an outcome into a pattern the KB can act on

This skill closes the loop that makes the KB a protector (see `AGENTS.md` → "The KB as a protector"). **The protector can only *name* a pattern the KB has already *learned*. Post-mortems are how it learns.** No post-mortems → an empty pattern library → a second brain that can file but can't intervene.

Run this whenever an outcome is worth learning from — not only failures:
- a project shipped, stalled, or was abandoned
- a decision played out (well or badly)
- a plan was followed — or dropped the moment it got uncomfortable
- a trade/bet/negotiation closed
- a recurring frustration surfaced again
- a win worth reinforcing (learn from what worked, not just what broke)

## The post-mortem (do all of it)

1. **What actually happened** — the facts, dated. No spin. Pull from `raw/`, `log.md`, and the human's account.
2. **What was expected / intended** — the plan or prediction going in.
3. **The gap** — where outcome diverged from intent, and when the divergence started.
4. **Root cause — separate the two layers:**
   - **Situational:** what about *this* situation caused it (missing info, bad timing, external shock).
   - **⭐ Behavioral / pattern:** what about *how the human operates* caused it — the recurring move. This is the load-bearing part. Was this an instance of a pattern that has happened before? (Search existing memories + post-mortems for it.) Common shapes: bailing under stress, abandoning a plan when it got uncomfortable instead of when it was invalidated, giving away the visible/exposed work out of fear, de-risking on emotion not information, over-committing then resenting it, avoiding a rep that later proved easy.
5. **The counter-evidence** — what the human's *own track record* actually shows about how these situations tend to go for them. (Very often: the feared outcome didn't happen; they performed better than the in-the-moment prediction. That evidence is the lever the protector will use.)
6. **The intervention that would have worked** — stated concretely enough to *reuse*: what should the agent say/do next time the early-warning signal of this pattern appears?

## Write it back (this is the point)

- **If it's a recurring behavioral pattern → write/update a `feedback`-type memory** (`memory/`, indexed in `MEMORY.md`). Include: the pattern, its **trigger / early-warning signal**, the **evidence** (their own record), and the **intervention**. This is exactly what the protector reads to intervene next time. Update the existing memory rather than duplicating if the pattern is already known — strengthen it with the new datapoint.
- **If it's a substantive one-off (a project/decision/trade) → write a post-mortem wiki page** (e.g. `wiki/<domain>/postmortems/` or the domain's learnings page), and cite it. If it *also* reveals a behavioral pattern, do both and cross-link.
- **Update `state.md`** if it changes an open loop, and append to `log.md`.
- **Commit.**

## The loop, stated plainly

```
outcome  →  postmortem (this skill)  →  pattern written as feedback-memory
                                              │
                                              ▼
        human hits the trigger again  →  protector reads the memory  →  intervenes
                                              │
                                              ▼
                                      new outcome  →  postmortem …  (the library compounds)
```

Do post-mortems on the wins too: they tell the protector what *good* looks like for this person, so "you're at your best when…" is as available as "watch out for…".
