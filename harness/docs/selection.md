# Selection policy version 2

Version 2 makes the following practices admission checks. It is **explicitly opt-in**:
use `decide(..., policy_version=2, ...)` or `rh ideas promote/freeze
--policy-version 2 --selection-policy protocol.json`. Version 1 remains the default.
Existing frozen receipts, gate decisions, lane configurations and submission behavior
retain their original contract. No automatic migration or timestamp cutover occurs.

These checks address validation leakage, unstable measurements, repeated hidden
scoring, and search optimism. They validate declared evidence; they cannot prove
that an external training process followed its declarations.

The harness loads the bundled `selection/` package by default. Set
`RH_SELECTION_GATE` to another installed package root when needed; `~` expands
in this path. V2 requires NumPy; ordinary version-1 commands remain standard
library only. See the [enforcement map](../../docs/overfitting.md) for the default
versus v2 distinction.

## Checks

| Practice | Enforced v2 behavior |
| --- | --- |
| 1. Split mimics the test | Freeze requires `split: group\|time\|source\|random`, one-line `split_reason`, and `preprocessing_inside_folds: true`. Random additionally requires `test_shares_units: true` and a nonempty `test_shares_units_statement`. |
| 2. Measurements and SEM | Default minimum is 10 independent measurements per finalist, with unique measurement IDs and distinct seeds, or distinct backtest splits. All finalists and the baseline use the same IDs. A lower minimum (at least 2) requires `measurement_waiver: {"reason": "..."}`; every decision reports it. The lower bound stays `mean − SD × sqrt(2 ln k) − 2 × SEM`. |
| 3. Training-only augmentation | Freeze must declare `augmentation_used` as a boolean. If true, require `augmentation_train_only: true`, `augmentation_label_preserving: true`, and nonempty `augmentation_transforms`. Every v2 result config reports `augmentation_used`; result admission and lint reject undeclared augmentation, including populated `augmentation`, `augmentations`, or `augmentation_transforms` fields. |
| 4. Final fit | Freeze requires `final_fit: fold_ensemble\|fixed_steps\|retrain_epochs`. `retrain_epochs` requires paired `retrain_backtest.fold_scores` and `.retrain_scores` on at least two splits; conservatively, no observed split may deteriorate. |
| 5. Ensemble | Optional `ensemble_scorer` averages aligned predictions, then scores the average. It creates one extra candidate named `simple_average`, which counts toward k. Caller-supplied ensemble candidates and fitted weight fields are rejected. Both sealed and backtest predictions must align across members; backtest windows may differ in size. |
| 6. Successive backtests | `backtests` fits/evaluates candidates through an operator callback on expanding windows with disjoint held-out units. V2 evidence must supply the window manifest. Each training window includes the previous train and test units. Every candidate must strictly beat the paired baseline by `backtest_margin` (default 0) in more than half the splits. Every decision reports wins, differences, and the worst split and score. |
| 7. Baseline and bootstrap | `bootstrap_diff(candidate, baseline, units, n=2000)` averages paired differences within units and resamples those independent unit means, giving every unit equal weight. At least two distinct units are required. V2 uses 2,000 deterministic resamples and accepts only when the 95% CI's lower endpoint is **strictly positive**, the lower bound exceeds the floor, and backtests pass. |
| 8. All trials, fresh reruns, optimism | Freeze records `n_trials_total` plus any initial trial records. Append every completed and pruned trial with `rh selection trial`; lint rejects removal/replacement. Scoring requires all recorded trials and the best 3–5 completed candidates, rerun with fresh seeds **and** splits disjoint from tuned IDs, plus an explicit untouched-data/independence declaration. Each row reports `expected = mean − max(search_optimism, measured_prior)`. The decision records tuned-minus-fresh drops in per-lane `OPTIMISM.jsonl`. The prior is the nonnegative average of previous competition means, excluding the current competition. Lint reconstructs the prior from the historical ledger prefix. |
| 9. Fixed, sparse hidden scoring | V2 scoring requires one per-lane `SEALED_SCORING.jsonl`, a fixed `sealed_set_id` within each competition, unique scoring IDs, and an immutable lane budget. Every v2 scoring invocation consumes one evaluation of the complete finalist batch (including its baseline and optional ensemble). Over-budget attempts remain in the ledger and fail lint. `score_and_decide` reserves the use **before** invoking an actual sealed scorer, so exceptions also consume the budget and over-budget callbacks never run. |
| 10. Multiple submissions | After all acceptance checks, rank eligible candidates by `mean − 1.96 × SEM`, with input-order ties, and return up to `submissions` names. No safe-plus-ambitious heuristic. The harness does not submit anything. |

