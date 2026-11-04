#!/usr/bin/env python3
"""Trusted CPU worker: bounded recipes, development targets and target-free predictions."""
import argparse
import contextlib
import csv
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO/'examples'), str(REPO/'harness'), str(REPO/'selection')]
from bench.cases import CASES, FRESH_SEEDS, TUNED_SEED, digest
from bench.setup_data import write_json


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def features(rows, target):
    return [{key: (value if key == 'Sex' else float(value))
             for key, value in row.items() if key not in ('id', 'group', target)} for row in rows]


def losses(metric, target, prediction):
    import numpy as np
    error = np.asarray(target) - np.asarray(prediction)
    return np.abs(error) if metric == 'mae' else np.square(error)


def score(metric, target, prediction):
    import numpy as np
    loss = float(np.mean(losses(metric, target, prediction)))
    return -loss if metric == 'mae' else -float(np.sqrt(loss))


def fit(recipe, seed, rows, target, bootstrap=True):
    import numpy as np
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
    medium = recipe['strength'] == 'medium'
    family = recipe['family']
    if family == 'ridge':
        model = Ridge(alpha=10.0 if medium else 1.0)
    elif family == 'extra_trees':
        model = ExtraTreesRegressor(n_estimators=48 if medium else 24,
                                    max_depth=10 if medium else 6, min_samples_leaf=3,
                                    n_jobs=1, random_state=seed)
    elif family == 'hist_gradient_boosting':
        model = HistGradientBoostingRegressor(max_iter=48 if medium else 24,
                    max_leaf_nodes=15 if medium else 7, max_bins=31, learning_rate=.1,
                    early_stopping=False, random_state=seed)
    else:
        raise ValueError('unsupported model family')
    chosen = np.random.default_rng(seed).integers(0, len(rows), len(rows)) if bootstrap else range(len(rows))
    sampled = [rows[i] for i in chosen]
    pipeline = make_pipeline(DictVectorizer(sparse=False), StandardScaler(), model)
    pipeline.fit(features(sampled, target), [float(row[target]) for row in sampled])
    return pipeline


def toy(plan, output):
    import toy_demo
    import sweep
    from selection_gate import decide, write_receipt, verify_receipt
    root = output/'toy-lane'
    with (output/'preview.log').open('x') as log, contextlib.redirect_stdout(log):
        if toy_demo.main(['--root', str(root)]):
            raise ValueError('existing toy demo preview failed')
    lane = root/'lanes/toy'
    (lane/'runs').mkdir(exist_ok=True)
    (lane/'reports').mkdir(exist_ok=True)
    sequence = lane/'runs/sequence.json'
    write_json(sequence, plan['sequence'])
    paths = {}
    reports = dict(search='IDEA TOY-1: enumerate squares\nDONE\n',
                   read='No queued external ideas.\nDONE\n',
                   kill='RESULT TOY-1: SURVIVED 10/10 squares match the exact arithmetic pass line\nDONE\n')
    for stage, content in reports.items():
        paths[stage] = lane/('reports/'+stage+'.md')
        paths[stage].write_text(content)
    if json.loads(sequence.read_text()) != [i*i for i in range(10)]:
        raise ValueError('toy arithmetic mismatch')
    errors = sweep.check(paths, [], {'boards': ['TOY']}, known_ids=set())
    if errors:
        raise ValueError('; '.join(errors))
    # The existing toy explicitly uses exact-verifier repeats, not statistical holdouts.
    decision = decide([dict(name='squares', sealed_scores=[1., 1.])], 0., 1)
    import hashlib
    receipt = output/'toy-receipt.json'
    write_receipt(receipt, decision, hashlib.sha256(sequence.read_bytes()).hexdigest(), 'bench', 'TOY')
    verify_receipt(receipt, sequence)
    write_json(output/'result.json', dict(case='toy', metric='exact_match', local_validation_score=1.,
                hidden_test_score=None, hidden_score_reason='Exact arithmetic toy has no statistical hidden test.',
                n_submissions=1, selected='squares', gate='existing exact verifier (v1 receipt for both setups)',
                statistical_holdout=False, receipt_verified=True))


def train(case, data, recipes, output, setup='v1-default'):
    import numpy as np
    source = CASES[case]
    target, metric = source['target'], source['metric']
    train_rows, dev_rows = read_csv(data/'train.csv'), read_csv(data/'dev.csv')
    selection, hidden = read_csv(data/'selection.csv'), read_csv(data/'hidden.csv')
    available = train_rows + dev_rows
    y_dev = [float(r[target]) for r in dev_rows]
    groups = sorted({r['group'] for r in available}, key=lambda g: digest(['backtest', g]))
    blocks = [set(block.tolist()) for block in np.array_split(groups, 3)]
    backtests = []
    for cut in (1, 2):
        train_groups = set().union(*blocks[:cut])
        test_groups = blocks[cut]
        backtests.append(dict(
            train_units=sorted(train_groups), test_units=sorted(test_groups),
            train=[r for r in available if r['group'] in train_groups],
            test=[r for r in available if r['group'] in test_groups]))
    baseline = float(np.mean([float(r[target]) for r in available]))
    base_dev = float(np.mean([float(r[target]) for r in train_rows]))
    baseline_backtests = []
    for split in backtests:
        mean = float(np.mean([float(r[target]) for r in split['train']]))
        baseline_backtests.append(score(metric, [float(r[target]) for r in split['test']],
                                             [mean]*len(split['test'])))
    candidates, trials = [], []
    for recipe in recipes:
        name = recipe['family']
        tuned = fit(recipe, TUNED_SEED, train_rows, target)
        tuned_score = score(metric, y_dev, tuned.predict(features(dev_rows, target)))
        trials.append(dict(id=name, name=name, status='completed', tuned_score=tuned_score))
        selection_predictions, hidden_predictions = [], []
        for seed in FRESH_SEEDS:
            model = fit(recipe, seed, available, target)
            selection_predictions.append(model.predict(features(selection, target)).tolist())
            hidden_predictions.append(model.predict(features(hidden, target)).tolist())
        bt_scores = []
        if setup == 'v2-selection':
            for index, split in enumerate(backtests):
                model = fit(recipe, 201+index, split['train'], target)
                bt_scores.append(score(metric, [float(r[target]) for r in split['test']],
                                       model.predict(features(split['test'], target))))
        candidates.append(dict(name=name, recipe=recipe, tuned_score=tuned_score,
                    selection_predictions=selection_predictions,
                    hidden_prediction=np.mean(hidden_predictions, axis=0).tolist(),
                    backtest_scores=bt_scores))
    write_json(output/'predictions.json', dict(case=case, metric=metric, trials=trials,
        selection_ids=[r['id'] for r in selection], hidden_ids=[r['id'] for r in hidden],
        candidates=candidates, baseline=baseline, baseline_backtest_scores=baseline_backtests,
        baseline_validation_score=score(metric, y_dev, [base_dev]*len(dev_rows)),
        backtest_splits=[{k: value for k, value in s.items() if k.endswith('_units')} for s in backtests]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['toy']+list(CASES), required=True)
    parser.add_argument('--setup', choices=['v1-default', 'v2-selection'], default='v1-default')
    parser.add_argument('--data', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True)
    plan = json.loads(args.plan.read_text())
    if args.case == 'toy':
        toy(plan, args.output)
    else:
        train(args.case, args.data, plan['recipes'][args.case], args.output, args.setup)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
