import gzip
import hashlib
import json
import math
import pytest
from selection_gate import optimism, decide, write_receipt, verify_receipt, trajectory_scan


def test_optimism():
    assert optimism(2, 1) == 0
    assert optimism(1, 13000) == pytest.approx(4.35256, abs=.01)
    for sd, n in [(-1, 2), (1, 0), (1, 1.5), (float('nan'), 3)]:
        with pytest.raises(ValueError): optimism(sd, n)


def test_decide_margin_and_strict_floor():
    d = decide([dict(name='a', sealed_scores=[49, 50, 52])], 50, 13000)
    assert d['mean'] > 50 and d['selected'] == 'floor'
    assert d['optimism'] == 0 and d['search_optimism'] > 0
    assert decide([dict(name='a', sealed_scores=[50, 50])], 50, 1)['selected'] == 'floor'
    d = decide([dict(name='a', sealed_scores=[60, 62]), dict(name='b', sealed_scores=[65, 67])], 50, 20)
    assert d['selected'] == 'b'
    assert d['optimism'] == pytest.approx(d['sd'] * math.sqrt(2*math.log(2)))
    assert d['lower_bound'] == d['mean']-d['optimism']-d['margin']
    assert decide([], 50, 1)['selected'] == 'floor'
    with pytest.raises(ValueError): decide([dict(name='a', sealed_scores=[float('nan'), 2])], 50, 1)


def test_receipt(tmp_path):
    file = tmp_path/'prediction'; file.write_bytes(b'original')
    path = tmp_path/'receipt.json'
    write_receipt(path, decide([], 50, 1), hashlib.sha256(file.read_bytes()).hexdigest(), 'run', 'board')
    assert verify_receipt(path, file)['board'] == 'board'
    file.write_bytes(b'changed')
    with pytest.raises(ValueError, match='mismatch'): verify_receipt(path, file)
    with pytest.raises(FileExistsError): write_receipt(path, {}, '0'*64, 'run', 'board')


def test_scan(tmp_path):
    plain = tmp_path/'a.jsonl'; plain.write_text('safe\n'+json.dumps({'text':'secret/target.h5ad'})+'\n')
    zipped = tmp_path/'b.jsonl.gz'
    with gzip.open(zipped, 'wt') as f: f.write('{"path":"secret\\/target.h5ad"}\n')
    hits = trajectory_scan([plain, zipped], ['secret/target.h5ad'])
    assert [(h['file'], h['line_no']) for h in hits] == [(str(plain), 2), (str(zipped), 1)]
    assert trajectory_scan([plain], ['absent']) == []
