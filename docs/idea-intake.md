# Idea intake, sweeps and kill-tests

Intake screens source metadata against a lane's `INTAKE.toml`. External sources require a URL; internal and operator proposals do not. Duplicate titles normalize to the same hash. Intake is dry by default; `--live` fetches configured sources and appends a run receipt. Local hits do not count as having read an external source. A source failure is recorded and returns failure even when another source succeeds.

Ideas move through `new`, `testing`, `closed` and `shipped`. Changes append events to `IDEAS.jsonl`; the latest event determines current state. Promoting an experiment requires prior art, a frozen test and pass line, holdout disposition and a permitted decider. A mechanical close needs result/config evidence. A judgment close without testing needs an explicit reason; claimed attempts must correspond to distinct recorded configurations.

A sweep runs search, read, kill and deterministic compilation. Missing or empty rules refuse before a worker starts, including in dry runs. Each generated prompt contains the current rules and intake policy verbatim. Report grammar, expected idea IDs and receipt hashes are checked. Unsettled ideas carry forward instead of disappearing after a failed stage. Search outputs are leads until read and tested.

Workers have time, memory, CPU-headroom and disk checks. Lanes serialize their sweeps with locks; separate lanes can run independently. Process-group cleanup stops remaining worker descendants. These checks do not verify a scientific claim or enforce the text of `allowed_writes` as an OS sandbox.

Code: [rh.py](../harness/rh.py), [sweep.py](../harness/sweep.py), [idea_policy.py](../harness/idea_policy.py). Offline compiler cases: [test_sweep.py](../harness/tests/test_sweep.py).
