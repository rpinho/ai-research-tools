# Sharing lessons between lanes and sessions

A lane shares what it learned by adding an evidence-backed row to the root
`LESSONS.jsonl`. The next lane and the next sweep receive relevant rows from
other lanes automatically. Retros explain what a field taught us; transferable
method and agent lessons land in the shared log, then become tests, lint or rules
when we can enforce them.

The order of strength is **rules in code > promoted lessons > the lessons log >
retros > handoff files**. A promoted row points at the enforcing file in
`promoted_to`. Read that implementation; a lesson is not itself enforcement.

## Record, find and improve a lesson

All commands accept `rh --root ROOT ...`. Examples use synthetic lane names and
invented evidence; use an existing evidence file when recording a real lesson.

```sh
rh lessons add --lane sample-a --source retro --tags evaluation,baselines \
  --lesson 'A label-count control exposed a shortcut in this evaluation.' \
  --evidence lanes/sample-a/control.md \
  --applies-when 'Evaluating models on imbalanced labels'
rh lessons list --status active
rh lessons for sample-b --tags evaluation -n 12
rh lessons for sample-b --idea IDEA_ID -n 12
rh lessons supersede OLD_ID --by NEW_ID
rh lessons promote NEW_ID --to tests/test_control.py
```

`add --supersedes OLD_ID ...` records a correction and supersedes its predecessors
in the same locked operation. `--promoted-to PATH` can import an already enforced
lesson. `list` includes all current statuses unless filtered. `for` excludes
superseded rows and returns other lanes first; within each group it ranks by tag
overlap, then UTC recency and ID for ties. It includes promoted rows and their
code paths. `--tags` accepts comma-separated tags or multiple words; tags are
trimmed, deduplicated, case-normalized and sorted. Use domain, method and failure
mode tags. Context comes from top-level `lesson_tags`, `tags`, `keywords` and
`depth_tags` in `INTAKE.toml`, plus idea `data_tags`/`tags`. `--idea` uses that
idea's tags rather than the entire queue and accepts an unambiguous ID prefix.
The lane name and words in search phrases/domain tags also match; each lesson
tag contributes at most one overlap point. No embeddings or model calls run.

Every row has `id`, `utc`, `lane`, `source`, `tags`, `lesson`, `evidence`,
`applies_when`, `status`, `supersedes` (ID array), and `promoted_to` (path or empty
string). Sources are `retro|close|referee|correction|backfill`; statuses are
`active|superseded|promoted`. Write one claim in at most two sentences and a
one-line applicability condition. Local evidence and promotion targets must be
existing files, resolved from ROOT; HTTP(S) evidence URLs are recorded without
network verification. File fragments can identify a row. Historical lane names
are allowed for backfill even after a lane has ended.

Revisions append full row snapshots; no row is deleted. All log reads and
read/modify/appends use the existing `rh.locked(ROOT)` mechanism (`ROOT/.lock`).
Empty reads create nothing. Concurrent writers serialize across lanes. Multi-row
supersession holds that lock throughout; this is a local append log, not a
crash-atomic database transaction or tamper-proof external audit.

## How it reaches work

- Every sweep stage and repair embeds the same `rh lessons for LANE -n 12`
  output verbatim beside the policy. Finished receipts record `lesson_ids`,
  `lessons_command` and `lessons_sha256` for the snapshot actually shown.
  An interrupted run records only IDs already included in a generated prompt.
- `rh lane new` prints relevant rows and writes `LESSONS_READ.json` with the
  command, IDs, time and snapshot hash. A read receipt proves exposure, not
  comprehension. A new lane does not run a worker or invent results.
- `rh idea close` (also `rh ideas close`) accepts `--lesson TEXT` and optional
  `--lesson-tags TAG ...`. It records a source=`close` row linked from the close.
  A top-level `lessons_required = true` in `INTAKE.toml` requires that flag or
  `--no-lesson REASON`. Default is false; installation enables no lane. Existing
  attempt, taste, selection and verification requirements still apply.
- Lint emits WARN, without changing failure counts, when an enabled lane's
  last sweep receipt has no lesson IDs, or an active lesson is older than 90 days
  with no recorded ID reference in any lane's sweep/read/idea receipts.
  Historical log rows are not rewritten. These are coverage warnings, not
  scientific validity checks; arbitrary programs can bypass CLI close admission.

## Retros are about the field

Run `rh retro LANE [--out PATH]` at a challenge's end, phase change, final
submission, or after a referee FAIL changes direction. It only writes a local
draft (default `LANE/RETRO.draft.md`), a `.sources.json` evidence packet, and a
`.lessons.proposed.jsonl` candidate file. Existing outputs are refused. It reads
the lane's idea history/close reasons, result and prior-art artifacts, selection
and optimism ledgers, verification levels, substantive outreach replies, and
relevant lesson rows. It never runs a checker, worker, search or send.

The eight draft sections cover what we tried; what results taught us about the
problem and field; what's easy/hard; missing data; what would help; open
questions; named linked credits; and the difficulty of open science with AI in
that field. The scaffold supplies counts, IDs, recorded verification levels, and numeric
result/selection summaries when structured evidence contains them.
Raw free text stays in the evidence packet: it may contain ops or private notes.
Fill the field narrative from reviewed evidence rather than copying that packet.
Keep agent/ops lessons in the candidate file and log. Candidate rows attribute
recorded close reasons; they need human revision into transferable claims and
explicit `rh lessons add` before landing. No model infers scientific conclusions
or credits, and no automatic prose classifier promises privacy.

Use the retro skill and the author's voice guidance to fill the draft. Human
reading and `egress-gate scan` PASS are required before public use; this local
command does not publish and cannot intercept other publishing tools.

## Backfill

Choose a small number of scientific and method claims, with actual evidence
paths, and use `--source backfill`. Mark an already enforced claim promoted only
when you can point to its test, lint or rule file. Do not import personal notes
or credentials. The repository ships no real lane history. A synthetic example
is available in `examples/lessons-backfill.md` in the public distribution.
