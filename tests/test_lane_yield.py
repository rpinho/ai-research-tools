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


def test_unsurfaced_holds_claims_fail_for_any_subject_without_a_hold_file(tmp_path, monkeypatch):
    # 11-05: an audit took a hand-picked subject list, so alpha's HOLDS claims were never audited.
    # The subject label is the claim dir name up to the first underscore, so these ids carry one.
    now = time.time()
    claim(tmp_path / 'claims/beta_M1', True, 'HOLDS', now - 60)
    claim(tmp_path / 'claims/alpha_B6', False, 'HOLDS WITH CAVEATS', now - 60)
    claim(tmp_path / 'claims/alpha_B9', False, 'FAILS', now - 60)
    (tmp_path / 'surface_a').mkdir()
    (tmp_path / 'surface_a/surface.csv').write_text('id,subject,new\nbeta_M1,beta,yes\n')
    cfg = dict(logs=[], claims=[str(tmp_path / 'claims/*')], hold=str(tmp_path / 'HOLD'),
               surface_audit=dict(claims=[str(tmp_path / 'claims/*')], csv=[str(tmp_path / 'surface_*/surface*.csv')]))
    r = ly.check_lane('f', cfg, now)
    assert r['status'] == 'FAIL' and r['fails'] == [
        'UNSURFACED: 1 HOLDS claims of 1 subjects never audited (alpha 1)']
    monkeypatch.setattr(ly.Path, 'home', lambda: tmp_path)  # no real tell-general push
    ly.act(r, cfg)
    assert not (tmp_path / 'HOLD').exists()  # paperwork gap, not spending in circles
    (tmp_path / 'surface_b').mkdir()
    (tmp_path / 'surface_b/surface_alpha.csv').write_text('id,subject,new\nalpha_B6,alpha,no\n')
    assert ly.check_lane('f', cfg, now)['status'] == 'PASS'


def test_dry_rounds_fail_when_a_lane_repeats_an_approach_with_no_win(tmp_path):
    # 11-05: c5 ran 17 rounds against a known wall with no portfolio improvement.
    # It produces no launcher logs, so job_hours is 0 and BURNING can never fire.
    now = time.time()
    ledger = tmp_path / 'RESULTS.jsonl'
    cfg = {'logs': [], 'results': [str(ledger)], 'max_attempts_without_new': 6}
    utc = lambda ago: ly.datetime.fromtimestamp(now - ago).astimezone().isoformat()
    ledger.write_text(''.join(json.dumps({'utc': utc(600 * i), 'new': False, 'kind': 'rerun',
                                          'what': f'round {i}', 'referee': 'NONE'}) + '\n' for i in range(8)))
    r = ly.check_lane('c5', cfg, now)
    assert r['attempts'] == 8 and r['status'] == 'FAIL'
    assert any(f.startswith('DRY_ROUNDS') for f in r['fails'])
    # one real improvement clears it
    with open(ledger, 'a') as f:
        f.write(json.dumps({'utc': utc(300), 'new': True, 'kind': 'value', 'referee': 'HOLDS'}) + '\n')
    assert ly.check_lane('c5', cfg, now)['status'] == 'PASS'
    # off by default: a lane that never set the cap is not failed by attempts alone
    assert ly.check_lane('c5', {'logs': [], 'results': [str(ledger)]}, now)['status'] == 'PASS'


