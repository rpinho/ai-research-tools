"""Personal data discovery must require an explicit configuration root."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'harness'))
import rh
import worker_models
import outreach


def test_unconfigured_session_and_database_never_search_home(monkeypatch):
    monkeypatch.delenv('RH_TRANSCRIPTS_ROOT', raising=False)
    monkeypatch.delenv('RH_CS_DB_ROOT', raising=False)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'synthetic-session')
    def forbidden(*args, **kwargs):
        raise AssertionError('unconfigured personal path search')
    monkeypatch.setattr(Path, 'glob', forbidden)
    assert rh.session_model() is None
    assert rh.banned_cs_sessions('http://localhost/projects/synthetic') == []


def test_default_routes_and_contacts_are_workspace_relative():
    assert worker_models.ROLES_FILE == Path('.runtime/roles.json')
    assert rh.ROLES_FILE == Path('.runtime/roles.json')
    assert outreach.CONTACTS == Path('.runtime/contacts.json')
