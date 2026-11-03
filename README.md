# AI tools for research

Tools for running AI agents on open research problems and competitions without letting them overfit, stall or spam people. The research harness records ideas and evidence, checks lane rules, watches delivery cadence, and manages bounded workers. The [model leaderboard](LEADERBOARD.md) is one input to model routing.

A **lane** is one research problem with its own queue, rules, schedule and results. Lane state stays local. This repository contains code, synthetic examples and a dated model snapshot.

## Quickstart

Python 3.9 or later runs the harness using the standard library. Install pytest to run its tests. The optional job launcher needs zsh and an executor CLI; the toy preview uses neither an executor nor the network.

```sh
python3 examples/toy_demo.py --root .runtime/toy
python3 harness/rh.py --root .runtime/toy ideas list toy
python3 harness/rh.py --root .runtime/toy sweep toy --dry-run
python3 -m pytest -q
```

The demo creates an invented integer-sequence lane and previews search, read and kill-test instructions. Choose a fresh root on subsequent runs. A real sweep without `--dry-run` starts configured workers and can use remote sources. Do not enable workers until you have supplied your own access controls and lane policy.

To recover the dated model routes into a local file:

```sh
export RH_ROLES_FILE="$PWD/.runtime/roles.json"
python3 leaderboard/model-roles.py bootstrap --output "$RH_ROLES_FILE"
```

See [configuration](docs/configuration.md) before connecting an executor, remote pod, contact file or notification command.

## Enforced contracts

- [Sealed selection](docs/sealed-selection.md): promotion records the holdout declaration and freezes the test and pass line. Data access separation belongs to the lane's experiment environment.
- [Idea intake, sweeps and kill-tests](docs/idea-intake.md): append-only idea events, source screening, required rule files, bounded workers and result receipts.
- [Lane cadence, lint and alerts](docs/lane-cadence.md): delivery checks remain active when workers are disabled; lane-health alerts enter an agent queue.
- [Pods and harvesting](docs/pods.md): ownership, capacity receipts, atomic claims, deadlines and verified copies before supported shutdown paths.
- [Prior-art receipts](docs/prior-art.md): promotion requires structured source dispositions, a baseline and captured evidence.
- [Outreach](docs/outreach.md): draft, lint, approve exact bytes, then record a separate send. The harness has no send function.

The [science rules](tools/RULES.md) are a job-spec template. They distinguish requirements enforced by code from responsibilities of the experiment owner. The [harness reference](harness/README.md) lists commands and the [migration notes](harness/MIGRATION.md) explain changes from a machine-local installation.

## Limits

This is a Unix-oriented local harness, using file locks and processes. Reversible disposal defaults to macOS Trash; other systems need a compatible configured command. It is not an access-control sandbox, a competition platform or an independent judge of scientific truth. Holdout declarations, reviewer identities, human approval quotes and reported results are assertions with receipts; the harness cannot prove the person, model or experiment was honest. An exact verifier and a separate selection environment remain necessary.

The model snapshot reflects selected tasks and small samples. Seed orders are judgments, not measured superiority. An executor exit code is not a quality score. Model availability can change; a route entry does not prove access to a provider. See the [ranking method](leaderboard/README.md).

Optional hooks must be installed in the tool environment that performs the action. The capacity hook starts in audit mode. The deadline hook only recognizes command text; it cannot prove that a remote termination command will succeed. A stopped controller, missing scheduler, unavailable provider or missing credentials can still stall work. There is no installed watchdog, cloud account configuration or notification service in this repository.

## Credit and license

The separation of adaptive work from final evaluation is informed by [Dwork and coauthors on holdout reuse](https://arxiv.org/abs/1506.02629) and [Blum and Hardt on reliable competition leaderboards](https://arxiv.org/abs/1502.04585). The search/evaluate/retain pattern has related work in [AlphaEvolve](https://arxiv.org/abs/2506.13131). This code implements local workflow checks, not those papers' algorithms or guarantees. [Sources and attribution](docs/credits.md) describe the relationship.

[MIT license](LICENSE).