def outreach(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as f:
        for row in rows:
            f.write(json.dumps(row) + '\n')


def test_a_claim_that_was_emailed_is_surfaced_without_a_hand_written_csv_row(tmp_path):
    """friends, 11-05: three HOLDS claims were flagged as never audited while all three had
    been emailed. `rh outreach draft --claim DIR` already records which claims reached
    someone, so the check was asking for the same fact twice and the only way to clear it was
    to hand-write CSV rows for messages the ledger had recorded. A message that went out
    surfaces its claims; a draft or a dropped draft does not.
    """
    now = time.time()
    for name in ('alpha_A1', 'beta_B1', 'gamma_C1'):
        claim(tmp_path / 'claims' / name, True, 'HOLDS', now - 60)
    ledger = tmp_path / 'lanes/f/OUTREACH.jsonl'
    outreach(ledger, [
        # Sent: alpha_A1 reached someone, so it is surfaced.
        dict(id='s1', status='sent', claims=[str(tmp_path / 'claims/alpha_A1')],
             sent=dict(utc='2026-11-05T20:00:00+00:00', message_id='m1', via='api')),
        # Only drafted, and only dropped: neither went out.
        dict(id='d1', status='drafted', claims=[str(tmp_path / 'claims/beta_B1')]),
        dict(id='x1', status='dropped', claims=[str(tmp_path / 'claims/gamma_C1')],
             reason='superseded'),
    ])
    cfg = dict(logs=[], claims=[str(tmp_path / 'claims/*')], hold=str(tmp_path / 'HOLD'),
               surface_audit=dict(claims=[str(tmp_path / 'claims/*')],
                                  csv=[str(tmp_path / 'surface_*/surface*.csv')],
                                  outreach=[str(ledger)]))
    r = ly.check_lane('f', cfg, now)
    assert r['status'] == 'FAIL' and r['fails'] == [
        'UNSURFACED: 2 HOLDS claims of 2 subjects never audited (beta 1, gamma 1)']
    # A later send clears the drafted one; the CSV still clears the other, as the second route.
    outreach(ledger, [dict(id='d1', status='sent', claims=[str(tmp_path / 'claims/beta_B1')],
                           sent=dict(utc='2026-11-06T09:00:00+00:00', message_id='m2', via='api'))])
    (tmp_path / 'surface_a').mkdir()
    (tmp_path / 'surface_a/surface.csv').write_text('id,subject,new\ngamma_C1,gamma,no\n')
    assert ly.check_lane('f', cfg, now)['status'] == 'PASS'


def test_a_message_recorded_after_the_fact_still_surfaces_its_claims(tmp_path):
    """`rh outreach record-sent` carries no approval and may carry lint findings, but the
    message did leave, so its claims are surfaced. A `closed` row with no send is not.
    """
    now = time.time()
    claim(tmp_path / 'claims/alpha_A1', True, 'HOLDS', now - 60)
    claim(tmp_path / 'claims/beta_B1', True, 'HOLDS', now - 60)
    ledger = tmp_path / 'lanes/f/OUTREACH.jsonl'
    outreach(ledger, [
        dict(id='r1', status='sent', claims=[str(tmp_path / 'claims/alpha_A1')],
             sent=dict(utc='2026-11-05T22:44:00+00:00', message_id='m1', via='api'),
             recorded_after_the_fact=dict(reason='sent from his phone', lint_ok=False,
                                          findings=['questions: at most 1 question(s); found 3'])),
        # Closed without ever being sent: a draft that was abandoned, not a message.
        dict(id='c1', status='closed', claims=[str(tmp_path / 'claims/beta_B1')], reason='stale'),
    ])
    cfg = dict(logs=[], claims=[str(tmp_path / 'claims/*')], hold=str(tmp_path / 'HOLD'),
               surface_audit=dict(claims=[str(tmp_path / 'claims/*')], outreach=[str(ledger)]))
    assert ly.check_lane('f', cfg, now)['fails'] == [
        'UNSURFACED: 1 HOLDS claims of 1 subjects never audited (beta 1)']


def test_the_outreach_route_is_optional_and_a_corrupt_line_does_not_blind_it(tmp_path):
    now = time.time()
    claim(tmp_path / 'claims/alpha_A1', True, 'HOLDS', now - 60)
    ledger = tmp_path / 'lanes/f/OUTREACH.jsonl'
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"not json\n\n' + json.dumps(
        dict(id='s1', status='sent', claims=[str(tmp_path / 'claims/alpha_A1')],
             sent=dict(utc='2026-11-05T20:00:00+00:00', message_id='m', via='api'))) + '\n')
    base = dict(logs=[], claims=[str(tmp_path / 'claims/*')], hold=str(tmp_path / 'HOLD'))
    # A lane that configures no outreach globs is unaffected: the claim is still unsurfaced.
    assert ly.check_lane('f', dict(base, surface_audit=dict(
        claims=[str(tmp_path / 'claims/*')])), now)['status'] == 'FAIL'
    # A missing file is not an error either.
    assert ly.check_lane('f', dict(base, surface_audit=dict(
        claims=[str(tmp_path / 'claims/*')], outreach=[str(tmp_path / 'nope/*.jsonl')])),
        now)['status'] == 'FAIL'
    # And the good line after the corrupt one is still read.
    assert ly.check_lane('f', dict(base, surface_audit=dict(
        claims=[str(tmp_path / 'claims/*')], outreach=[str(ledger)])), now)['status'] == 'PASS'
