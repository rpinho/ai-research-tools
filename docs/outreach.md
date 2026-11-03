# Outreach: draft, lint, approve, send

The outreach module never sends or fetches a message. It records a local lifecycle: draft, lint, approve, sent, reply, close or drop. Sending belongs to a separate tool and requires the operator's authorization for the actual message.

Set `RH_CONTACTS` to a private JSON file containing an array of objects with a nonempty `name` string. Names must be unique ignoring case. Extra fields may hold addresses for your separate sending tool; the harness only uses names. Nothing in this repo contains contacts. Do not commit that file. Ledger metadata rejects email-address-shaped strings; draft bodies remain private files.

Set `RH_VOICE_COMMAND` to a shell-quoted executable and arguments. It receives the draft on stdin; lines starting `FAIL:` or a nonzero exit refuse lint. It is executed as an argument vector without a shell. For a social reply, the harness appends `--reply`. A missing checker refuses. No personal voice policy is provided. The tiny checker under tests is a synthetic fixture, not a writing standard.

`OUTREACH.toml` sets question limits and follow-up timing. Optional `science_checks = true` invokes `RH_SCIENCE_CHECK_COMMAND`, which receives JSON with `text` and `attachments` on stdin and uses the same failure convention. That replaces an installation-specific Python import.

Lint checks question count, links, recent contact collisions and similarity across drafts. It normalizes names, links and titles; first/last sentence word-set Jaccard thresholds are 0.6 and body 5-gram Jaccard is 0.35. These heuristics can miss templated spam or flag legitimate messages. A documented collision override is possible.

Approval requires a current passing lint hash and the operator's approval quote. Any byte change invalidates approval. `sent` requires matching approved bytes and records a message identifier; follow-ups become due after the configured interval. The optional email hook checks exact submitted bytes against the latest approved ledger entry. Approval enforcement defaults to warning; set `RH_OUTREACH_GATE=enforce` in the sending environment. Voice failures deny in both modes. The hook cannot authenticate the person behind an approval quote and matches message bodies, not recipient authorization.

Code: [outreach.py](../harness/outreach.py), [email-voice-gate.py](../tools/email-voice-gate.py). Tests: [test_outreach.py](../harness/tests/test_outreach.py), [test_outreach_gate.py](../harness/tests/test_outreach_gate.py).
