"""Honest local-only fixtures for the evidence contracts (no source queries)."""
import hashlib
import json
from pathlib import Path


def prior_art(path):
    path = Path(path)
    capture = path.parent / 'X_CAPTURE_FIXTURE.jsonl'
    capture.write_text('{"query":"fixture only","results":[]}\n')
    sources = {k: dict(status='none', query='fixture only', searched_at='2026-01-01T00:00:00+00:00',
                      hits=[], note='searched, none (offline test fixture)')
               for k in ('github','x','papers','leaderboards')}
    sources['x'].update(capture_path=str(capture), capture_sha256=hashlib.sha256(capture.read_bytes()).hexdigest())
    path.write_text(json.dumps(dict(schema=1, baseline='fixture method: 1.0', sources=sources)))
    return path


def config(path, **values):
    Path(path).write_text(json.dumps(values or {'seed': 1}))
    return Path(path)
