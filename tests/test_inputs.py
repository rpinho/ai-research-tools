"""Public inputs staged once and pinned, so a worker that may not download can still run.

The friends kill test returned BLOCKED twice on 11-06 for the same reason: the public XTW-n308
fixture was not on disk and workers are forbidden to download. The input is public and free; the
lane just had no way to be given it.

The suite forbids network (conftest's `no_internet`), and that guard stays: `urlopen` is faked
here rather than a local server being started, so these tests exercise the same code path with no
socket at all. Two behaviours matter most: a hash mismatch must not leave the file on disk looking
staged, and `path` must never reach the network, because `path` is the call a worker makes.
"""
import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('inputs', ROOT / 'harness/tools/inputs.py')
inp = importlib.util.module_from_spec(spec); spec.loader.exec_module(inp)

BODY = b'NAME : X-n308-k13\nDIMENSION : 308\nEOF\n'
SHA = hashlib.sha256(BODY).hexdigest()
URL = 'https://example.invalid/x.vrp'


class FakeResponse(io.BytesIO):
    def __init__(self, body, url=URL):
        super().__init__(body)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


@pytest.fixture
def served(monkeypatch):
    """Serve a body for any http(s) URL, and record that urlopen was reached at all."""
    state = {'calls': 0, 'body': BODY, 'url': URL}

    def fake_urlopen(req, timeout=None):
        state['calls'] += 1
        if getattr(req, 'full_url', str(req)).endswith('/missing'):
            raise inp.urllib.error.HTTPError(URL, 404, 'Not Found', None, None)
        return FakeResponse(state['body'], state['url'])

    monkeypatch.setattr(inp.urllib.request, 'urlopen', fake_urlopen)
    return state


@pytest.fixture
def lane(tmp_path):
    d = tmp_path / 'lanes' / 'friends'
    d.mkdir(parents=True)
    return d


def add(lane, *extra):
    return inp.main(['--lane', str(lane), 'add'] + list(extra))


def rows(lane):
    return inp.read(lane / inp.INPUTS)


def test_a_pinned_input_is_staged(lane, served):
    assert add(lane, '--name', 'XTW-n308', '--url', URL, '--sha256', SHA,
               '--source', 'CVRPLIB') == 0
    row = rows(lane)['XTW-n308']
    assert row['pinned'] and row['sha256'] == SHA and row['bytes'] == len(BODY)
    assert Path(row['path']).read_bytes() == BODY


def test_a_hash_mismatch_keeps_nothing(lane, served, capsys):
    """The file must not be left on disk looking staged."""
    assert add(lane, '--name', 'x', '--url', URL, '--sha256', 'de' * 32) == 2
    assert rows(lane) == {}
    assert not (lane / inp.STORE / 'x').exists()
    assert 'hash mismatch' in capsys.readouterr().err


def test_staging_without_a_hash_says_it_is_unpinned(lane, served, capsys):
    assert add(lane, '--name', 'loose', '--url', URL) == 0
    assert rows(lane)['loose']['pinned'] is False
    out = capsys.readouterr().out
    assert 'UNPINNED' in out and SHA in out, 'it must print the hash to pin with'


def test_path_is_what_a_worker_calls_and_never_reaches_the_network(lane, served, capsys):
    add(lane, '--name', 'XTW-n308', '--url', URL, '--sha256', SHA)
    capsys.readouterr()        # drop the staging output; what follows must be only the path
    before = served['calls']
    assert inp.main(['--lane', str(lane), 'path', '--name', 'XTW-n308']) == 0
    assert served['calls'] == before, 'path must not fetch anything'
    assert Path(capsys.readouterr().out.strip()).read_bytes() == BODY


def test_an_unstaged_name_tells_you_who_stages_it(lane, capsys):
    assert inp.main(['--lane', str(lane), 'path', '--name', 'XTW-n308']) == 2
    err = capsys.readouterr().err
    assert 'A worker does not download it' in err and 'add --name XTW-n308' in err


def test_check_catches_a_changed_input(lane, served, capsys):
    add(lane, '--name', 'i', '--url', URL, '--sha256', SHA)
    assert inp.main(['--lane', str(lane), 'check']) == 0
    Path(rows(lane)['i']['path']).write_bytes(BODY + b'tampered\n')
    assert inp.main(['--lane', str(lane), 'check']) == 2
    assert 'CHANGED' in capsys.readouterr().out


def test_check_catches_a_deleted_input(lane, served, capsys):
    add(lane, '--name', 'i', '--url', URL, '--sha256', SHA)
    Path(rows(lane)['i']['path']).unlink()
    assert inp.main(['--lane', str(lane), 'check']) == 2
    assert 'MISSING' in capsys.readouterr().out


def test_a_404_and_an_empty_body_are_refused(lane, served):
    assert add(lane, '--name', 'gone', '--url', 'https://example.invalid/missing') == 2
    served['body'] = b''
    assert add(lane, '--name', 'empty', '--url', URL) == 2
    assert rows(lane) == {}


def test_only_http_urls_are_staged(lane, served):
    assert add(lane, '--name', 'local', '--url', 'file:///etc/passwd') == 2
    assert served['calls'] == 0, 'refused before any fetch'
    assert rows(lane) == {}


def test_a_redirect_off_http_is_refused(lane, served):
    served['url'] = 'file:///etc/passwd'
    assert add(lane, '--name', 'sneaky', '--url', URL) == 2
    assert rows(lane) == {}


@pytest.mark.parametrize('name', ['../escape', 'a/b', '.hidden', ''])
def test_a_name_cannot_escape_the_lane(lane, served, name):
    assert add(lane, '--name', name, '--url', URL) == 2


def test_the_size_cap_is_enforced(lane, served, monkeypatch):
    monkeypatch.setattr(inp, 'MAX_BYTES', 4)
    assert add(lane, '--name', 'big', '--url', URL) == 2
    assert rows(lane) == {}
    assert not (lane / inp.STORE / 'big').exists()
