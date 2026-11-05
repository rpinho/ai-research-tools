"""Host settings that keep a deployed controller's older ledgers and disk limits valid."""
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harness'))
import harvest
import idea_policy
import rh


def test_disk_floor_default_and_override(monkeypatch):
    try:
        assert importlib.reload(harvest).DISK_FLOOR_BYTES == 12_000_000_000
        monkeypatch.setenv('RH_DISK_FLOOR_BYTES', '33000000000')
        assert importlib.reload(harvest).DISK_FLOOR_BYTES == 33_000_000_000
    finally:
        monkeypatch.delenv('RH_DISK_FLOOR_BYTES', raising=False)
        importlib.reload(harvest)


def test_owner_alias_is_a_valid_source_only_when_configured(monkeypatch):
    try:
        assert 'owner' in importlib.reload(rh).SOURCES and 'operator-a' not in rh.SOURCES
        monkeypatch.setenv('RH_OWNER_ALIASES', 'operator-a')
        assert 'operator-a' in importlib.reload(rh).SOURCES
    finally:
        monkeypatch.delenv('RH_OWNER_ALIASES', raising=False)
        importlib.reload(rh)


def _exception_errors(tmp_path, decided_by):
    lane = tmp_path / 'lane'
    lane.mkdir(exist_ok=True)
    (lane / 'lint_exceptions.jsonl').write_text(
        '{"id": "i1", "reason": "r", "evidence_path": "missing", "decided_by": "%s", "utc": "2026-11-01T00:00:00Z"}\n'
        % decided_by)
    return ' '.join(str(e) for e in idea_policy.resolutions([], lane)[2])


def test_owner_alias_may_decide_exceptions(tmp_path, monkeypatch):
    assert 'must identify Operator' not in _exception_errors(tmp_path, 'Operator via review')
    assert 'must identify Operator' in _exception_errors(tmp_path, 'Alias-a via review')
    monkeypatch.setenv('RH_OWNER_ALIASES', 'alias-a')
    assert 'must identify Operator' not in _exception_errors(tmp_path, 'Alias-a via review')


def test_env_file_fills_only_unset_keys(tmp_path, monkeypatch):
    import settings
    env = tmp_path / 'env'
    env.write_text('# host values\n\nRH_TEST_A=one\nexport RH_TEST_B="two words"\nRH_TEST_C=\'~/x\'\n')
    monkeypatch.setenv('RH_TEST_A', 'real')
    monkeypatch.delenv('RH_TEST_B', raising=False)
    monkeypatch.delenv('RH_TEST_C', raising=False)
    try:
        assert settings.load_env_file(env) == ['RH_TEST_B', 'RH_TEST_C']
        import os
        assert (os.environ['RH_TEST_A'], os.environ['RH_TEST_B'], os.environ['RH_TEST_C']) == ('real', 'two words', '~/x')
    finally:
        monkeypatch.delenv('RH_TEST_B', raising=False)
        monkeypatch.delenv('RH_TEST_C', raising=False)


def test_env_file_location_and_bad_lines(tmp_path, monkeypatch):
    import settings
    monkeypatch.setenv('RH_ENV_FILE', str(tmp_path / 'custom'))
    assert settings.env_file() == tmp_path / 'custom'
    monkeypatch.delenv('RH_ENV_FILE')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    assert settings.env_file() == tmp_path / 'research-harness/env'
    assert settings.load_env_file(tmp_path / 'absent') == []
    bad = tmp_path / 'bad'
    bad.write_text('rh_lower=1\n')
    try:
        settings.load_env_file(bad)
    except ValueError as exc:
        assert 'bad:1' in str(exc)
    else:
        raise AssertionError('lowercase key accepted')


def test_intake_runs_declared_lanes_in_pinned_order(tmp_path, monkeypatch):
    import intake_all
    for name, intake in (('b-lane', True), ('a-lane', True), ('outreach-only', False), ('c-lane', True)):
        (tmp_path / 'lanes' / name).mkdir(parents=True)
        if intake:
            (tmp_path / 'lanes' / name / 'INTAKE.toml').write_text('')
    monkeypatch.setattr(intake_all, 'ROOT', tmp_path)
    monkeypatch.delenv('RH_INTAKE_LANES', raising=False)
    assert intake_all.lanes() == ['a-lane', 'b-lane', 'c-lane']
    monkeypatch.setenv('RH_INTAKE_LANES', 'c-lane, outreach-only,missing,a-lane')
    assert intake_all.lanes() == ['c-lane', 'a-lane']
