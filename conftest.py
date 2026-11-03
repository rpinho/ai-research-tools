"""Hermetic collection and offline tests, including child interpreters.

HOME is replaced before module collection. The original home is never read by the
suite. Socket guards forbid Internet connections while permitting local Unix IPC.
"""
import os
from pathlib import Path
import shlex
import socket
import sys
import tempfile

import pytest

_TEST_HOME = tempfile.TemporaryDirectory(prefix='ai-research-tests-')
os.environ['HOME'] = _TEST_HOME.name
os.environ['XDG_CONFIG_HOME'] = str(Path(_TEST_HOME.name) / '.config')
os.environ['XDG_STATE_HOME'] = str(Path(_TEST_HOME.name) / '.local/state')
os.environ['RH_POD_LANES'] = 'team,theory,orbits,toy-evolution'
os.environ['RH_POLICY_EXCLUDED'] = 'restricted-a,restricted-b'
os.environ['RH_REAP_EXCLUDED'] = 'restricted-a,restricted-b'
for key in ('RH_ROLES_FILE', 'RH_CONTACTS', 'RH_DECIDED_BY', 'CLAUDE_CODE_SESSION_ID', 'RH_EVIDENCE_ROOT',
            'MODEL_OVERRIDES', 'MODEL_REVIEW_ROOT', 'MODEL_DRAFTS_LOG', 'MODEL_STATE', 'MODEL_LEDGER', 'RH_ROOT', 'LAUNCH_SELECTOR', 'LAUNCH_ARTIFACT_DIR', 'LAUNCH_IDEA', 'LAUNCH_IDEA_ROOT'):
    os.environ.pop(key, None)
FIXTURE = Path(__file__).parent / 'tests/fixtures/check.py'
os.environ['RH_VOICE_COMMAND'] = shlex.join([sys.executable, str(FIXTURE), 'voice'])
os.environ['RH_SCIENCE_CHECK_COMMAND'] = shlex.join([sys.executable, str(FIXTURE), 'science'])


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def guarded(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('network is disabled in tests')
        return connect(self, address)
    def guarded_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError('network is disabled in tests')
        return connect_ex(self, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_ex)
