#!/usr/bin/env python3
"""Report declarations for all ten practices; never read hidden data or score."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'harness'))
import rh
import lane_setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('lane')
    args = parser.parse_args()
    if not rh.re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.lane):
        parser.error('invalid lane name')
    lane = args.root.expanduser() / 'lanes' / args.lane
    try:
        record, policy = lane_setup.read(lane)
        errors = lane_setup.audit(lane, rh)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    checks = [
        ('Test-like split and fold-local preprocessing', policy.get('split') and policy.get('preprocessing_inside_folds'), policy.get('split_reason', '')),
        ('Independent measurements and SEM', policy.get('min_measurements', 10) >= 10, 'minimum {}; no measurements verified'.format(policy.get('min_measurements', 10))),
        ('Training-only augmentation', type(policy.get('augmentation_used')) is bool, 'augmentation_used={}'.format(policy.get('augmentation_used'))),
        ('Final fitting method', policy.get('final_fit'), policy.get('final_fit', '')),
        ('Diverse ensemble', policy.get('final_fit') == 'fold_ensemble', 'no diversity evidence verified'),
        ('Successive backtests and learning curves', False, 'required at v2 scoring; no backtests or learning curves supplied'),
        ('Baseline and independent-unit bootstrap', bool(record['frozen'].get('bar')), record['frozen'].get('bar', '') + '; no bootstrap evidence supplied'),
        ('All trials and fresh reruns', policy.get('n_trials_total') == len(policy.get('trials', [])), '{} initial trials; fresh reruns required at scoring'.format(policy.get('n_trials_total'))),
        ('Fixed hidden set and sparse use', policy.get('sealed_set_id') and policy.get('sealed_budget'), '{}; {} selector batch(es)'.format(policy.get('sealed_set_id'), policy.get('sealed_budget'))),
        ('Multiple submissions', policy.get('submissions'), '{} permitted submission(s); none uploaded'.format(policy.get('submissions', 1))),
    ]
    for number, (practice, declared, detail) in enumerate(checks, 1):
        print('{}. {}: {} — {}'.format(number, practice, 'DECLARED' if declared else 'UNDECLARED', detail))
    print('Declarations only; no training, physical independence or hidden access isolation verified.')
    for error in errors:
        print('FAIL: ' + error)
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
