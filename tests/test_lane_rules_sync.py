import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lrs', ROOT / 'harness/tools/lane-rules-sync/lane_rules_sync.py')
lrs = importlib.util.module_from_spec(spec); spec.loader.exec_module(lrs)


def lanes(tmp_path, names):
    for n in names:
        (tmp_path / 'lanes' / n).mkdir(parents=True)
    return tmp_path


def test_common_block_is_the_hard_rule_and_comes_from_one_file():
    common = lrs.source(ROOT)
    assert 'the goal must not turn into process, and success is never activity' in common
    assert common.startswith(lrs.START) and common.endswith(lrs.END)


def test_sync_creates_updates_and_is_idempotent(tmp_path):
    root = lanes(tmp_path, ['a', 'b'])
    (root / 'lanes/a/RULES.md').write_text('# a lane rules\n\nLane-specific line that must survive.\n')
    # b has no RULES.md at all: the drift this tool exists to stop
    assert lrs.main(['--root', str(root), '--repo', str(ROOT), '--check']) == 2
    assert lrs.main(['--root', str(root), '--repo', str(ROOT)]) == 0
    a, b = (root / 'lanes/a/RULES.md').read_text(), (root / 'lanes/b/RULES.md').read_text()
    assert 'success is never activity' in a and 'success is never activity' in b
    assert 'Lane-specific line that must survive.' in a
    assert (root / 'lanes/a/RULES.md.bak-lane-rules-sync').is_file()
    assert lrs.main(['--root', str(root), '--repo', str(ROOT), '--check']) == 0  # idempotent
    assert (root / 'lanes/a/RULES.md').read_text() == a


def test_an_edited_block_is_restored_and_lane_text_is_untouched(tmp_path):
    root = lanes(tmp_path, ['a'])
    lrs.main(['--root', str(root), '--repo', str(ROOT)])
    f = root / 'lanes/a/RULES.md'
    f.write_text(f.read_text().replace('success is never activity', 'anything goes') + '\nLane tail.\n')
    assert lrs.main(['--root', str(root), '--repo', str(ROOT), '--check']) == 2
    lrs.main(['--root', str(root), '--repo', str(ROOT)])
    out = f.read_text()
    assert 'success is never activity' in out and 'anything goes' not in out and 'Lane tail.' in out
