"""A baseline you can run, not a number you quoted.

The behaviour worth protecting is the refusal: `add` must not record a baseline that does not
reproduce. That is the whole difference between this and writing the paper's number into a spec,
and it is the thing a future change would most plausibly soften.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('baseline', ROOT / 'harness/tools/baseline.py')
bl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bl)


@pytest.fixture
def lane(tmp_path):
    d = tmp_path / 'lanes' / 'math'
    d.mkdir(parents=True)
    return d


def add(lane, *extra):
    return bl.main(['--lane', str(lane), 'add'] + list(extra))


def rows(lane):
    return bl.read(lane / bl.BASELINES)


def test_a_baseline_that_reproduces_is_recorded(lane):
    assert add(lane, '--name', 'orloski', '--source', '10.1234/x', '--published', '1.8216',
               '--cmd', 'echo 1.8216', '--metric', 'bound') == 0
    row = rows(lane)['orloski']
    assert row['value'] == 1.8216 and row['first_reproduced_utc']


def test_a_baseline_that_does_not_reproduce_is_refused(lane, capsys):
    """The point of the whole module."""
    assert add(lane, '--name', 'claimed', '--source', 'paper', '--published', '1.80',
               '--cmd', 'echo 1.95') == 2
    assert rows(lane) == {}, 'nothing may be recorded'
    assert 'did not reproduce' in capsys.readouterr().err


def test_a_command_that_fails_is_refused(lane):
    assert add(lane, '--name', 'broken', '--source', 'p', '--published', '1.0',
               '--cmd', 'exit 3') == 2
    assert rows(lane) == {}


def test_a_command_that_prints_no_number_is_refused(lane, capsys):
    assert add(lane, '--name', 'quiet', '--source', 'p', '--published', '1.0',
               '--cmd', 'echo done') == 2
    assert 'no number' in capsys.readouterr().err


def test_the_last_number_wins_so_progress_output_is_fine(lane):
    assert add(lane, '--name', 'chatty', '--source', 'p', '--published', '0.42',
               '--cmd', 'printf "step 1 of 3\\nloss 9.99\\n0.42\\n"') == 0


def test_a_json_key_can_name_the_number(lane):
    assert add(lane, '--name', 'keyed', '--source', 'p', '--published', '0.65',
               '--json-key', 'auroc',
               '--cmd', 'printf \'{"n": 40, "auroc": 0.65}\\n\'') == 0
    assert rows(lane)['keyed']['value'] == 0.65


def test_tolerance_is_respected(lane):
    assert add(lane, '--name', 'loose', '--source', 'p', '--published', '1.0',
               '--tolerance', '0.01', '--cmd', 'echo 1.005') == 0
    assert add(lane, '--name', 'tight', '--source', 'p', '--published', '1.0',
               '--tolerance', '0.001', '--cmd', 'echo 1.005') == 2


def test_beating_the_published_number_still_counts_as_reproducing(lane):
    """Refusing this would be the harness calling a lane's own improvement a failure."""
    assert add(lane, '--name', 'better', '--source', 'p', '--published', '1.8216',
               '--lower-is-better', '--cmd', 'echo 1.8100') == 0
    assert add(lane, '--name', 'worse', '--source', 'p', '--published', '1.8216',
               '--lower-is-better', '--cmd', 'echo 1.9000') == 2


def test_run_reports_drift_and_fails(lane, capsys):
    assert add(lane, '--name', 'drifts', '--source', 'p', '--published', '2.0',
               '--cmd', 'cat value.txt') == 2          # no file yet
    (lane / 'value.txt').write_text('2.0\n')
    assert add(lane, '--name', 'drifts', '--source', 'p', '--published', '2.0',
               '--cmd', 'cat value.txt') == 0
    assert bl.main(['--lane', str(lane), 'run', '--name', 'drifts']) == 0
    (lane / 'value.txt').write_text('7.5\n')           # a dependency changed under us
    assert bl.main(['--lane', str(lane), 'run', '--name', 'drifts']) == 2
    assert 'stopped reproducing' in capsys.readouterr().err


