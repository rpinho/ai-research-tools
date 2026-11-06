#!/usr/bin/env python3
"""build_prompt.py TEMPLATE OUT: fill a scheduled-run prompt template's @@PLACEHOLDERS@@.

Reads NB_* environment variables set by scripts/nightly/run.sh. Done in Python so no shell quoting can
mangle the text: a prompt once died on a backtick inside a double-quoted shell string (exit 127, no
digest, nothing recognisable in the log), and `bash -n` does not catch that class of bug.
Fails loudly on any unsubstituted placeholder or a suspiciously short result.
"""
import os
import re
import sys

tpl, out = sys.argv[1], sys.argv[2]
t = open(tpl, encoding="utf-8").read()
backend = os.environ["NB_BACKEND"]
sub = os.environ.get("NB_SUBAGENT", "")

delegation = (
    "DELEGATION (REQUIRED; this is how the run stays affordable). You are the ORCHESTRATOR. Split tonight's "
    f"new items into chunks of about 40 and hand each chunk to a sub-agent on model {sub}, several in parallel. "
    "Each sub-agent gets the file paths of its chunk and the triage rules, and returns ONLY one line per item: "
    "id | disposition (full / light / skip + reason) | author | the claim in <= 30 words | suggested wiki page. "
    "You keep: all wiki routing decisions and edits, the ledger updates, commits, and the BRIEF. Never paste whole "
    "raw files into your own context."
)
how = {
    "claude": f' HOW: use the Agent tool (model "{sub}"), several Agent calls in ONE message so they run in parallel.',
    "codex": f' HOW: use your sub-agent spawning tool with model "{sub}" and low reasoning effort; wait for all results.',
}.get(backend, "")

test = ""
if os.environ.get("NB_TEST"):
    test = ("TEST MODE (a verification run, not the real one). Do NOT call paid external APIs and do NOT update "
            "the processed ledger. Work from the raw files already in the repo. Everything else runs normally in "
            "this throwaway worktree.")

subs = {
    "@@DAY@@": os.environ["NB_DAY"],
    "@@YESTERDAY@@": os.environ.get("NB_YESTERDAY", ""),
    "@@PENDING_N@@": os.environ.get("NB_PENDING_N", "0"),
    "@@DELIVERY_STEP@@": os.environ.get("NB_DELIVERY", ""),
    "@@DELEGATION@@": delegation + how,
    "@@TESTMODE@@": test,
}
for k, v in subs.items():
    t = t.replace(k, v)
left = sorted(set(re.findall(r"@@[A-Z_]+@@", t)))
if left or len(t) < int(os.environ.get("NB_MIN_PROMPT_CHARS", 800)):
    sys.exit(f"FATAL: prompt build failed (len={len(t)}, unsubstituted={left})")
open(out, "w", encoding="utf-8").write(t)
