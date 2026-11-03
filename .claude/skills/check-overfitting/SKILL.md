---
name: check-overfitting
description: Audit a lane against the ten practices in docs/overfitting.md and report declared practices without claiming unrun checks passed.
---

Run `.venv/bin/python scripts/check-overfitting.py --root ROOT NAME`.
Read `docs/overfitting.md` for the ten practices and enforcement limits.
Report all ten script rows, distinguishing declarations from measured evidence.
Do not fill missing measurements or claim a declaration proves training behavior.
