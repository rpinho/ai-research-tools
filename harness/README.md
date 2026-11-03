# Research harness

Run `python3 harness/rh.py --help` from the repository root. The runtime uses Python 3.9+ and standard-library modules. State is local, append-only where possible, and excluded from Git.

```sh
python3 harness/rh.py --root ROOT ideas add LANE --source-type internal --claim 'testable mechanism'
python3 harness/rh.py --root ROOT ideas list LANE
python3 harness/rh.py --root ROOT sweep LANE --dry-run
python3 harness/rh.py --root ROOT lint --all --json
python3 harness/rh.py --root ROOT lanes cadence --json
python3 harness/rh.py --root ROOT lanes check
```

The last command writes monitor state and alerts. A real sweep launches workers; intake defaults to dry-run but `intake LANE --live` fetches configured sources and writes receipts. Consult command help before using mutation commands.

| Module | Responsibility |
|---|---|
| `rh.py` | Idea CLI, metadata intake, source screening, append-only revisions and lint. |
| `idea_policy.py` | Frozen bars, config/result hashes, source receipts and explicit close contracts. |
| `sweep.py` | Policy compiler, search/read/kill stages, resource guards and carry-over. |
| `cadence.py`, `lane_alerts.py` | Delivery evidence and durable alerts to agents. |
| `pods.py`, `pod/runner.py` | Registry, monitoring, ownership, capacity, claims, reaping and local pod runner. |
| `harvest.py`, `harvest_reader.py` | Verified incremental snapshots and bounded remote reads. |
| `outreach.py` | Private draft ledger, lint, exact-byte approval and follow-up tracking. |
| `settings.py`, `storage.py` | Explicit configuration and reversible Trash disposal. |
| `worker_models.py` | Role route selection and outcome receipts; no inference calls. |
| `intake_all.py` | Discover configured lane directories and continue after an intake failure. |
| `cs.py` | Optional unofficial local daemon client. See [CLAUDE_SCIENCE.md](CLAUDE_SCIENCE.md). |
| `tools/prior_art_receipt.py` | Generate source receipts using explicit capture storage. |

The [idea intake](../docs/idea-intake.md), [pod](../docs/pods.md) and [outreach](../docs/outreach.md) pages describe enforcement boundaries. Use the [toy example](../examples/toy_demo.py) for a safe local preview.
