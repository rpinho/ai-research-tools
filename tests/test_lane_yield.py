import importlib.util, json, os, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lane_yield', ROOT / 'harness/tools/lane-yield/lane_yield.py')
ly = importlib.util.module_from_spec(spec); spec.loader.exec_module(ly)


def write_log(p, hours):
    p.write_text('=== run 1 model m Tue Nov  3 00:00:00 EST 2026\nwork\n'
                 f'exit 0 Tue Nov  3 {hours:02d}:00:00 EST 2026\n')


def claim(d, new, verdict, when):
    d.mkdir(parents=True)
    head = ('NEW: yes\nPrinted: 1.0 in https://arxiv.org/abs/2601.00001\nOurs: 1.1\nCited by: 0 papers since 2026 (OpenAlex W1, checked 2026-11-04); closest: none\nKind: value\n' if new else 'NEW: no\n')
    (d / 'CLAIM.md').write_text(head + 'Standard method: x\nDeviations: none\n')
    (d / 'VERDICT.md').write_text(verdict + '\n')
    os.utime(d / 'VERDICT.md', (when, when))


def test_parse_launcher_stamps():
    assert ly.parse_stamp('exit 0 Wed Nov  4 02:23:40 EST 2026') is not None
    assert ly.parse_stamp('=== attempt 2 model gpt Wed Nov  4 02:23:40 EST 2026') is not None
    assert ly.parse_stamp('exit') is None


def test_burning_lane_fails_and_new_result_passes(tmp_path):
    log = tmp_path / 'lane_a.log'; write_log(log, 23)
    now = ly.parse_stamp('exit 0 Tue Nov  3 23:30:00 EST 2026')
    cfg = dict(logs=[str(tmp_path / '*.log')], claims=[str(tmp_path / 'claims/*')])
    claim(tmp_path / 'claims/x1', False, 'HOLDS', now - 60)
    r = ly.check_lane('a', cfg, now)
    assert r['status'] == 'FAIL' and r['fails'][0].startswith('BURNING')
    claim(tmp_path / 'claims/x2', True, 'HOLDS', now - 60)
    assert ly.check_lane('a', cfg, now)['status'] == 'PASS'


def test_rubber_stamp_referee_fails(tmp_path):
    now = time.time()
    for i in range(25):
        claim(tmp_path / f'claims/c{i}', i == 0, 'HOLDS', now - 60)
    cfg = dict(logs=[], claims=[str(tmp_path / 'claims/*')])
    r = ly.check_lane('a', cfg, now)
    assert any(f.startswith('RUBBER_STAMP') for f in r['fails'])


def test_results_ledger_counts_refereed_new_results(tmp_path):
    log = tmp_path / 'math_a.log'; write_log(log, 23)
    now = ly.parse_stamp('exit 0 Tue Nov  3 23:30:00 EST 2026')
    ledger = tmp_path / 'RESULTS.jsonl'
    cfg = {'logs': [str(log)], 'results': [str(ledger)]}
    assert ly.check_lane('m', cfg, now)['status'] == 'FAIL'
    utc = ly.datetime.fromtimestamp(now - 3600).astimezone().isoformat()
    ledger.write_text(json.dumps({'utc': utc, 'new': True, 'kind': 'value', 'referee': 'NONE'}) + '\n')
    assert ly.check_lane('m', cfg, now)['status'] == 'FAIL'  # unrefereed does not count
    ledger.write_text(json.dumps({'utc': utc, 'new': True, 'kind': 'value', 'referee': 'HOLDS'}) + '\n')
    assert ly.check_lane('m', cfg, now)['status'] == 'PASS'


def test_report_only_lane_gets_no_hold_file(tmp_path, monkeypatch):
    hold = tmp_path / 'HOLD'
    monkeypatch.setattr(ly.Path, 'home', lambda: tmp_path)
    ly.act({'lane': 'm', 'as_of': 'x', 'fails': ['BURNING']}, {'hold': str(hold), 'report_only': True})
    assert not hold.exists()
    ly.act({'lane': 'm', 'as_of': 'x', 'fails': ['BURNING']}, {'hold': str(hold)})
    assert hold.exists()
