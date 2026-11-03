# Prior-art receipts

Start from an existing method when one exists: identify it, reproduce it and only then try to improve it. Promotion requires a nonempty baseline and a structured receipt for GitHub, papers, leaderboards and community captures. Each source must have an honest disposition, query, search date and hits or an explanation that nothing was found. A skipped or blocked required search does not satisfy admission.

A `rh-prior-art` fenced JSON block can live inside `PRIOR_ART.md`; a plain JSON receipt is also accepted. The evidence contract checks its structure and hashes local captures. The generator queries repository metadata, paper indexes and benchmark discovery sources. Community search uses captured metadata supplied under `RH_XCAPTURE_ROOT/x_capture/`; the generator does not use a social-media API or invent a capture when one is missing. Its discovery sources are not an exhaustive survey or the official scoreboard for every competition.

```sh
python3 harness/rh.py prior-art receipt toy PRIOR_ART.md \
  --baseline 'Published method and metric, with source' \
  --query 'problem name baseline'
```

That command uses the network and GitHub CLI. Configure capture storage first. Review sources, dates, competing results and competition data rules yourself. A hash proves which local bytes were referenced; it does not prove a search was complete or a novelty claim correct. No receipt overrides a lane's data restrictions.

Code: [idea_policy.py](../harness/idea_policy.py), [prior_art_receipt.py](../harness/tools/prior_art_receipt.py). Tests: [test_prior_art_shared.py](../harness/tests/test_prior_art_shared.py).
