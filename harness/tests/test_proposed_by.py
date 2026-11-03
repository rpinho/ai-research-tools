import json, os, subprocess, sys, tempfile
from pathlib import Path

RH = Path(__file__).resolve().parent.parent / 'rh.py'


def test_proposed_by_stored_and_defaults_empty(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('rh_mod', RH)
    rh = importlib.util.module_from_spec(spec); spec.loader.exec_module(rh)
    row = rh.new_idea('internal', '', 'claim x', 'test', proposed_by='gpt-6-astra')
    assert row['proposed_by'] == 'gpt-6-astra'
    row2 = rh.new_idea('internal', '', 'claim y', 'test')
    assert 'proposed_by' not in row2 or row2['proposed_by'] == ''