def test_check_runs_every_baseline(lane):
    add(lane, '--name', 'a', '--source', 'p', '--published', '1', '--cmd', 'echo 1')
    add(lane, '--name', 'b', '--source', 'p', '--published', '2', '--cmd', 'echo 2')
    assert bl.main(['--lane', str(lane), 'check']) == 0
    add(lane, '--name', 'c', '--source', 'p', '--published', '3', '--cmd', 'cat gone.txt')
    assert bl.main(['--lane', str(lane), 'check']) == 0, 'c was refused, so it is not registered'


def test_an_unknown_name_is_an_error_not_a_silent_pass(lane):
    assert bl.main(['--lane', str(lane), 'run', '--name', 'nope']) == 2


def test_a_corrupt_ledger_line_does_not_lose_the_rest(lane):
    add(lane, '--name', 'good', '--source', 'p', '--published', '1', '--cmd', 'echo 1')
    with (lane / bl.BASELINES).open('a') as f:
        f.write('{not json\n')
    assert 'good' in rows(lane)


def test_the_published_number_can_come_from_a_snapshot(lane):
    """planets asked for this: a typed number is what this module exists to replace."""
    (lane / 'snap.json').write_text(json.dumps(
        {'candidates': [{'tic': '259233660', 'period': 4.2134185}]}))
    assert bl.main(['--lane', str(lane), 'add', '--name', 'eumemic-rank1',
                    '--source', 'eumemic table', '--published-from', 'snap.json:candidates.0.period',
                    '--cmd', 'echo 4.2134185', '--metric', 'days']) == 0
    row = rows(lane)['eumemic-rank1']
    assert row['published'] == 4.2134185
    assert row['published_src']['sha256'] and row['published_src']['value'] == 4.2134185


def test_a_jsonl_receipt_works_too(lane):
    (lane / 'receipt.jsonl').write_text('{"old": 1}\n{"rate": 0.91}\n')
    assert bl.main(['--lane', str(lane), 'add', '--name', 'recovery', '--source', 'our first run',
                    '--published-from', 'receipt.jsonl:rate', '--tolerance', '0.01',
                    '--cmd', 'echo 0.915']) == 0


def test_a_missing_key_is_refused_with_the_keys_it_found(lane, capsys):
    (lane / 'snap.json').write_text(json.dumps({'candidates': []}))
    assert bl.main(['--lane', str(lane), 'add', '--name', 'x', '--source', 's',
                    '--published-from', 'snap.json:period', '--cmd', 'echo 1']) == 2
    assert 'keys:' in capsys.readouterr().err


def test_a_non_numeric_value_is_refused(lane):
    (lane / 'snap.json').write_text(json.dumps({'period': 'unknown'}))
    assert bl.main(['--lane', str(lane), 'add', '--name', 'x', '--source', 's',
                    '--published-from', 'snap.json:period', '--cmd', 'echo 1']) == 2


def test_both_or_neither_published_is_refused(lane):
    (lane / 'snap.json').write_text(json.dumps({'period': 1.0}))
    assert bl.main(['--lane', str(lane), 'add', '--name', 'x', '--source', 's', '--published', '1.0',
                    '--published-from', 'snap.json:period', '--cmd', 'echo 1']) == 2
    assert bl.main(['--lane', str(lane), 'add', '--name', 'y', '--source', 's',
                    '--cmd', 'echo 1']) == 2


def test_a_moved_snapshot_is_reported_not_silently_accepted(lane, capsys):
    """If the table updates, the baseline must be re-registered, not quietly re-compared."""
    snap = lane / 'snap.json'
    snap.write_text(json.dumps({'period': 4.0}))
    assert bl.main(['--lane', str(lane), 'add', '--name', 'p', '--source', 's',
                    '--published-from', 'snap.json:period', '--cmd', 'echo 4.0']) == 0
    assert bl.main(['--lane', str(lane), 'run', '--name', 'p']) == 0
    snap.write_text(json.dumps({'period': 4.5}))
    assert bl.main(['--lane', str(lane), 'run', '--name', 'p']) == 2
    out = capsys.readouterr().out
    assert 'SOURCE' in out and '4.5' in out
