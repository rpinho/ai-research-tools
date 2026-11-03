# Anti-overfitting: enforcement map

This map rates the **default version-1 gate and ordinary harness**, with optional
v2 behavior stated separately. A check on a declaration cannot verify training
behavior. No policy creates OS-level read isolation. Line numbers refer to the
shipped code. The copied shared package included v2; it is not silently enabled.

| Practice | Default status | Verified enforcement and limits (file:line) |
|---|---|---|
| 1. Make validation mimic the test, using group/time splits. | PARTLY | `harness/idea_policy.py:97` freezes a lane's holdout disposition and pass line, not a split algorithm. There is no generic default group-aware split. Opt-in `selection/selection_gate/policy.py:48` requires split/reason and fold-local preprocessing declarations; it cannot prove the declared split matches test sampling. |
| 2. Average seeds and penalize instability using SEM. | DONE | `selection/selection_gate/__init__.py:43` requires at least two finite scores, uses sample SD and SEM; `selection/selection_gate/__init__.py:53` picks the highest mean and computes mean − SD√(2 ln finalists) − 2 SEM by default. The SEM multiplier is configurable. Seeds need not represent independent scientific units. V2 requires ten measurements or a reasoned waiver (`selection/selection_gate/policy.py:77`). |
| 3. Augment only training folds. | NOT DONE | Default `selection/selection_gate/__init__.py:38` consumes scores and never inspects training. Lane-specific. V2 checks training-only and label-preserving augmentation assertions (`selection/selection_gate/policy.py:56`), not actual data transformations. |
| 4. Take care when retraining on all data. | NOT DONE | Default `selection/selection_gate/__init__.py:38` has no retraining inputs. V2 checks the final-fit declaration and paired non-degradation evidence for epoch retraining (`selection/selection_gate/policy.py:64`); it does not train or verify the experiment. |
| 5. Ensemble diverse models. | NOT DONE | The default selects an individual candidate (`selection/selection_gate/__init__.py:53`). Opt-in `selection/selection_gate/v2.py:136` averages predictions and counts the ensemble toward finalists; it does not establish model diversity. |
| 6. Successive backtests and learning curves. | NOT DONE | No default backtest or learning-curve requirement (`selection/selection_gate/__init__.py:17`). V2's expanding-window helpers and majority-win check exist (`selection/selection_gate/v2.py:267`), but learning curves are not enforced. |
| 7. Beat a baseline using bootstrap over independent units. | PARTLY | Default strict lower bound must beat `floor` (`selection/selection_gate/__init__.py:57`), without any independent-unit bootstrap. Seed noise alone is not replication. V2 bootstraps paired declared units and requires a positive CI (`selection/selection_gate/v2.py:273`); real independence remains the caller's responsibility. |
| 8. Count every trial; rerun the best few on fresh seeds/splits. | PARTLY | Historical count and search optimism are recorded but not applied in v1 (`selection/selection_gate/__init__.py:59`); no trial ledger or fresh-seed enforcement. V2 validates completed/pruned trials and the best 3–5 finalists (`selection/selection_gate/v2.py:211`), disjoint declared IDs, and reports an optimism-adjusted expected score (`selection/selection_gate/v2.py:281`). That expected score is not the eligibility threshold. The claimed default cap of three is absent. |
| 9. Keep a fixed hidden set and use it sparingly. | PARTLY | A receipt path cannot be overwritten (`selection/selection_gate/__init__.py:72`), but default `decide` neither scores data nor limits calls or finalist count. Thus “scored once, at most three finalists” is a deployment protocol, **not a default guarantee**. V2 `score_and_decide` reserves a declared budget before invoking the scorer (`selection/selection_gate/v2.py:334`), even on failure, and binds set/budget in its ledger; it still requires protected storage and external isolation. |
| 10. Choose among several submissions. | NOT APPLICABLE (default) | V1 returns one candidate or floor (`selection/selection_gate/__init__.py:61`) and performs no upload. V2 can explicitly rank several eligible outputs by mean − 1.96 SEM (`selection/selection_gate/v2.py:282`). `best_of_choice` is a separate advisory helper for a specifically best-of-many board; neither it nor multiple submissions is used by the toy/default gate. |

Two corrections to the initial evidence are deliberate: v1 has no three-finalist
cap, and exclusive receipt creation does not enforce one-time hidden scoring.
Calling v2 `decide` with already computed scores cannot police their earlier
computation either. The pre-scoring wrapper is the appropriate optional boundary.
Tests: `selection/tests/test_gate.py`, `selection/tests/test_v2.py`,
`selection/tests/test_best_of.py`; run all with `python3 -m pytest -q`.
