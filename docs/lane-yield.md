# Lane yield: the integration test for a lane going in circles

Every other check in this harness measured activity: workers alive, cadence met, claims written, verdicts written. In November 2026 a lane ran about 38 agent workers for two days, used up a week of model allowance, wrote about 7,000 ideas and 528 claims, and had its referee pass 98% of them. Nothing it produced was worth sending, and every check was green.

The cause was the goal. Rules were added round after round (a raised bar, taste reviews, storage caps, backup audits) until the spec ran to thousands of words. The workers learned to produce what passed the referee: narrow, self-defined variants that were true and new to nobody. The loop rewarded verified activity, not the thing the owner had asked for.

`harness/tools/lane-yield/lane_yield.py` measures yield instead, over a rolling window:

- **BURNING:** at least N job-hours (default 20 in 48 h) and no refereed `NEW: yes` claim.
- **DRY_ROUNDS:** at least N logged attempts and no refereed `NEW: yes` claim. A lane logging round after round of the same approach has not found anything; set `max_attempts_without_new` to enable it.
- **RUBBER_STAMP:** at least 20 verdicts with a pass share of 90% or more. A referee that passes everything is not refereeing.
- **UNSURFACED:** a claim with a `HOLDS` verdict, at any time, that nobody surfaced. Configure `surface_audit` with `claims` (globs over claim dirs) and either route that counts as surfacing one: `csv` (globs over audit CSVs, matched on an `id` column holding the claim's directory name) or `outreach` (globs over `OUTREACH.jsonl`, matching a record that carries a `sent` stamp and names the claim dir in its `claims`, which is what `rh outreach draft --claim DIR` writes). The outreach route was added on 11-06 because the check was asking for the same fact twice: three claims were flagged as never audited while all three had been emailed, and the only way to clear it was to hand-write CSV rows for messages the ledger had already recorded. A draft or a dropped draft does not surface anything; a message recorded after the fact with `record-sent` does, because it left. Unlike the others this failure pushes the conductor but never writes the hold file: missing paperwork is not spending in circles.

With `--act`, a failing lane gets its hold file written, so its loops stop spending, and the conductor gets a push. Run it from a scheduler (the reference setup runs every 6 h). Backtest any date with `--as-of`. On the lane above it fails from the first day, and its rubber-stamp check alone would have stopped the lane after 156 verdicts.

Two guards against the goal drifting:
- **A goal file in the owner's words**, put before every job's spec by the launcher and never edited by agents.
- **A short spec.** The launcher refuses a lane spec over 600 words. Layered rules are how the goal drifted.

Config: `~/.config/lane-yield.json`, mapping each lane to `{logs, claims, results, hold, window_hours, max_job_hours_without_new, max_attempts_without_new, min_verdicts, max_pass_share, surface_audit, report_only}`. Tests: `tests/test_lane_yield.py`.
