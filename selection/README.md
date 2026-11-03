# Sealed selection gate

Install the package with `python3 -m pip install ./selection`, or use the root
quickstart to install the test and numerical dependencies. From the repo root,
`PYTHONPATH=selection python3 -c "import selection_gate"` also works.
Python 3.9+ and NumPy are required. Run every test with `python3 -m pytest -q`.

## Default policy (version 1)

Develop on development targets. Freeze recipes and predictions before an
independent selector reads sealed targets. `decide(finalists, floor, n_candidates)`
requires at least two finite seed scores per candidate, chooses the highest mean,
and accepts only if mean − SD√(2 ln finalists) − 2 SEM strictly exceeds the floor.
The SEM multiplier defaults to 2 and is configurable. Historical search count and
its estimated optimism are recorded, but the historical correction is not applied.
The result selects one candidate or the floor; it does not upload anything.

`write_receipt` creates a new receipt exclusively; `verify_receipt` checks the
artifact SHA256. The caller must check board/run identity, independent PASS review,
and scientific validity. Receipts are ordinary files, not attestations.
`trajectory_scan` searches plain/gzipped text and decoded JSON for literal patterns.

**Limits:** version 1 does not enforce a three-finalist limit, freeze nominations,
count scoring calls, authenticate a reviewer, or isolate target data. The caller
must provide these controls. A write-once receipt alone cannot enforce sparing use
of a hidden set. See [the enforcement map](../docs/overfitting.md).

## Opt-in version 2

Pass `policy_version=2` explicitly to `decide`, with a policy declaration,
fresh-rerun evidence, a sealed-scoring ledger and an optimism ledger. The
[policy schema](selection_gate/policy.py) and [implementation](selection_gate/v2.py)
are the source of truth; [synthetic tests](tests/test_v2.py) demonstrate complete inputs.
The [harness guide](../harness/docs/selection.md) covers freeze, trial, scoring,
and lint integration. `RH_SELECTION_GATE` overrides the bundled package location.
No live run fixtures or lane datasets are included.

Version 2 validates group/time/source/random split declarations, fold-local
preprocessing and augmentation assertions, final-fit evidence, at least ten
measurements (or a reasoned waiver), the best 3–5 completed trials rerun on disjoint
seed/split IDs, paired independent-unit bootstrap intervals, and majority wins on
expanding backtests. An optional simple-average ensemble counts as another finalist.
It records search optimism and applies it to the reported expected score, while
eligibility still uses the sealed lower bound, bootstrap and backtests.

Use `score_and_decide` to reserve the hidden scoring budget *before* calling the
scorer, including when scoring raises. Calling `decide` with already-computed
scores cannot prevent earlier hidden-set reads. Ledgers are locked and hash-chained;
the owner must protect their files against replacement. Neither version verifies
that declared units, independence or training behavior correspond to reality.

Version 2 can rank several permitted submissions by mean − 1.96 SEM. The separate
advisory `best_of_choice` helper models an explicitly best-of-many board; it is not
used by the default gate or toy run, and requires scores on the same sealed scale.

Keep targets under a separate account/service and stop producing agents before
selection. This package does not create that boundary or connect itself to every
lane. The toy installation example exercises receipts with exact arithmetic only.
