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


def test_missing_evidence_does_not_probe_absolute_sentinels(monkeypatch):
    import idea_policy
    import pytest
    def forbidden(*args, **kwargs):
        raise AssertionError('missing evidence must not touch files')
    monkeypatch.setattr(Path, 'is_file', forbidden)
    with pytest.raises(ValueError, match='path is required'):
        idea_policy.file_hash(None)


def test_linux_resource_root_is_configurable(tmp_path, monkeypatch):
    import pods
    (tmp_path/'cpu.max').write_text('100000 100000')
    monkeypatch.setenv('RH_CGROUP_ROOT', str(tmp_path))
    assert pods.local_resources(nproc=8, gpu_probe=lambda: '') == (1.0, 0)


def test_selection_gate_uses_bundled_package_from_an_unrelated_directory(tmp_path):
    import os
    import subprocess
    env = dict(os.environ)
    env.pop('RH_SELECTION_GATE', None)
    env.pop('PYTHONPATH', None)
    repo = Path(__file__).resolve().parents[1]
    script = "import sys; sys.path.insert(0, sys.argv[1]); import selection_policy; print(selection_policy.gate().__file__)"
    result = subprocess.run([sys.executable, '-c', script, str(repo/'harness')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert Path(result.stdout.strip()) == repo/'selection/selection_gate/__init__.py'


def test_selection_gate_override_expands_home_without_discovery(tmp_path):
    import os
    import subprocess
    package = tmp_path/'custom-gate/selection_gate'
    package.mkdir(parents=True)
    (package/'__init__.py').write_text("MARKER = 'synthetic gate override'\n")
    env = dict(os.environ, HOME=str(tmp_path), RH_SELECTION_GATE='~/custom-gate')
    env.pop('PYTHONPATH', None)
    repo = Path(__file__).resolve().parents[1]
    script = "import sys; sys.path.insert(0, sys.argv[1]); import selection_policy; print(selection_policy.gate().MARKER)"
    result = subprocess.run([sys.executable, '-c', script, str(repo/'harness')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'synthetic gate override'
