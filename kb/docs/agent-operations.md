# Running a personal agent setup

The KB is the durable layer. Around it are model calls, background jobs, rented machines and sessions you may control from a phone. Those pieces need contracts too: who owns the work, what counts as progress, what happens on failure, and how to stop it.

This is a set of **deployment patterns**, not a bundled orchestrator. The scaffold already ships the worktree launcher, nightly fallback, lint and alerts directory. The resource guards and session registry below are things to wire into your own setup. Keep machine paths, conversation IDs, notification destinations and credentials in private configuration outside the repo.

## Cheap execution does not mean cheap judgment

Model cost and decision authority are separate choices. A cheap coordinator can notice that a job finished and route its result; that does not make it qualified to decide whether a surprising result is scientifically sound.

| Role | Contract | Escalate when |
|---|---|---|
| Scheduler / watcher | Deterministic script: inspect state, enforce limits, emit events | State is missing, stale or contradictory |
| Cheap coordinator | Launch approved jobs, relay short status, route requests | A plan or interpretation must change |
| Worker | Execute a bounded task with an acceptance check | The check fails, scope expands, or evidence is ambiguous |
| Planner / reviewer | Choose methods, interpret evidence, challenge conclusions | An independent review or human decision is needed |

Use the cheapest model that passes the task's acceptance check. Save the strongest model for the decisions that need it; do not leave it awake through hours of compilation or training. A consultation can be a short plan or review instead of a long builder session.

**Give each delegated job a small contract:**

- Goal and exact deliverable; what is out of scope.
- Allowed write paths and worktree; other sessions sharing the repository.
- Model/provider requested, fallback policy, time/turn and compute limits.
- Checkpoint location, stop mechanism, expected progress signal.
- Reproduction command, acceptance check and short final report.

The supervising session reads the short report first, then opens only the evidence needed to assess it. Keep the durable queue in a state file. Repeating an unchanged queue in every reply adds noise without advancing it.

## Stop paying a large context to wait

A short polling message can still replay a large conversation. Cached context can also have a cost. Measure input, cache reads, output and wall time per completed task before deciding where the expense is.

For a long job:

1. Launch a tracked shell process and write a checkpoint with its job ID, expected output and stop condition.
2. End the worker turn. Let a small scheduler script check progress and budgets.
3. On completion, failure or a real decision, send an event and resume a worker with the checkpoint.
4. Verify the artifact, then account for every child process and resource before closing the job.

Use a lock and an event ID so repeated scheduler ticks cannot launch duplicate workers. A recurring prompt inside a large session is not a cheap watcher. Put the schedule outside that session so it survives compaction, disconnection and session expiry.

Do not silently shrink a user's chosen context window to fix cost. Checkpoint first and make compaction or rotation an explicit workflow choice; remote access and conversation continuity may be part of the requirement.

## Retry the failure you actually have

Fallback is a state transition with provenance. Keep the original job specification and acceptance checks; resume from `STATUS.md` and existing artifacts instead of restarting completed work.

| Failure | Response |
|---|---|
| Capacity / transient transport failure | Bounded retries with backoff, then an allowed backend |
| Expired authentication / exhausted credit | Record the failed dependency, alert, and use only a configured alternative |
| Provider content-filter stop | Review the task and stop reason first; for permitted work, use another provider only within an explicit fallback policy and that provider's rules |
| Invalid or missing artifact | Fail the acceptance check; repair or escalate with evidence |
| Explicit model requested but unavailable | Report unavailability; do not silently substitute another model |

A content-filter refusal is not a capacity error. Do not automatically route a refused request around safeguards. Keep any permitted provider change visible in the report, along with the reason, attempts and actual model used. The provider change never relaxes the acceptance check.

Prefer structured error/status fields to searching a log tail. Where a CLI only emits text, treat the classifier as fallible and retain the exit status and a short, sanitized diagnostic. HTTP success can contain an API error; an empty answer can have a nominally successful exit. Inspect the response body and required output.

Keep transport configuration separate from the requested model. A global backend switch must not turn a consultation with one model into an answer from a different one under the same label.

## Process inspection must not read another conversation

Prompts passed as command-line arguments can appear in ordinary process listings. One innocent liveness check can pull another session's entire task into the current context. Logs and terminal captures have the same boundary.

Use an allowlist of metadata fields. For example, for a PID recorded by **your own** launcher:

```sh
ps -p "$job_pid" -o pid=,ppid=,etime=,pcpu=,comm=
```

