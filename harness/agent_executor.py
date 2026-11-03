#!/usr/bin/env python3
"""Build explicit backend commands; inherit authentication from the caller."""
import argparse
from pathlib import Path
import subprocess
import sys


def command(backend, executable, model, effort, directory, output, budget='1.00'):
    if backend == 'codex':
        return [executable, 'exec', '--ignore-user-config', '--skip-git-repo-check',
                '--cd', str(directory), '-m', model, '-c', 'model_reasoning_effort='+effort,
                '--sandbox', 'workspace-write', '-o', str(output), '-']
    if backend == 'claude':
        return [executable, '-p', '--model', model, '--effort', effort,
                '--output-format', 'json', '--max-budget-usd', budget,
                '--permission-mode', 'acceptEdits', '--allowedTools', 'Read,Write,Edit',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--setting-sources', '', '--disable-slash-commands',
                '--no-session-persistence']
    raise ValueError('AGENT_BACKEND must be codex or claude')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=('codex', 'claude'), required=True)
    p.add_argument('--executable', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--effort', required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--budget', default='1.00')
    a = p.parse_args(argv)
    cmd = command(a.backend, a.executable, a.model, a.effort, a.directory, a.output, a.budget)
    if a.backend == 'codex':
        return subprocess.call(cmd, cwd=a.directory)
    # Preserve the provider's JSON result, including usage and budget errors.
    with a.output.open('wb') as out:
        proc = subprocess.Popen(cmd, cwd=a.directory, stdout=subprocess.PIPE)
        for block in iter(lambda: proc.stdout.read(65536), b''):
            out.write(block)
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()
        return proc.wait()


if __name__ == '__main__':
    raise SystemExit(main())