Use higher-is-better scores throughout (negate loss before calling). k includes
fresh rerun finalists and the optional ensemble; `n_candidates` counts all search
trials, including pruned trials, and is distinct from k. SD is sample SD and
SEM is SD / sqrt(number of independent measurements). Search optimism is
`SD × sqrt(2 ln n_trials_total)`. The measured prior is a lower bound on the
expected-score correction; taking the maximum avoids counting the same
optimism twice. Selection still uses the existing lower-bound formula plus CI
and backtest checks, rather than applying the historical search penalty twice.

## Freeze and lane limits

A minimal `protocol.json` before any search:

```json
{
  "policy_version": 2,
  "split": "group",
  "split_reason": "The test holds out subjects, so folds hold out subjects.",
  "preprocessing_inside_folds": true,
  "augmentation_used": false,
  "final_fit": "fixed_steps",
  "n_trials_total": 0,
  "sealed_set_id": "competition-fixed-subjects-sha256",
  "sealed_budget": 3
}
```

For searches already begun, also include `trials`, a list of all initial trials,
whose length must equal `n_trials_total`. Each has unique nonempty `id` and
`name`, `status: completed|pruned`, and a finite `tuned_score` for completed
trials (optional for pruned trials). The receipt snapshots this initial list;
subsequent rows append trials to `trial_records` and increase `n_trials_total`.
Initial count 0 is valid; scoring requires at least three completed candidates.
Trials are configurations; repeating measurements is not an extra configuration.

Optional top-level `INTAKE.toml` keys `selection_min_measurements`,
`selection_sealed_budget`, and `selection_submissions` must match the frozen
policy's `min_measurements` (default 10), `sealed_budget`, and `submissions`
(default 1). All v2 ideas in a lane share one sealed budget. Budget counts
accumulate across that ledger; extending or rotating it requires an explicit
operator migration outside this API, not changing the next receipt. No live lane
configuration is changed by installing this code. Without lane keys, the frozen
declaration supplies the limits. These checks also apply to explicitly v2 ideas
in lanes exempt from historical prior-art admission.

Example commands (substitute an existing idea and actual evidence):

```sh
rh ideas promote LANE IDEA --board BOARD --test 'paired subject metric' \
  --bar '> frozen baseline' --prior-art prior.json --sealed-holdout \
  --decided-by MODEL --policy-version 2 --selection-policy protocol.json
rh selection trial LANE IDEA --trial-id t1 --name config1 \
  --status completed --tuned-score 60.2
rh selection trial LANE IDEA --trial-id t2 --name config2 --status pruned
rh selection score LANE IDEA --evidence fresh-evidence.json --output decision.json
rh lint --all
```

`selection score` does not launch a worker, rerun a model, mark an idea shipped,
or submit predictions. It consumes operator-produced metric evidence through the
gate, writes an exclusive decision artifact, and appends its input/output hashes
and ledger anchors to the idea history. Lint replays the decision without writing
to ledgers. A v2 shipped idea must have an accepted decision. Existing result
admission, freeze integrity, and prior-art checks continue to apply.

## Fresh evidence

`fresh-evidence.json` contains `finalists`, `floor`, `n_candidates`, and `evidence`.
Optional `margin_sd` must equal 2; it declares the two-sided 95% convention, and the
multiplier actually applied is the Student t quantile for the number of measurements,
recorded per finalist as `margin_quantile` and never below 1.96. Each finalist contains:

- `name`, matching a completed trial; `sealed_scores` (at least 10 by default).
- `measurement_ids`, exactly equal to `evidence.fresh.measurement_ids` in order.
- `unit_scores`, paired row metric contributions for the sealed independent-unit
  bootstrap. They must align with `baseline_unit_scores` and `units`.
- `backtest_scores`, aligned with the shared fresh backtest split schedule.

The `evidence` object contains:

- `competition`, unique `scoring_id`, and the complete `trials` list. The CLI
  requires it to equal the idea's accumulated trial history.
