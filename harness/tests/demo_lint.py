"""Offline lint demonstration. All fixtures live in a temporary harness root."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rh


def main():
    with tempfile.TemporaryDirectory(prefix='rh-lint-demo-') as tmp:
        root = Path(tmp)
        empty = root / 'lanes/empty'
        healthy = root / 'lanes/healthy'
        empty.mkdir(parents=True)
        healthy.mkdir(parents=True)
        print('Empty lane (isolated fixture):')
        empty_code = rh.main(['--root', str(root), 'lint', 'empty'])
        print('exit={}'.format(empty_code))
        # This audit is a synthetic fixture, never production evidence.
        for n in range(3):
            rh.append(healthy / 'IDEAS.jsonl', rh.new_idea('paper', 'https://example.org/' + str(n), 'Fixture idea ' + str(n)))
        rh.append(healthy / 'INTAKE_RUNS.jsonl', dict(created=rh.now(), sources_read=['fixture-arxiv'], added=3))
        (healthy / 'CADENCE.toml').write_text('deliverable = "synthetic receipt"\nevery_hours = 24\nowner_session = "toy-agent"\nevidence_globs = ["verified.md"]\n')
        (healthy / 'verified.md').write_text('Synthetic fixture: not a research result.\n')
        print('Healthy lane (isolated fixture):')
        healthy_code = rh.main(['--root', str(root), 'lint', 'healthy'])
        print('exit={}'.format(healthy_code))
        assert empty_code == 1 and healthy_code == 0


if __name__ == '__main__':
    main()
