You are running UNATTENDED as phase 1 (INGEST) of the scheduled KB run for @@DAY@@. Work in THIS worktree.
Commit here with explicit paths (never `git add -A`); do not push. Read AGENTS.md first.

@@TESTMODE@@

THE ORDER BELOW IS A PRIORITY ORDER, NOT A CHECKLIST. Your deliverable is the BRIEF at
raw/_index/digest-brief-@@DAY@@.md. A smaller brief is a SUCCESS. Exhaustive ingest work that ends with
no brief is a FAILURE. If you run low on room or time, jump to step 5 immediately.

@@DELEGATION@@

1. GATHER. <YOUR SPEC: where tonight's new items come from (a queue, an export, a folder), how to
   pull them, and the stop rule for pagination (e.g. stop after two consecutive pages that are
   entirely already-processed per scripts/ledger.py allseen).>

2. SAVE RAW, THEN ROUTE. Save every new item verbatim under raw/ before anything else. Route the
   signal into the right wiki pages (CONVENTIONS.md -> Routing). Update index.md and log.md. Record
   processed ids with scripts/ledger.py add, LAST.

3. INTRADAY LEDGER. @@PENDING_N@@ entries were ingested in interactive sessions since the last digest:
   read `python3 scripts/day-ledger.py pending --md` and carry each into the brief, in the section
   it belongs to, keeping its pages and authors (the digest lint matches on them).

4. HOUSEKEEPING (judged, then applied):
   - `python3 scripts/state-hygiene.py --candidates /tmp/hygiene.json`; judge each pair (is the open
     item really resolved by the closed one?), write decisions, then `--apply` them.
   - Fix anything `python3 scripts/coherence.py --corrections-only` flags (a correction recorded in
     one file while another still states the old claim).

5. WRITE THE BRIEF, COMMIT IT. raw/_index/digest-brief-@@DAY@@.md, in this shape:
   ## 1. TODAY       dated real-life actions from state.md: due within 14 days, with a COUNTDOWN
   ## 2. THE READ    the 3-5 things that matter most tonight and why (synthesis, not a list)
   ## 3. EVIDENCE    one line per routed item: author | claim | wiki page | link
   ## 4. ASKS        only questions that need the human (money, sends, irreversible acts, taste);
                     never ask permission for routine work you already have standing permission for
   Commit it. Everything after this point is optional.

6. <YOUR OPTIONAL WORK: profile updates, deeper analyses, anything that can wait a day.>

7. Before finishing: `git status --short` and commit anything you created, by explicit path.
