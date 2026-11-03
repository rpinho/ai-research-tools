---
name: run-sweep
description: Preview a lane sweep, show its plan, and execute only a requested real run.
---

Run `scripts/run-sweep.sh --root ROOT NAME`. Show the search/read/kill plan,
allowed writes and limits. For a dry-run request stop after this command.
For an explicitly requested real run, configure sources, routes and bounded
workers, then set `workers_enabled=true` in SWEEP.toml and invoke the same script
with `--execute`. It previews again before launching. Never enable workers for a
preview. Choose `AGENT_BACKEND=codex|claude` per the requested executor.
