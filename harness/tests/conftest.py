"""Keep the suite off real state.

11-03: lane_alerts.push() now appends to an on-disk agent queue instead of shelling
out to a (mocked) notifier, so any test touching the real push would write to
~/.local/state/agent-alerts.jsonl. Redirect it for the whole session.
"""
import os
import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_alert_queue(tmp_path_factory, monkeypatch):
    target = tmp_path_factory.mktemp('agent-alerts') / 'agent-alerts.jsonl'
    monkeypatch.setenv('RH_AGENT_ALERTS', str(target))
