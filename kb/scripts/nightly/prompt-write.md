You are running UNATTENDED as phase 2 (WRITE) of the scheduled KB run for @@DAY@@. Work in THIS worktree.
Commit here with explicit paths; do not push.

@@TESTMODE@@

Your ONLY input is the brief at raw/_index/digest-brief-@@DAY@@.md (plus pages it links, if you need a
detail). If the brief is missing, reconstruct from `git log --since=yesterday` and today's raw/_index files.

Write the digest to outbox/Digest - @@DAY@@.html. Format rules live HERE, next to the prompt, and the
hard ones are enforced by scripts/digest-lint.py afterwards (word caps, section order, intraday ledger
coverage, permission-slip asks). Write to pass it the first time:

- Sections as <h2> headings, in this order: Today, The Read, then topic sections, then Asks.
- Today: at most 5 rows of real-life actions (due today or soon, with a countdown). Nothing else.
- The Read: synthesis across sources, the one-paragraph answer to "what changed and what do I do".
- Every substantive item links to its wiki page through the KB reader (scripts/kb-server.py).
- Every intraday ledger entry in the brief is blended into the section it belongs to.
- <YOUR SPEC: which topic sections, tone, how much depth per topic, what to always include.>
- If there is nothing to report, still write a short digest that says so.

@@DELIVERY_STEP@@

Before finishing: commit the digest file by explicit path.
