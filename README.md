# AI tools for research

Tools for running AI agents on open research problems and competitions with checks against overfitting, stalled work and unapproved outreach. The research harness records ideas and evidence, checks lane rules, watches delivery cadence, and manages bounded workers. The [model leaderboard](LEADERBOARD.md) is one input to model routing.

A **lane** is one research problem with its own queue, rules, schedule and results. Lane state stays local. This repository contains code, synthetic examples and a dated model snapshot.

Agents: start with [AGENTS.md](AGENTS.md), the single source of agent instructions.

## Quickstart

Start in a clone or extracted copy of this repository. Python 3.9 or later runs
the harness using the standard library; the selection gate also needs NumPy.
Run the idempotent bootstrap to create a virtual environment and install dependencies.
The paid worker example needs zsh and an authenticated executor CLI on PATH.
The toy preview uses neither an executor nor the network.

```sh
scripts/bootstrap.sh
. .venv/bin/activate
python3 examples/toy_demo.py --root .runtime/toy
python3 harness/rh.py --root .runtime/toy ideas list toy
python3 harness/rh.py --root .runtime/toy sweep toy --dry-run
python3 -m pytest -q
```

For a user-owned problem, follow [new-lane](.claude/skills/new-lane/SKILL.md): `rh --root .runtime lane new NAME --help` creates frozen v2 declarations; then lint and preview using the commands in [AGENTS.md](AGENTS.md). New lanes keep real workers disabled and have at most 24 hours of setup lint grace; no external reading or results are claimed.

The demo creates an invented integer-sequence lane and previews search, read and kill-test instructions. Choose a fresh root on subsequent runs. A real sweep without `--dry-run` starts configured workers and can use remote sources. Do not enable workers until you have supplied your own access controls and lane policy.

To recover the dated model routes into a local file:

```sh
export RH_ROLES_FILE="$PWD/.runtime/roles.json"
python3 leaderboard/model-roles.py bootstrap --output "$RH_ROLES_FILE"
```

See [configuration](docs/configuration.md) before connecting an executor, remote pod, contact file or notification command.

## Run one real toy worker

Install [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) or
[Claude Code](https://code.claude.com/docs/en/setup) separately. Each provider
requires an account with model access. Authenticate **in the same HOME and
environment that will run the worker**: `codex login` for Codex, or `claude auth login`
for Claude Code. Check with `codex login status` or `claude auth status`.
[Codex authentication](https://learn.chatgpt.com/docs/auth) also supports an API key
piped to `codex login --with-api-key`; keep credentials outside this repository.
For automated Claude authentication, supply your own `CLAUDE_CODE_OAUTH_TOKEN`
(from `claude setup-token`) or `ANTHROPIC_API_KEY` through your secret manager.
Do not copy credentials from another HOME as an installation shortcut.

After the quickstart, choose either command, or run both with separate roots:

```sh
AGENT_BACKEND=codex python3 examples/toy_worker.py --root .runtime/toy-codex
AGENT_BACKEND=claude python3 examples/toy_worker.py --root .runtime/toy-claude
```

Each command starts exactly one worker through `tools/launch.sh`, asks it to write
an integer sequence and search/read/kill reports, and verifies the exact result.
It writes `receipts/toy.json`, a hash-bound `receipts/selection.json`, model selection
and outcome events in `jobs/toy-worker.log`, the provider output in
`jobs/toy-worker.last.md`, and a finished registry record under the chosen root.
A fresh root is mandatory. The toy uses `gpt-6.1-sol` or `claude-haiku-5-5`, low
effort and a three-minute timeout. Claude has a $1 per-run CLI budget and no
launcher retries; actual pricing and account access depend on the provider.
Codex has no dollar cap in this launcher. The toy claims no statistical holdout.

For general jobs set `AGENT_BACKEND=codex|claude` (default `codex`) and supply
compatible routes through `RH_ROLES_FILE`. There is no automatic backend/model
substitution. Claude uses `acceptEdits`, preapproved file tools and an empty MCP
configuration; it has no OS sandbox supplied by this runner. Codex uses
`workspace-write`. Both inherit caller authentication. A clean HOME excludes
personal context but requires its own login. If that login is unavailable, a
fresh clone outside personal directories with an existing login is a useful
integration test, but does not prove authentication in an empty HOME.

## Enforced contracts

- [Sealed selection](docs/sealed-selection.md): a [selection package](selection/README.md) computes conservative decisions and hashes artifacts; promotion freezes a test/pass line. The opt-in [v2 harness policy](harness/docs/selection.md) adds frozen declarations, trial accounting and replayed scoring evidence. The [overfitting map](docs/overfitting.md) distinguishes defaults, optional checks and missing controls.
- [Idea intake, sweeps and kill-tests](docs/idea-intake.md): append-only idea events, source screening, required rule files, bounded workers and result receipts.
- [Lane cadence, lint and alerts](docs/lane-cadence.md): delivery checks remain active when workers are disabled; lane-health alerts enter an agent queue.
- [Pods and harvesting](docs/pods.md): ownership, capacity receipts, atomic claims, deadlines and verified copies before supported shutdown paths.
- [Prior-art receipts](docs/prior-art.md): promotion requires structured source dispositions, a baseline and captured evidence.
- [Verification levels L0–L3](docs/verification.md): scoped whole-result and finite-component claims, hashed checker or Lean receipts, and opt-in close/lint checks.
- [Outreach](docs/outreach.md): draft, lint, approve exact bytes, then record a separate send. The harness has no send function.

The [science rules](tools/RULES.md) are a job-spec template. They distinguish requirements enforced by code from responsibilities of the experiment owner. The [harness reference](harness/README.md) lists commands and the [migration notes](harness/MIGRATION.md) explain changes from a machine-local installation.

## Limits

This is a Unix-oriented local harness, using file locks and processes. Reversible disposal defaults to macOS Trash; other systems need a compatible configured command. It is not an access-control sandbox, a competition platform or an independent judge of scientific truth. Holdout declarations, reviewer identities, human approval quotes and reported results are assertions with receipts; the harness cannot prove the person, model or experiment was honest. An exact verifier and a separate selection environment remain necessary.

The model snapshot reflects selected tasks and small samples. Seed orders are judgments, not measured superiority. An executor exit code is not a quality score. Model availability can change; a route entry does not prove access to a provider. See the [ranking method](leaderboard/README.md).

Optional hooks must be installed in the tool environment that performs the action. The capacity hook starts in audit mode. The deadline hook only recognizes command text; it cannot prove that a remote termination command will succeed. A stopped controller, missing scheduler, unavailable provider or missing credentials can still stall work. There is no installed watchdog, cloud account configuration or notification service in this repository.

## Credit and license

The separation of adaptive work from final evaluation is informed by [Dwork and coauthors on holdout reuse](https://arxiv.org/abs/1506.02629) and [Blum and Hardt on reliable competition leaderboards](https://arxiv.org/abs/1502.04585). The search/evaluate/retain pattern has related work in [AlphaEvolve](https://arxiv.org/abs/2506.13131). This code implements local workflow checks, not those papers' algorithms or guarantees. [Sources and attribution](docs/credits.md) describe the relationship.

[MIT license](LICENSE).

## Shared lessons and field retros

See [sharing lessons](docs/lessons.md) for `rh lessons`, automatic sweep/new-lane
reads, opt-in close requirements, and the local-only `rh retro` workflow.
