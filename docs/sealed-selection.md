# Sealed selection

Producing agents should never see the data used to choose a final submission. Keep development data and final selection data under separate accounts or services. Give the selector immutable candidate artifacts and a fixed selection protocol. Return only the permitted decision to the producer. Limit selection queries in that service; a folder name or a prompt is not isolation.

`rh ideas promote` requires a board, test and pass line, and either `--sealed-holdout` or an explicit `--no-holdout-reason`. Exact certificate tasks can explain why they do not have a statistical holdout. Promotion freezes a digest of the declared test; result/config files are hashed. Changed bars require a new freeze before an attempt. Prior results and events remain visible.

A `board=none` item needs a route to a human, organizer or community and is marked non-runnable. Routing an idea is not admission to an experiment.

The harness records and lints these declarations. It does not open selection data, submit candidates, set filesystem permissions or limit selection-service calls. The toy lane includes no statistical selection dataset; its paid-worker example verifies exact arithmetic and exercises the receipt API. Banned metadata patterns and worker prompts help preserve a lane's rules but do not prevent a worker with excessive permissions from reading a file.

Related rationale: [adaptive holdout reuse](https://arxiv.org/abs/1506.02629) and [competition leaderboard overfitting](https://arxiv.org/abs/1502.04585). Neither paper's statistical mechanism is implemented here. Code: [idea_policy.py](../harness/idea_policy.py), [rh.py](../harness/rh.py). Tests: [test_idea_policy.py](../harness/tests/test_idea_policy.py).

The [selection package](../selection/README.md) supplies the numerical decision,
artifact-hash receipts, trajectory scanning and optional v2 evidence/ledger checks.
It is a library: a deployment must connect its independent scorer to the selected
API. Default v1 does not enforce a scoring budget or finalist cap. Opt-in
`score_and_decide` reserves a declared budget before scoring. See the
[ten-practice enforcement map](overfitting.md), including limits discovered by
checking the actual code rather than assuming the protocol is enforced.
