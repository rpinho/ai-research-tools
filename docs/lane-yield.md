# Lane yield: the integration test for a lane going in circles

Every other check in this harness measured activity: workers alive, cadence met, claims written, verdicts written. In November 2026 a lane ran about 38 agent workers for two days, used up a week of model allowance, wrote about 7,000 ideas and 528 claims, and had its referee pass 98% of them. Nothing it produced was worth sending, and every check was green.

The cause was the goal. Rules were added round after round (a raised bar, taste reviews, storage caps, backup audits) until the spec ran to thousands of words. The workers learned to produce what passed the referee: narrow, self-defined variants that were true and new to nobody. The loop rewarded verified activity, not the thing the owner had asked for.

`harness/tools/lane-yield/lane_yield.py` measures yield instead, over a rolling window:

- **BURNING:** at least N job-hours (default 20 in 48 h) and no refereed `NEW: yes` claim.
- **RUBBER_STAMP:** at least 20 verdicts with a pass share of 90% or more. A referee that passes everything is not refereeing.

With `--act`, a failing lane gets its hold file written, so its loops stop spending, and the conductor gets a push. Run it from a scheduler (the reference setup runs every 6 h). Backtest any date with `--as-of`. On the lane above it fails from the first day, and its rubber-stamp check alone would have stopped the lane after 156 verdicts.

Two guards against the goal drifting:
- **A goal file in the owner's words**, put before every job's spec by the launcher and never edited by agents.
- **A short spec.** The launcher refuses a lane spec over 600 words. Layered rules are how the goal drifted.

Config: `~/.config/lane-yield.json`, mapping each lane to `{logs, claims, hold, window_hours, max_job_hours_without_new, min_verdicts, max_pass_share}`. Tests: `tests/test_lane_yield.py`.