`comm` is the executable name; do not replace it with `command` or `args`. Keep the working directory and job label in a registry rather than trying to extract them from arbitrary argument strings. Avoid broad process dumps even if you intend to redact them afterwards: the raw output has already crossed the boundary.

Pass prompts on stdin, or through a private file when the runtime requires it. Do not enable shell tracing around credentials or prompt construction. A privacy-safe process display is useful even when your own launcher is careful, because other launchers may still put prompts in arguments.

Cleanup uses the ownership records too. Parent PID 1 or high CPU alone does not prove a process belongs to your job. Track descendants or a dedicated process group, verify identity before signaling, request a graceful stop, and check for leftovers after the agent exits.

## Guarantee an artifact; independently deliver the alarm

The [nightly runner](../scripts/nightly/run.sh) already separates ingest and writing, caps the ingest phase, lints the digest, builds a deterministic fallback, and attempts delivery or a failure notice. That is the starting point. A deployment still needs a working delivery channel and a watcher that survives the agent session.

Treat these as separate states:

| State | Evidence |
|---|---|
| Produced | Non-empty artifact at the expected path, for this run |
| Validated | Content/coverage checks passed, or explicitly labeled degraded output |
| Published / sent | Destination accepted the artifact; verify readability where possible |
| Alert queued | Durable local alert exists |
| Alert accepted | Push service acknowledged the request |
| Alert received | End-to-end test reached the intended device |

**An EXIT trap is a backstop, not an external supervisor.** It cannot handle every host failure or forced kill. Add an independent deadline check that notices a missing or stale artifact, publishes the deterministic fallback when possible, and sends a short failure push. Optional ingest and cleanup must not block that deadline path.

Use the shared [alerts directory](../scripts/alert.py) for idempotent problem keys and recovery clears. Run its consumer from the OS scheduler, not a repeating prompt in the main conversation. Start a fresh repair worker only for a real event, with a time budget and duplicate-run lock; if repair cannot start, send the alert directly.

The shipped `alert.py push-pending` **marks alerts pushed when it prints them**, before your sender acknowledges receipt. A pipe to a failed sender can therefore lose the next retry. For a reliable deployment, use an outbox with acknowledgement after successful send, or persist failed sends in a retry queue. Do not infer receipt from that flag.

Test the full path from the scheduler's environment to the device. A push tool saying "requested" is not proof the human saw it. Use a channel they actually receive; keep notification payloads non-sensitive and private destinations out of version control. If both delivery paths fail, retain local evidence for retry. No networked system can promise delivery through a total outage.

Before scheduling, exercise the degraded paths: unavailable models, missing artifact, malformed API response, delivery failure, duplicate tick, and a job dying before its send step. Run those checks with local stubs; a dry-run flag alone may still call paid models.

## Compute has an owner, a budget and an end

An idle rented GPU can keep billing after the useful computation ends. A cheap monitoring model can also consume a surprising amount through frequent context replay. Guard both from outside the agent doing the work.

Record this **before launch** in private job state:

| Field | Why it matters |
|---|---|
| Job and resource ID, owner | Cleanup must target this job only |
| Hourly rate and maximum total spend | Converts elapsed time into a limit |
| Deadline and idle timeout | Stops an abandoned or stalled run |
| Last useful checkpoint and expected next one | Distinguishes progress from log noise |
| Output destination and retrieval check | Avoids stopping with the only copy still on the machine |
| Stop/terminate action and billing check | "Training stopped" may leave the resource rented |

Use provider spending caps where available, plus an independent watchdog for deadline, estimated cost and idle time. Low-balance alerts are early warning, not spending enforcement. A failed balance query is **unknown**, never a fabricated healthy balance; pause new paid launches until the state is known. Deduplicate warnings by job and threshold, and make monitor failure visible too.

Judge idleness with both workload activity and useful output. Low utilization can be a legitimate data-loading phase; a busy device can be stuck doing useless work. Give known long phases explicit grace periods, and cap them. A heartbeat alone is not progress.

On completion or timeout: checkpoint, retrieve and verify outputs, gracefully stop owned workers, release the rented resource, and query the provider to confirm its billable state. Check whether disks or other retained resources still incur charges. Retain small reports, metadata and reproduction commands rather than all regenerable intermediates.

The watcher must outlive the agent. Do not rely on the model remembering to terminate a machine when its context ends. For local jobs, account for orphaned child processes too; an agent's final answer does not clean them up.

## Session lanes preserve identity and access

A lane is one named purpose with a pinned conversation, project directory and remote-control connection. Name it for its harness or job, so a model change does not imply a new identity. A terminal multiplexer keeps the host-side process alive when a client disconnects; it does not by itself provide phone access.

