<!-- lane-rules-common: generated from harness/LANE_RULES_COMMON.md by `lane-rules-sync`. Edit there, not here. -->
## Rules every lane gets

**HARD RULE (Ricardo, 11-04): the goal must not turn into process, and success is never activity.**
Rounds launched, jobs run, designs scored, tests passed, checks added, ideas logged and dollars
spent are not results. The only result is the thing that was asked for, shipped. A lane that
measures itself by how much it ran has already lost the ask.

**Restate the ask.** The first line of each round, job or campaign note is
`You asked: <the ask, in the spec's or Ricardo's words>. I will: <what this run does differently from the last one>.`
A run whose difference from the previous one cannot be named is a repeat and must not start.

**Stop rule.** Three runs that fail the same way end that approach; there is no fourth. Record in
STATUS.md or STATE.md what failed, by number, and what changes. "Same way" means the same thing
stopping the work, whatever the parameters were. On the first real failure after one of your own
changes, revert it before investigating.

**Log every run, win or not.** One line per run to this lane's `RESULTS.jsonl`:
`log-result --ledger <lane>/RESULTS.jsonl --new yes|no --kind <kind> --what "<what changed and what came of it>" --link <path> --referee HOLDS|NONE`
`--new yes` only when something actually moved: a printed number beaten under the same definition
and data, a stated open problem settled, a prediction tested on new data, or a shipped artefact
that is better than the one before it. Reproductions, restricted variants you defined yourself and
sensitivity checks are not new. Say "no" plainly when it is no.

**How this is enforced.** `lane-yield` reads that ledger on a schedule. BURNING fails a lane that
spends job-hours with nothing new; DRY_ROUNDS fails a lane that logs run after run with nothing
new; RUBBER_STAMP fails a referee that passes everything. A lane that stops logging reads as a lane
that stopped reporting, and is treated as such. Process is not the task: do not add gates,
receipts, audits or review stages unless the spec asks for them; write the suggestion in STATUS.md
and keep doing the task.
<!-- /lane-rules-common -->
