"""Offline behavioral tests for closes, frozen scoring and source coverage."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rh
import idea_policy as policy
import sweep
from policy_fixtures import prior_art, config


@pytest.fixture
def lane(tmp_path):
    lane = tmp_path/'lanes/test'
    lane.mkdir(parents=True)
    (lane/'INTAKE.toml').write_text('keywords = []\n')
    return lane


def cli(lane, *args):
    out, err = io.StringIO(), io.StringIO()
    # Keep model routing deterministic; no actual model-pick subprocess in these tests.
    with patch.object(rh, 'taste_models', return_value=('fixture-taste',)),\
            patch.object(rh, 'session_model', return_value=None),\
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = rh.main(['--root', str(lane.parents[1])] + list(args))
    return rc, out.getvalue(), err.getvalue()


def add(lane):
    rc, out, err = cli(lane, 'ideas','add','test','--source-type','internal','--claim','a testable idea')
    assert rc == 0, err
    return out.strip()


def promote(lane, key, **kw):
    survey = prior_art(lane/'prior.json')
    args = ['ideas','promote','test',key,'--board','board','--test','same metric and fixed baseline',
            '--bar','strictly greater than 1','--prior-art',str(survey),'--decided-by','fixture-taste']
    if kw.get('sealed',True): args += ['--sealed-holdout']
    if kw.get('no_holdout'): args += ['--no-holdout-reason',kw['no_holdout']]
    return cli(lane,*args)


def current(lane,key):
    return rh.latest(rh.rows(lane/'IDEAS.jsonl'))[key]


def result(lane,key,seed=1,text=None):
    cfg = config(lane/('config-'+str(seed)+'.json'), seed=seed)
    path = lane/('result-'+str(seed)+'.md')
    path.write_text(text or 'metric={} < frozen bar; failed\n'.format(seed/10))
    return cli(lane,'ideas','result','test',key,'--result-path',str(path),'--config-path',str(cfg))


def test_no_silent_close_and_explicit_triage(lane):
    key=add(lane)
    args=('ideas','close','test',key,'--closed-reason','not worth pursuing','--decided-by','fixture-taste')
    rc,_,err=cli(lane,*args)
    assert rc==2 and 'no silent close' in err
    assert len(rh.rows(lane/'IDEAS.jsonl'))==1
    assert cli(lane,*args,'--no-test-reason','outside the organiser rules')[0]==0
    row=current(lane,key)
    assert row['no_test_reason']=='outside the organiser rules'
    errors,warnings=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert not errors
    assert any('taste close with no run' in s for s in warnings)


@pytest.mark.parametrize('n',[-1,0,1,2,100])
def test_declared_attempts_cannot_invent_runs(lane,n):
    key=add(lane)
    rc,_,err=cli(lane,'ideas','close','test',key,'--closed-reason','failed','--attempts',str(n),'--decided-by','fixture-taste')
    assert rc==2 and ('minimum' in err or 'recorded' in err)
    assert current(lane,key)['status']=='new'


def test_distinct_configs_and_evidence_backed_close(lane):
    key=add(lane); assert promote(lane,key)[0]==0
    assert result(lane,key,1)[0]==0
    rc,_,err=cli(lane,'ideas','close','test',key,'--closed-reason','negative result',
                  '--attempts','2','--decided-by','fixture-taste')
    assert rc==2 and 'recorded' in err
    assert result(lane,key,2)[0]==0
    assert cli(lane,'ideas','close','test',key,'--closed-reason','both variants failed',
               '--attempts','2','--decided-by','fixture-taste')[0]==0
    row=current(lane,key)
    assert row['result_path'].endswith('result-2.md') # close preserves existing evidence
    assert len(row['attempt_records'])==2
    assert policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)==([],[])


def test_same_config_with_different_formatting_is_not_a_variant(lane):
    key=add(lane); assert promote(lane,key)[0]==0
    assert result(lane,key,1)[0]==0
    other=lane/'same-config.json'; other.write_text('{ "seed" : 1 }\n')
    path=lane/'another-run.md'; path.write_text('same config repeated; metric=0.15')
    assert cli(lane,'ideas','result','test',key,'--result-path',str(path),'--config-path',str(other))[0]==0
    rc,_,err=cli(lane,'ideas','close','test',key,'--closed-reason','negative','--attempts','2','--decided-by','fixture-taste')
    assert rc==2 and 'distinct configs' in err


def test_minimum_is_lane_configurable_but_cannot_be_weakened(lane):
    (lane/'INTAKE.toml').write_text('minimum_attempts = 3\n')
    key=add(lane); assert promote(lane,key)[0]==0
    for n in (1,2): assert result(lane,key,n)[0]==0
    assert cli(lane,'ideas','close','test',key,'--closed-reason','failed','--attempts','2','--decided-by','fixture-taste')[0]==2
    assert result(lane,key,3)[0]==0
    assert cli(lane,'ideas','close','test',key,'--closed-reason','failed','--attempts','3','--decided-by','fixture-taste')[0]==0
    for n in (0,1,True):
        with pytest.raises(ValueError,match='minimum_attempts'): rh.validate_config({'minimum_attempts':n})


def test_a_result_requires_prior_frozen_protocol(lane):
    key=add(lane)
    assert result(lane,key)[0]==2 # still new
    row=current(lane,key); row.update(status='testing',board='board',test='metric',bar='> 1')
    rh.append(lane/'IDEAS.jsonl',row)
    rc,_,err=result(lane,key)
    assert rc==2 and 'frozen bar' in err


def test_promotion_requires_explicit_holdout_disposition(lane):
    key=add(lane)
    rc,_,err=promote(lane,key,sealed=False)
    assert rc==2 and 'sealed-holdout' in err
    assert promote(lane,key,sealed=False,no_holdout='exact arithmetic certificate; no statistical test set')[0]==0
    row=current(lane,key)
    assert row['sealed_holdout'] is False and row['holdout_policy']=='not-applicable'
    assert not policy.frozen_error(row)


def test_sealed_holdout_cannot_be_silently_unsealed(lane):
    key=add(lane); assert promote(lane,key)[0]==0
    row=current(lane,key); row['sealed_holdout']=False
    rh.append(lane/'IDEAS.jsonl',dict(row,event='edit',revised=rh.now()))
    rc,_,err=result(lane,key)
    assert rc==2 and 'inconsistent' in err


def test_no_result_path_result_file_is_not_an_attempt_counter(lane):
    key=add(lane); assert promote(lane,key)[0]==0
    assert result(lane,key)[0]==0
    row=current(lane,key)
    assert cli(lane,'ideas','result','test',key,'--result-path',row['result_path'],
               '--config-path',row['attempt_records'][0]['config_path'])[0]==2


def test_a_one_config_close_is_visible_without_an_extra_review(lane):
    key=add(lane); assert promote(lane,key)[0]==0
    assert result(lane,key)[0]==0
    row=current(lane,key)
    assert cli(lane,'ideas','close','test',key,'--mechanical','--result-path',row['result_path'],
               '--decided-by','a mechanical executor')[0]==0
    errors,warnings=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert not errors and any('one distinct config' in s for s in warnings)


@pytest.mark.parametrize('field,value',[('bar','greater than zero'),('test','different metric'),('board','other')])
def test_even_rehashed_bar_edits_after_result_fail(lane,field,value):
    key=add(lane); assert promote(lane,key)[0]==0; assert result(lane,key)[0]==0
    row=current(lane,key); row[field]=value
    # A direct writer cannot repair the hash to erase the original frozen contract.
    policy.freeze(row,sealed=True)
    rh.append(lane/'IDEAS.jsonl',dict(row,event='tampered',revised=rh.now()))
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('original frozen bar' in s for s in errors)
    assert any('bar edited after first result' in s for s in errors)


@pytest.mark.parametrize('field',['result_path','config_path'])
def test_changed_evidence_bytes_fail_lint_and_close(lane,field):
    key=add(lane); assert promote(lane,key)[0]==0; assert result(lane,key)[0]==0
    row=current(lane,key)
    Path(row['attempt_records'][0][field]).write_text('changed bytes')
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('hash mismatch' in s for s in errors)
    assert cli(lane,'ideas','close','test',key,'--closed-reason','failed','--decided-by','fixture-taste')[0]==2


def test_result_pointer_must_match_receipt(lane):
    key=add(lane); assert promote(lane,key)[0]==0; assert result(lane,key)[0]==0
    row=current(lane,key); row['result_path']=str(lane/'invented.md')
    rh.append(lane/'IDEAS.jsonl',dict(row,event='edit',revised=rh.now()))
    assert any('current result pointer' in s for s in policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)[0])


def test_cannot_erase_attempt_history_or_refreeze_after_results(lane):
    key=add(lane); assert promote(lane,key)[0]==0; assert result(lane,key)[0]==0
    assert cli(lane,'ideas','freeze','test',key,'--sealed-holdout','--decided-by','fixture-taste')[0]==2
    row=current(lane,key); row['attempt_records']=[]
    rh.append(lane/'IDEAS.jsonl',dict(row,event='edit',revised=rh.now()))
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('attempt history removed' in s for s in errors)


def test_same_event_freeze_and_result_is_not_preregistration(lane):
    key=add(lane); row=current(lane,key)
    row.update(status='testing',board='board',test='metric',bar='>1',runnable=True)
    policy.freeze(row,sealed=True)
    cfg=config(lane/'cfg.json'); p=lane/'r.md';p.write_text('result')
    policy.record_result(row,p,cfg)
    rh.append(lane/'IDEAS.jsonl',dict(row,revised=rh.now(),event='import'))
    assert any('no earlier frozen bar event' in s for s in policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)[0])


def test_existing_testing_row_can_freeze_before_a_future_run(lane):
    key=add(lane); row=current(lane,key)
    row.update(status='testing',board='board',test='metric',bar='>1')
    rh.append(lane/'IDEAS.jsonl',row)
    p=prior_art(lane/'prior.json')
    rc,_,err=cli(lane,'ideas','freeze','test',key,'--sealed-holdout','--prior-art',str(p),'--decided-by','fixture-taste')
    assert rc==0,err
    assert result(lane,key)[0]==0


@pytest.mark.parametrize('source',policy.SOURCES)
def test_every_prior_art_source_is_mandatory(lane,source):
    p=prior_art(lane/'prior.json'); data=json.loads(p.read_text());del data['sources'][source];p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match=source): policy.prior_art(p)


def test_empty_or_prose_only_prior_art_does_not_pass(lane):
    p=lane/'p.md'
    for text in ('','best method is known; GitHub X arXiv leaderboard'):
        p.write_text(text)
        with pytest.raises(ValueError):policy.prior_art(p)


def test_searched_none_is_valid_but_blocked_and_empty_hits_are_not(lane):
    p=prior_art(lane/'prior.json');assert policy.prior_art(p)['baseline']
    original=json.loads(p.read_text())
    for update in ({'status':'blocked'},{'status':'hits','hits':[]},{'query':''},{'note':''},{'searched_at':'2099-01-01T00:00:00Z'}):
        data=deepcopy(original);data['sources']['github'].update(update);p.write_text(json.dumps(data))
        with pytest.raises(ValueError):policy.prior_art(p)


def test_x_coverage_binds_existing_capture_bytes(lane):
    p=prior_art(lane/'prior.json');data=json.loads(p.read_text())
    Path(data['sources']['x']['capture_path']).write_text('changed browser capture')
    with pytest.raises(ValueError,match='capture hash mismatch'):policy.prior_art(p)


def test_markdown_receipt_supported(lane):
    p=prior_art(lane/'prior.json');payload=p.read_text()
    p.write_text('# Prior art\n\n```rh-prior-art\n'+payload+'\n```\n')
    assert policy.prior_art(p)['baseline']


def test_promote_lint_detects_prior_file_changed_after_admission(lane):
    key=add(lane);assert promote(lane,key)[0]==0
    p=Path(current(lane,key)['prior_art']); data=json.loads(p.read_text());data['baseline']='different winner 2.0';p.write_text(json.dumps(data))
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('prior-art receipt hash mismatch' in s for s in errors)


def test_lint_flags_new_direct_writer_close_without_reason(lane):
    key=add(lane);row=current(lane,key)
    rh.append(lane/'IDEAS.jsonl',dict(row,status='closed',event='import',revised=rh.now(),closed_reason='taste'))
    assert any('no silent close' in s for s in policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)[0])


def test_legacy_gaps_are_visible_without_rewriting_history(lane):
    row=dict(id='old',created='2026-10-30T12:00:00+00:00',status='closed',event='close',
             decided_by='fixture-taste',result_path='',closed_reason='taste')
    before=json.dumps(row,sort_keys=True)
    errors,warnings=policy.audit([row],lane)
    assert not errors and any('UNRECORDED' in s for s in warnings)
    assert json.dumps(row,sort_keys=True)==before


@pytest.mark.parametrize('name',['restricted-a','restricted-b'])
def test_new_policy_skips_excluded_lanes_without_waiving_old_gates(tmp_path,name):
    lane=tmp_path/name
    assert not policy.enabled(lane)
    assert policy.audit([dict(id='bad',status='closed')],lane)==([],[])
    assert rh.promotion_error({'board':'A'}) # original promotion requirements remain


def test_sweep_compiler_requires_per_idea_coverage(lane):
    paths={s:lane/(s+'.md') for s in ('search','read','kill')}
    paths['search'].write_text('IDEA B-1: https://example.org method; metric; test; risk\nDONE\n')
    paths['read'].write_text('DONE\n');paths['kill'].write_text('RESULT B-1: KILLED value=0; pass >1\nDONE\n')
    cfg={'evidence_policy':True}
    assert any('prior-art B-1' in s for s in sweep.check(paths,[],cfg))
    prior_art(lane/'prior-art-B-1.json')
    assert sweep.check(paths,[],cfg)==[]


def test_failed_coverage_does_not_orphan_an_idea_declared_killed(lane):
    paths={s:lane/(s+'.md') for s in ('search','kill')}
    paths['search'].write_text('IDEA B-1: public method still needs coverage\nDONE\n')
    paths['kill'].write_text('RESULT B-1: KILLED value=0 pass>1\nDONE\n')
    assert sweep.carry_over(lane,paths,rh,rh.now(),preserve_all=True)==['B-1']
    assert sweep.carry_over(lane,paths,rh,rh.now(),preserve_all=True)==[]
    assert len(rh.rows(lane/'IDEAS.jsonl'))==1


def test_reading_a_legacy_result_does_not_reclassify_it_as_a_new_run(lane):
    old=dict(id='legacy',created='2026-10-30T12:00:00+00:00',revised='2026-10-30T13:00:00+00:00',
             status='closed',event='close',result_path='/old/report.md',board='board',test='metric',bar='>1')
    read=dict(old,event='read',revised=rh.now())
    errors,warnings=policy.audit([old,read],lane)
    assert not errors and any('result lacks frozen bar' in s for s in warnings)


def test_close_without_explicit_result_path_requires_attempts_or_disposition(lane):
    key=add(lane);assert promote(lane,key)[0]==0;assert result(lane,key)[0]==0
    rc,_,err=cli(lane,'ideas','close','test',key,'--closed-reason','negative','--decided-by','fixture-taste')
    assert rc==2 and 'no silent close' in err


def test_clearing_a_result_path_cannot_hide_later_bar_edits(lane):
    key=add(lane);assert promote(lane,key)[0]==0;assert result(lane,key)[0]==0
    row=current(lane,key);row.update(result_path='',bar='changed after the data',attempt_records=[])
    rh.append(lane/'IDEAS.jsonl',dict(row,event='edit',revised=rh.now()))
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('bar edited after first result' in s for s in errors)
    assert any('attempt history removed' in s for s in errors)


def test_legacy_ts_records_keep_the_existing_grandfathering(lane):
    assert policy.audit([dict(id='old',ts='2026-10-30T00:00:00Z',status='lead')],lane)==([],[])


def test_distinct_config_runs_may_measure_identical_results(lane):
    key=add(lane);assert promote(lane,key)[0]==0
    assert result(lane,key,1,text='metric=0; same measured negative outcome')[0]==0
    assert result(lane,key,2,text='metric=0; same measured negative outcome')[0]==0
    assert cli(lane,'ideas','close','test',key,'--closed-reason','both variants negative',
               '--attempts','2','--decided-by','fixture-taste')[0]==0


def test_direct_writer_start_cannot_bypass_prior_art_and_frozen_bar(lane):
    key=add(lane);row=current(lane,key)
    rh.append(lane/'IDEAS.jsonl',dict(row,status='testing',event='import',revised=rh.now(),
                                   board='board',test='metric',bar='>1'))
    errors,_=policy.audit(rh.rows(lane/'IDEAS.jsonl'),lane)
    assert any('prior-art' in s or 'nonexistent-rh-prior-art' in s for s in errors)
    assert any('frozen bar' in s for s in errors)