- Nonempty unique `tuned_seed_ids`, `tuned_split_ids`.
- `fresh`: `nobody_tuned_against: true`, `independent_measurements: true`,
  `measurement_kind: seeds|backtests`, unique `measurement_ids`, `seed_ids`,
  `split_ids`. Fresh seed and split IDs must be disjoint from tuned IDs.
- `units`: independent subject/group/stage IDs, one per row metric contribution.
  Repeated labels are grouped before bootstrap, never sampled as separate units.
  `baseline_unit_scores` pairs with these same observations, and
  `baseline_measurement_ids` must match the fresh schedule.
- Optional `baseline_sealed_scores`: the baseline's own score on each sealed
  measurement, in `measurement_ids` order. Supplying it adds the paired
  comparison from practice 2 as an acceptance condition, so a candidate must beat
  the baseline on the matched seeds and windows and not only on average. A seed or
  an evaluation window moves both together, so the paired interval is usually much
  narrower than the unpaired one; each finalist's row reports `narrowing`.
- Optional `sealed_labels`: the truth for the sealed items, which lets the
  simple-average ensemble report the members' mean pairwise **error** correlation
  as well as their label-free disagreement. `diversity`, `subgroup_scores` and
  `diversity_by_subgroup` are refused: diversity is measured from the forecasts,
  never declared, and never argued from subgroup performance, which reuses labels.
- `baseline_backtest_scores`, unique `backtest_split_ids` drawn from the fresh
  split IDs, and `backtest_splits` in the same order. Each split has nonempty
  `train_units` and `test_units`; held-out units cannot overlap training or any
  previous test window. Training must expand, never slide backward.

With `measurement_kind: seeds`, each measurement requires a distinct seed.
With `measurement_kind: backtests`, measurement IDs equal the backtest split IDs
and sealed scores equal the actual per-split backtest scores. Each held-out
window counts once; correlated rows within a subject cannot inflate the count.

For the CLI's optional `simple_average_ensemble: true`, finalists additionally
include `sealed_predictions` and `backtest_predictions`. Each prediction array
contains additive per-row metric contributions, so the mean is the metric.
For nonlinear metrics use the Python API with a scorer that computes the metric
on averaged predictions; it can return `{"score": scalar, "unit_scores": [...]}`.
It must compute unit contributions appropriate for the paired bootstrap as well
as the official scalar. Never average precomputed scalar metrics as an ensemble.

## Python integration

```python
from selection_gate import decide, backtests, bootstrap_diff, score_and_decide

# Operator-side scoring: budget recorded before access to hidden labels.
decision = score_and_decide(
    score_all_finalists, floor, n_trials_total,
    policy=protocol, evidence=evidence,
    ledger_path=lane / "SEALED_SCORING.jsonl",
    optimism_path=lane / "OPTIMISM.jsonl",
)

# If scoring has already occurred outside the wrapper, log/validate its evidence
# as one v2 evaluation. Do not call both entry points for the same scoring job.
decision = decide(
    finalists, floor, n_trials_total, policy_version=2,
    policy=protocol, evidence=evidence,
    ledger_path=lane / "SEALED_SCORING.jsonl",
    optimism_path=lane / "OPTIMISM.jsonl",
)

# Callback fits preprocessing and the candidate using only train_units.
report = backtests(candidates, ordered_unit_windows, fit_and_score,
                   baseline="baseline", min_train=2, test_size=1, margin=0.1)
```

The gate requires NumPy. The legacy harness remains stdlib-only; v2 loads the
bundled `selection/` package lazily. Set `RH_SELECTION_GATE` to another package
root when installing elsewhere.

## Scope of enforcement

Declarations, IDs, counts, numeric evidence, histories, artifacts and ledger
bindings are checked. The harness cannot establish the scientific truth of a
split rationale, that two distinct IDs represent independent physical units,
that an unseen code path fits preprocessing only inside folds, that a transform
preserves labels, or that nobody privately tuned against fresh data. The
operator must enforce those data-access and execution boundaries. Instrumented
v2 result configs must report augmentation; arbitrary uninstrumented external
scripts cannot be discovered from a receipt.

Scoring outside v2 APIs (including historical version-1 calls) is intentionally not
intercepted or retrospectively charged. Lanes must opt in before using new
accounting. Ledger hash chains detect record edits; idea-history anchors detect
removal of recorded decisions or their ledger records. They are local integrity
checks, not a signed external audit service. No automatic sweep integration,
competition-specific scorer, training recipe, deployment or submission was added.
