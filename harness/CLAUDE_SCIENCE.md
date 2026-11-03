# Optional local science daemon client

`cs.py` is an unofficial HTTP client for a separately installed local daemon. The core harness and tests do not require that service. API changes may break it.

`CS_BASE` selects a URL; otherwise the client asks `CS_BIN status` for the local port. `CS_STATE` stores its private cookie jar with restricted permissions. `CS_AUTO_READ` is a path-separated list of explicitly approved roots; it defaults to empty. The client can automatically approve local execution prompts; other prompt types require review. Check its approval logic before using it with an untrusted project.

Models follow an explicit option, the calling session's model when supplied, or the lane's `claude_science_model`. A legacy banned-model set remains in `rh.py`; edit that policy for your deployment. No project IDs, session IDs, cookies or model account data are included.

A lane may enable `claude_science_required` to require a review URL before promotion. Promoting an idea sourced from this service requires an independent-check description. Those strings are receipts, not proof of an independent replication. Reopen primary sources and verify numbers outside the producing session.

```sh
python3 harness/cs.py projects
python3 harness/cs.py status SESSION
python3 harness/cs.py read SESSION
```

Other commands can create, edit, approve or cancel remote sessions; only run them under explicit authorization. These examples are documentation, not configured sessions.
