#!/usr/bin/env python3
"""Independent local selector/evaluator. Runs only after every producing process exits."""
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO/'selection')]
from bench.cases import FRESH_SEEDS, SETUPS, policy
from bench.setup_data import file_sha, write_json


def evaluate(case, setup, data, output, expected_prediction_sha256=None):
    import numpy as np
    from bench.worker import read_csv, score, losses
    from selection_gate import decide, write_receipt, verify_receipt
    from selection_gate.policy import consume_scoring, record_optimism, optimism_snapshot
    from selection_gate.v2 import evaluate_v2

    declaration = policy(case)
    version = SETUPS[setup]
    if expected_prediction_sha256 is not None and file_sha(output/'predictions.json') != expected_prediction_sha256:
        raise ValueError('predictions changed after producer exit')
    # Reserve BEFORE opening either sealed target file, for both setups. No retries/reselection.
    scoring_id = setup+'/'+case
    ledger = consume_scoring(output/'sealed-scoring.jsonl', declaration, case, scoring_id)
    prior, anchor = optimism_snapshot(output/'optimism.jsonl', case)
    predictions = json.loads((output/'predictions.json').read_text())
    selection = read_csv(data/'selection-targets.csv')
    if predictions['selection_ids'] != [r['id'] for r in selection]:
        raise ValueError('prediction/target row identity mismatch')
    from bench.cases import CASES
    target, metric = CASES[case]['target'], CASES[case]['metric']
    y_selection = [float(r[target]) for r in selection]
    baseline = predictions['baseline']
    floor = score(metric, y_selection, [baseline]*len(selection))
    measurement_ids = ['fresh-'+str(s) for s in FRESH_SEEDS]
    finalists = []
    for row in predictions['candidates']:
        array = np.asarray(row['selection_predictions'], dtype=float)
        if array.shape != (len(FRESH_SEEDS), len(selection)) or not np.isfinite(array).all():
            raise ValueError('invalid selection prediction dimensions/values')
        unit_scores = np.mean([-losses(metric, y_selection, p) for p in array], axis=0)
        finalists.append(dict(name=row['name'], measurement_ids=measurement_ids,
            sealed_scores=[score(metric, y_selection, p) for p in array],
            unit_scores=unit_scores.tolist(), backtest_scores=row['backtest_scores']))
    if version == 1:
        decision = decide(finalists, floor, len(predictions['trials']))
    else:
        evidence = dict(competition=case, scoring_id=scoring_id, trials=predictions['trials'],
            tuned_seed_ids=['7'], tuned_split_ids=['train-dev'],
            fresh=dict(nobody_tuned_against=True, independent_measurements=True,
                       measurement_kind='seeds', measurement_ids=measurement_ids,
                       seed_ids=[str(s) for s in FRESH_SEEDS], split_ids=['backtest-1', 'backtest-2', 'selection']),
            baseline_unit_scores=(-losses(metric, y_selection, [baseline]*len(selection))).tolist(),
            units=[r['group'] for r in selection], baseline_measurement_ids=measurement_ids,
            baseline_backtest_scores=predictions['baseline_backtest_scores'],
            backtest_split_ids=['backtest-1', 'backtest-2'], backtest_splits=predictions['backtest_splits'])
        write_json(output/'evidence.json', evidence)
        # The numerical gate is shared unchanged. Reservation above allows even baseline/evidence
        # construction to happen after accounting, rather than passing precomputed hidden scores.
        decision = evaluate_v2(finalists, floor, len(predictions['trials']), 2,
                               declaration, evidence, prior=prior)
        optimism = record_optimism(output/'optimism.jsonl', case, scoring_id,
            [row['optimism_drop'] for row in decision['finalists']], prior_sha256=anchor)
        decision.update(scoring_id=scoring_id, competition=case, optimism_prior_sha256=anchor,
                        scoring_ledger_sha256=ledger['sha256'], optimism_ledger_sha256=optimism['sha256'])
    selected = decision['selected']
    choice = next((row for row in predictions['candidates'] if row['name'] == selected), None)
    n_hidden = len(predictions['hidden_ids'])
    # Final hidden scores are NOT gate inputs. Only the frozen winner is evaluated, once.
    if choice is None:
        final = [baseline]*n_hidden
        validation = predictions['baseline_validation_score']
    else:
        final = choice['hidden_prediction']
        validation = choice['tuned_score']
    if len(final) != n_hidden or not np.isfinite(final).all():
        raise ValueError('invalid final predictions')
    submission = output/'submission.json'
    write_json(submission, dict(ids=predictions['hidden_ids'], predictions=final))
    receipt = output/'selection-receipt.json'
    write_receipt(receipt, decision, file_sha(submission), scoring_id, case)
    verify_receipt(receipt, submission)
    # Freeze winner/submission/receipt before the final-test file is even opened.
    hidden = read_csv(data/'hidden-targets.csv')
    if predictions['hidden_ids'] != [r['id'] for r in hidden]:
        raise ValueError('hidden prediction/target row identity mismatch')
    hidden_score = -score(metric, [float(row[target]) for row in hidden], final)
    write_json(output/'result.json', dict(case=case, metric=metric, local_validation_score=-validation,
        hidden_test_score=hidden_score, n_submissions=1, official_uploads=0, n_candidates=len(finalists),
        selected=selected, gate_version=version, gate_accepted=decision['accepted'],
        receipt_verified=True, prediction_sha256=file_sha(output/'predictions.json'),
        submission_sha256=file_sha(submission), receipt_sha256=file_sha(receipt),
        ledger_sha256=ledger['sha256'], selection_decision=decision,
        statistical_holdout=True, final_hidden_evaluations=1,
        hidden_score_kind='frozen local mock holdout, not original Kaggle private score'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--setup', choices=SETUPS, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--predictions-sha256', required=True)
    args = parser.parse_args(argv)
    evaluate(args.case, args.setup, args.data, args.output, args.predictions_sha256)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
