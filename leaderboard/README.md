# Model routing and scoring

[`LEADERBOARD.md`](../LEADERBOARD.md) is the sole committed leaderboard data artifact. It was generated from the current role-routing data using an explicit export allowlist. It contains models, roles, routing order, held-outcome scores, Wilson lower scores and the source snapshot date. A second table retains active 0–4 observation scores grouped only by recognized role, model and date. It includes operational/proxy labels and preserves repeated scores; observations without a recognized public model or role are omitted. Unranked observations have a dash in routing order; unscored models have dashes in score columns. No task text, case identifiers, prompts, costs or personal overrides are exported. `public` includes active scores from the configured local model ledger when that file exists; it validates the ledger before exporting.

The snapshot describes one selected set of local outcomes. Its seeded order is a routing preference and may differ from sorting by measured score. Individual source evidence, task distribution and the original outcome judgments are not shipped, so the scores cannot be independently verified from this snapshot. The 0–4 lists expose only selected recorded labels, not their context. Bootstrap recovers routes, not the private evidence ledger. A new deployment starts its own evidence history.

```sh
export RH_ROLES_FILE="$PWD/.runtime/roles.json"
python3 leaderboard/model-roles.py bootstrap
python3 harness/worker_models.py --role batch --executor codex \
  --log .runtime/example.log --receipt .runtime/selection.json
```

Selection refuses an incompatible executor or excluded model; it never silently skips to a lower-ranked provider. A fixed model needs `--model-override REASON`. Completion receipts retain the actual model and roles-file hash even if routes change mid-job. An exit code stays unscored.

## Local scoring

`model-eval.py` validates private observation ledgers: anchored scores 0..4, evidence references, finite optional costs and append-only supersession. It can print a local diagnostic including task and cost fields. That diagnostic is private; it is never used as the public Markdown export.

`model-roles.py ingest` requires at least one explicit evidence path. `RH_EVIDENCE_ROOT` selects a harness root and reads idea histories only there. A taste promotion is held when it ships, overturned when a later structured close identifies prior art, duplication or a rule violation. A failed kill-test does not by itself mean the proposal was a mistake. An unreversed judgment close settles after 14 days. The generic proposer adapter separately credits `proposed_by` for reaching testing, even if a candidate later fails its own bar. Optional review receipts under `MODEL_REVIEW_ROOT` score flags against revised document bytes; `MODEL_DRAFTS_LOG` scores explicit sent-as-drafted/rewritten/dropped outcomes without reading recipients. All inputs require explicit paths. Other sources can supply validated local observations directly; no personal archive, transcript, contact or account discovery adapters are included.

Only one process should write the model observation ledger at a time. It is validated before recomputing; malformed observations refuse ranking instead of disappearing.

`recompute` requires at least 5 resolved observations on 2 distinct days before a challenger can move on evidence. It passes an adjacent model only when its 95% Wilson lower bound exceeds that model's held rate. A model without sufficient evidence has seed rate 0.75. Ideation prioritizes distinct labs in its first two picks where possible. Configured exclusions are explicit; evidence can lift one only at 20 observations under the ranker's rule. Overrides are written to local routes and omitted from public exports.

```sh
export RH_EVIDENCE_ROOT=/path/to/private/runtime
python3 leaderboard/model-roles.py daily
python3 leaderboard/model-roles.py public --output /path/to/review/LEADERBOARD.md
```

`model-roles.sh` wraps the local daily command. The compatibility filename `model-roles-publish.sh` only generates a local export; it has no Git, upload or network-publication step. Review and scan exports before publishing them yourself.

`model-mine.py --archive ROOT --out DIR` deterministically scans supplied bookmark JSON under `ROOT/raw/bookmarks/`. It prints file/item denominators and writes local text dumps; those dumps can contain personal material and must remain outside public content. Mining output never flows into the public exporter automatically.

The ranker uses the [Wilson score interval](https://doi.org/10.1080/01621459.1927.10502953). Small samples, selected tasks, correlated runs, subjective outcomes and seed choices limit interpretation. This code does not run matched benchmark trials or establish causal comparisons between models.