Keep a private registry of lane name, native conversation ID, expected directory, project membership and launcher-owned process identity. A normal launch must not overwrite the pinned ID. Before recovery, validate only the selected lane's record, so one broken lane does not prevent another from starting.

Recovery should:

1. Acquire a lock and compare names exactly; prefix matching can confuse a lane with its browser/helper lane.
2. Leave healthy lanes alone. Start only what is missing.
3. Refuse a conversation whose recorded directory differs from the lane; launch from the project directory, never an accidental home-directory default.
4. Leave an ambiguous live pane untouched. Replace a placeholder only when its identity still matches what the launcher created.
5. Reattach the pinned conversation through the runtime's remote-control path, and verify project membership separately.
6. Check discovery from the phone: list history, open a conversation, return to the list, and confirm the expected conversations remain visible.

**Connected is not discoverable.** Provider filters, project membership and catalog pagination can each hide a valid conversation. Inspect their metadata without reading unrelated transcripts. Preserve native IDs and history instead of creating replacement conversations to make a status light green.

Updates need the same behavioral test. Finish active turns, checkpoint, test the candidate against remote discovery and history, retain a rollback path, then restart only the intended component. A CLI and a shared daemon can have different lifecycles; changing one version is not evidence the other is safe to replace.

## Verify before claiming "running"

Report the strongest state the evidence supports:

| Claim | Minimum evidence |
|---|---|
| Launched | Launcher accepted the job and recorded its identity |
| Alive | The owned process/session exists in the expected directory |
| Progressing | A fresh useful checkpoint or artifact advances the task |
| Validated | An acceptance check independent of the search/generator passes |
| Delivered | Destination check passes, with any receipt limitation stated |
| Closed | Owned processes cleaned up and billable resources released |

For long experiments, use controls suited to the task: a known-answer case, shuffled/negative input, or a separate verifier. Watch quality as well as liveness. A growing log full of repeated errors is activity, not progress. Timestamp the evidence; yesterday's checkpoint does not establish today's health.

Keep `STATUS.md` current enough to resume: completed work, best verified result, running resources, blockers and next step. End with `REPORT.md`: outcome, limits, reproduction commands and cleanup state. A clearly documented negative result is useful output.

## Prior art is the first research artifact

Before implementing a new scientific method or benchmark entry, write `PRIOR_ART.md`. Search the live official table and methodology, public code and open pull requests, primary papers, and relevant public discussions. Record URLs, dates checked, the strongest comparable result and the exact evaluation conditions. Inspect more than the top few ranks when descriptions contain recipes.

Reproduce the best available public baseline, or document why you cannot. Then improve it, with a fast evaluation loop and a separate verifier. A benchmark score is uninterpretable until you know its harness, data split and metric.

Make the project's premise a stop gate: if the planned question is already answered, record that and stop before expensive runs. Say "I did not find it in these searches," rather than "nobody has done it." Before a novelty or record claim, refresh the public sources and credit the methods used. A good absence check can save an entire project.

## Reversible cleanup still needs a dependency check

Trash is a recovery path, not proof that moving a folder is safe. A dated `review` folder can become live configuration. Before cleanup, check deployment manifests, scheduled jobs, symlinks and current artifacts for references to each candidate. Respect explicit keep markers; check path aliases too.

Verify another copy of irreplaceable data, move through the OS trash or a recoverable quarantine, and record the original path, destination and restore command. Smoke-check the consumers afterwards; restore promptly if a dependency breaks. Reversibility includes knowing what to put back.

## Wire the mechanisms before relying on them

The [operating manual](../AGENTS.md) keeps the rules short. Promote each deployment rule to the strongest mechanism available:

| Promise | Mechanism to install / verify |
|---|---|
| No duplicate worker or lane | Lock + stable job/lane identity |
| No cross-session prompt leaks | Allowlisted status fields + stdin prompt transport |
| No silent model substitution | Requested/actual model recorded + explicit fallback policy |
| No missing morning artifact | Existing fallback runner + independent deadline check |
| Failures reach the phone | Durable outbox + scheduled sender + receipt test |
| No abandoned compute bill | Provider cap + independent idle/deadline guard + billing query |
| Phone recovery preserves history | Pinned IDs + discovery regression before updates |
| No scratch rebuild of a solved result | Prior-art artifact + premise gate before compute |
| Cleanup preserves live dependencies | Reference check + reversible move + consumer check |

Test a failure on purpose before calling its guard installed. A checklist in a document is still prose until a mechanism has been watched refusing the bad path.
