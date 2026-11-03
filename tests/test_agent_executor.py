import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'harness'))
from agent_executor import command
import pytest


def test_codex_command_preserves_paths_and_sandbox():
    cmd = command('codex', '/tools/codex cli', 'gpt-6.1-sol', 'low', '/work with spaces', '/last message')
    assert cmd == ['/tools/codex cli', 'exec', '--ignore-user-config', '--skip-git-repo-check',
                   '--cd', '/work with spaces', '-m', 'gpt-6.1-sol', '-c', 'model_reasoning_effort=low',
                   '--sandbox', 'workspace-write', '-o', '/last message', '-']


def test_claude_command_has_budget_and_explicit_tools():
    cmd = command('claude', 'claude', 'claude-haiku-5-5', 'low', '/work', '/last', '.50')
    assert cmd[:5] == ['claude', '-p', '--model', 'claude-haiku-5-5', '--effort']
    assert cmd[cmd.index('--max-budget-usd')+1] == '.50'
    assert cmd[cmd.index('--permission-mode')+1] == 'acceptEdits'
    assert cmd[cmd.index('--allowedTools')+1] == 'Read,Write,Edit'
    assert '--no-session-persistence' in cmd and '--strict-mcp-config' in cmd
    with pytest.raises(ValueError):
        command('typo', 'cli', 'model', 'low', '/work', '/last')


def test_claude_launcher_binds_route_and_outcome(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    jobs = tmp_path/'jobs'; jobs.mkdir()
    (jobs/'fixture.md').write_text('synthetic fixture')
    roles = tmp_path/'roles.json'
    roles.write_text(json.dumps(dict(roles={'batch': ['fixture-claude']}, routes={
        'fixture-claude': {'canonical': 'fixture-claude', 'cli': 'claude'}})))
    fake = tmp_path/'fake-cli'
    fake.write_text('#!'+sys.executable+'\nimport sys\nprint("fixture")\nsys.exit(7)\n')
    fake.chmod(0o755)
    import os
    env = dict(os.environ, AGENT_BACKEND='claude', LAUNCH_CLAUDE=str(fake),
               LAUNCH_ARTIFACT_DIR=str(jobs), LAUNCH_STATE=str(tmp_path/'registry'),
               RH_ROLES_FILE=str(roles), FORCE_LAUNCH='1')
    proc = subprocess.run(['zsh', str(repo/'tools/launch.sh'), 'fixture', str(tmp_path)],
                          env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 7, proc.stderr
    log = (jobs/'fixture.log').read_text()
    outcome = json.loads(next(line.split(' ', 1)[1] for line in log.splitlines() if line.startswith('MODEL_OUTCOME ')))
    assert outcome['executor'] == 'claude' and outcome['exit_code'] == 7
    assert (jobs/'fixture.last.md').read_text() == 'fixture\n'
