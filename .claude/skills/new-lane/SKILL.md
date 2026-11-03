---
name: new-lane
description: Create a research or competition lane with frozen v2 selection declarations and lint it.
---

Use the user's problem, metric, dev/hidden split, deadline and submission rules;
interview only for missing facts. For unattended toy setup, use a provisional
`group` split holding out sequence families, explain the assumption in
`--split-reason`, and record that the organizer's sampling is unverified. Random
requires affirmative shared-unit evidence, never a convenience justification.

Run `.venv/bin/rh --root .runtime lane new NAME --help`, then that command with
all required facts. Set `--sealed-set-id` to the fixed organizer hidden test
identifier; an unavailable manifest is a declared identifier, not verified data.
Keep the default one sealed-selector batch separate from the allowed upload count
(`--submissions`). Use `--deadline-days` for relative deadlines. For exact-match
sequence toys the default development test and baseline bar apply; supply
`--test` and `--bar` for other problems. Defaults declare no augmentation, fold-local
preprocessing, fixed-step final fit, ten measurements, and zero trials.

Run `.venv/bin/rh --root .runtime lint NAME`. Do not create fake trials, reading
receipts or scores to satisfy lint. Inspect the generated `LANE.json` and
`SELECTION.json`; report assumptions and leave workers disabled.

Frozen fields are immutable from creation. If sampling assumptions, the sealed
identifier, test, bar or rules later change, create a new lane with corrected
declarations. Never tell the user to edit or replace a frozen field in place.
