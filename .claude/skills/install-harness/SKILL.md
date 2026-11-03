---
name: install-harness
description: Install this research harness, run its full tests, and preview the offline toy lane when asked to set up this repository.
---

Run `scripts/install-harness.sh` from the repo root. It calls bootstrap, the full
suite, then the toy demo in a fresh temporary root. Report exit status and test
count. Run the script directly without a pipeline: it prints the pytest summary,
`Toy demo: PASS` and `Install harness: PASS`; its exit status is authoritative.
Activate `.venv` for subsequent `rh` commands. No provider login is needed.
