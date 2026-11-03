# Moving a machine-local installation

This distribution contains generic code and synthetic tests. It contains no live lanes, alerts, contacts, state ledgers, receipts, backups or loaded scheduler jobs. Do not copy an existing lane into a public repository to get started.

1. Choose a writable runtime root outside tracked content. Create a lane from the toy layout, then replace the toy policy with your actual authorized experiment rules.
2. Set pod-eligible lanes explicitly with `RH_POD_LANES`. Evidence-policy and reaper exemptions default to empty. No source-machine exemptions are inherited.
3. Bootstrap local model routes from `LEADERBOARD.md`, point `RH_ROLES_FILE` at the generated file, and verify executor compatibility. No fallback silently substitutes another route.
4. Supply private contact storage and lint commands. The outreach module records approval and sends nothing; install the optional email hook in the tool environment if needed.
5. Set SSH identity/known-hosts and optional notification adapters. Deploy dependent modules together before using the pod runner. Install and verify remote deadlines separately.
6. Render scheduler templates with local paths and inspect them before activation. There is no bundled watchdog. The job launcher is synchronous and preserves failure exit codes.
7. Run `python3 -m pytest -q`. Tests replace HOME before collection, use synthetic state and prohibit Internet sockets. Existing personal hooks or archives are not required.

Ledger formats remain JSON/JSONL at runtime. The public model snapshot is a single Markdown file; scoring ledgers and generated routes remain private. Archive-specific leaderboard adapters and dated outreach backfills are intentionally absent. Generic model-ledger validation, conservative ranking and generic idea, proposer, review-receipt and draft-outcome adapters remain.

No migration command modifies an existing installation. Review the [configuration reference](../docs/configuration.md) and limits before connecting live services.
