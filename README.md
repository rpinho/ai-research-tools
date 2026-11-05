# AI tools for research

Tools for running AI agents on open research problems and competitions with checks against overfitting, stalled work and unapproved outreach. The research harness records ideas and evidence, checks lane rules, watches delivery cadence, and manages bounded workers. The [model leaderboard](LEADERBOARD.md) is one input to model routing.

A **lane** is one research problem with its own queue, rules, schedule and results. Lane state stays local. This repository contains code, synthetic examples and a dated model snapshot.

Agents: start with [AGENTS.md](AGENTS.md), the single source of agent instructions. Standard method first, and every result answers one question: did it find something new, yes or no? See [normal first](docs/normal-first.md).

## What it has produced (as of 2026-11-05)

All of it unrefereed unless stated. Where a result was reviewed, the reviewer was another model, not a human expert. These are agent runs under this harness, directed by one person.

**Mathematics.** Six bounds from this work are merged into Terence Tao's [optimization-constants table](https://github.com/teorth/optimizationproblems), among them the Schur–Siegel–Smyth trace constant C86 ≤ 1.8117251 ([PR #227](https://github.com/teorth/optimizationproblems/pull/227); the previous bound was 1.8216, Orloski and Talebizadeh Sardari) and the Zhang–Zagier height C82 < 0.2534449 ([PR #229](https://github.com/teorth/optimizationproblems/pull/229), building on Doche and on Épi/BakeLab's 0.25357). Sixteen more are open, several of them improving the merged values: [the list](https://github.com/teorth/optimizationproblems/pulls?q=is%3Apr+author%3Arpinho). Certificates and checkers are indexed at [math-certificates](https://github.com/rpinho/math-certificates). The search agent never grades its own certificate: a separate exact checker does, and deliberately corrupted certificates have to be rejected (4 for C86, 18 for C68). Public postings on Hexagon: C86 [2610.00425](https://hexagonmath.org/2610.00425), C82 [2610.00426](https://hexagonmath.org/2610.00426), Korenblum's c2 ≥ 0.432 [2610.00399](https://hexagonmath.org/2610.00399), and C44, C86 and C68 at [2610.00694](https://hexagonmath.org/2610.00694), [2610.00695](https://hexagonmath.org/2610.00695) and [2610.00696](https://hexagonmath.org/2610.00696).

**Exoplanets.** Four candidate reports from NASA TESS data, Research Notes under review at RNAAS: TOI-6000 c (1.15 R⊕, 4.21 d), a third candidate at TOI-4342 (18.9 d, first publicly reported by Eumemic and credited), TIC 144439305 (1.2 R⊕, 0.94 d), and four signals across three targets, TOI-4405, TIC 101653309 and TIC 262843927, the last of which carries two. Pipeline: [planet-hunt](https://github.com/rpinho/planet-hunt). All four notes are being corrected after the numbers were re-audited: the TOI-6000 c correction is published ([10.5281/zenodo.23822096](https://doi.org/10.5281/zenodo.23822096)) and three are in progress, and the pipeline's own [retro](https://github.com/rpinho/planet-hunt/blob/main/RETRO.md) says what changed and why. The lane's own lesson: cross-match the ExoFOP TOI and CTOI tables and the community lists before calling anything new, and run injection-recovery.

**Arc Virtual Cell Challenge 2026.** [virtual-cell-challenge](https://github.com/rpinho/virtual-cell-challenge): best official validation score 0.1644, rank about 463 of about 1,580 (2026-10-21). The final entry went in on 2026-10-23 and stood as the final when the challenge closed on 2026-11-05; its score is pending. The useful result is a negative one. A local proxy score ranked two candidates in the opposite order to the official scorer, so the official metric is the only judge. A split-half noise ceiling (method credited to @zymobyte012) puts two halves of the real data at cosine 0.65 while the predictions reach 0.08, about 10% of that ceiling.

**Adaptyv protein design.** [adaptyv-challenges](https://github.com/rpinho/adaptyv-challenges): in challenge 4 (TfR1 binder), 342 designs were scored with AlphaFold2-multimer, five models each, against the TfR1 homodimer construct. iPTM variance between the five models of one design was comparable to the spread between designs, so designs were filtered for agreement across all five rather than ranked by peak iPTM. In challenge 5 (KRAS G12D pMHC binder), neither AF2-multimer nor Boltz-2 confidence could separate G12D from wild type.

Friend outreach from the outreach lane is private and appears nowhere in this repository.

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
- [Outreach](docs/outreach.md): draft, lint, approve exact bytes, then record a separate send. The harness has no send function. Lint gates a result email on an independent re-check of every claim it reports (`RECHECK_MISSING`, `RECHECK_FAILED`, `RECHECK_DROPPED`), on pod-touched evidence having been re-run after a configured host incident (`POD_EVIDENCE_STALE`), and on the two-language rule when the recipient's contact language is not English.
- [Lane yield](docs/lane-yield.md): each lane logs every run to its own `RESULTS.jsonl` through `log-result`. `BURNING` fails a lane that spends job-hours with nothing new, `DRY_ROUNDS` a lane that logs run after run with nothing new, `RUBBER_STAMP` a referee that passes almost everything, and `UNSURFACED` a `HOLDS` claim that never reached an audit ledger.
- [One source for the installed copy](docs/harness-sync.md): `harness-sync` installs only committed `main`, reports drift as MISSING, BEHIND, EDITED, STRAY or REMOVED, and refuses to install while a harness job runs. The installed copy is never edited in place, and the rules every lane carries come from [LANE_RULES_COMMON.md](harness/LANE_RULES_COMMON.md) through `lane-rules-sync`.

Scheduled scripts carry what prose did not: a lane that goes quiet gets a [resume prompt](docs/lane-nudge.md), a lane going in circles fails [lane yield](docs/lane-yield.md), and agents wake the conductor only through the [conductor inbox](docs/conductor-inbox.md).

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
