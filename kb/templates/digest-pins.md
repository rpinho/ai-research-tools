# Digest pins — items that MUST appear in the next scheduled digest

**The problem this solves.** A nightly/scheduled agent run writes up what *it* processed that night.
Anything ingested outside that run — in an interactive session, from a handoff, a one-off
transcription — has **no route into a digest at all**. It is silently dropped.

**How the scheduled run uses this file:**
1. Read it BEFORE writing the digest.
2. Every `- [ ]` item MUST appear, in the section named on the item.
3. After including it, tick to `- [x] <digest date>` and commit the file.
4. Ticked items are history — leave them, never re-include.

---

- [ ] **§ SECTION NAME — headline.** What must be said, and any link.
