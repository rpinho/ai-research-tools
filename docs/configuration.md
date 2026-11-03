# Configuration

Use an isolated writable root with `python3 harness/rh.py --root PATH ...`. It contains `lanes/NAME/INTAKE.toml`, optional sweep/cadence/outreach TOML files, and runtime JSON/JSONL ledgers. Never use a real lane as a tutorial fixture. See [examples/toy-lane](../examples/toy-lane).

| Setting | Meaning |
|---|---|
| `RH_POD_LANES` | Comma-separated eligible pod lane names; empty by default. |
| `RH_POLICY_EXCLUDED` | Explicit legacy evidence-policy exemptions; empty by default. New deployments should keep it empty. The capacity hook also recognizes these exemptions. |
| `RH_REAP_EXCLUDED` | Comma-separated protected pod names or lane names; empty by default. |
| `RH_ROLES_FILE` | Local generated roles/routes JSON; default `$HOME/.config/ai-research-tools/roles.json`. |
| `RH_AGENT_ALERTS` | Agent alert JSONL queue; default `$HOME/.local/state/agent-alerts.jsonl`. |
| `RH_NOTIFY_COMMAND` | Optional pod/provider notification executable and quoted arguments; no service supplied. |
| `RH_CONTACTS` | Private contact file. See [outreach](outreach.md). |
| `RH_VOICE_COMMAND`, `RH_SCIENCE_CHECK_COMMAND` | Separately supplied lint commands. Missing required commands refuse lint. |
| `RH_TRASH_COMMAND` | Reversible disposal adapter; default `/usr/bin/trash`. Linux deployments must supply a compatible Trash command. There is no destructive fallback. |
| `RH_SSH_KEY`, `RH_KNOWN_HOSTS` | SSH identity and strict known-hosts file; standard SSH paths by default. |
| `RH_GH` | GitHub CLI executable used by metadata discovery; default `gh`. |
| `RH_XCAPTURE_ROOT` | Private root holding `x_capture/KEY.jsonl`; generator default is current directory. |
| `RH_EVIDENCE_ROOT` | Explicit harness root for leaderboard ingestion. No home/archive discovery. |
| `MODEL_REVIEW_ROOT`, `MODEL_DRAFTS_LOG` | Explicit private review receipt tree and draft-outcome ledger. |
| `MODEL_STATE`, `MODEL_LEDGER`, `MODEL_OVERRIDES` | Private scoring state directory, optional ledger path and exclusions config. |
| `LAUNCH_ARTIFACT_DIR` | Standalone job specs/logs; default `$HOME/.local/state/ai-research-tools/jobs`. A sweep passes its own spec path. |
| `LAUNCH_STATE`, `LAUNCH_SELECTOR`, `LAUNCH_CODEX` | Job registry, optional selector override and executor. |
| `LAUNCH_ROLE`, `LAUNCH_EFFORT` | Role and effort; fixed model overrides also require a reason. |
| `LAUNCH_IDEA`, `LAUNCH_IDEA_ROOT` | Optional promoted-idea admission check before execution. Requires an `rh` command on PATH. |
| `EVENT_WATCH_CONF`, `EVENT_WATCH_STATE`, `EVENT_WATCH_NOTIFY` | Watch rules, cursor/event state and optional notification executable. |
| `POLL_GUARD_STATE` | Private hook fingerprint directory. |
| `CS_BASE`, `CS_BIN`, `CS_STATE`, `CS_AUTO_READ` | Optional local daemon URL, CLI, cookie directory and path-separated auto-readable roots. Auto-read roots default to empty. |

The capacity hook reads `$HOME/.config/research-harness/pod-gate.json`: `mode` is `audit` or `enforce`; `enforce_lanes` is an array drawn from `RH_POD_LANES`. Its audit log is under `$HOME/.local/state/research-harness/`. Configure `RH_ROOT` for the hook's queue root. Hooks are not installed automatically.

The launcher stays synchronous so the sweep can bound and clean its process group. Capacity retries are limited to five attempts and resume from checkpoint files. It uses the executor's workspace-write sandbox; external provider access and approval policy remain your deployment's responsibility. It does not install a watchdog or claim that a registered job will restart automatically.

Launchd templates under [harness/templates](../harness/templates) contain `@...@` placeholders. Render them into your own scheduler directory, choose Python and root paths, and inspect before loading. Template files are not active jobs. Linux users can schedule the same commands with their existing scheduler.
