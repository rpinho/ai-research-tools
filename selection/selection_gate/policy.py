"""Version-2 declaration and append-only evidence contracts (stdlib only)."""
import datetime as dt
import fcntl
import hashlib
import json
import math
import numbers
from pathlib import Path


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def integer(value, minimum, label):
    if type(value) is not int or value < minimum:
        raise ValueError('{} must be an integer >= {}'.format(label, minimum))
    return value


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError('{} must be finite'.format(label))
    return float(value)


def trial_records(trials):
    if not isinstance(trials, list):
        raise ValueError('trials must include every completed and pruned trial')
    ids, names = set(), set()
    for trial in trials:
        if not isinstance(trial, dict):
            raise ValueError('trial must be an object')
        for key, seen in (('id', ids), ('name', names)):
            if not nonempty(trial.get(key)) or trial[key] in seen:
                raise ValueError('trial {} must be unique and nonempty'.format(key))
            seen.add(trial[key])
        if trial.get('status') not in ('completed', 'pruned'):
            raise ValueError('trial status must be completed or pruned')
        if trial['status'] == 'completed' or 'tuned_score' in trial:
            finite(trial.get('tuned_score'), 'trial tuned_score')
    return trials


def validate_policy(policy):
    if not isinstance(policy, dict) or type(policy.get('policy_version')) is not int or policy['policy_version'] != 2:
        raise ValueError('selection policy requires policy_version: 2')
    if policy.get('split') not in ('group', 'time', 'source', 'random'):
        raise ValueError('split must be group|time|source|random')
    if not nonempty(policy.get('split_reason')) or '\n' in policy['split_reason'].strip():
        raise ValueError('split_reason requires a one-line reason matching the test')
    if policy['split'] == 'random' and (policy.get('test_shares_units') is not True or not nonempty(policy.get('test_shares_units_statement'))):
        raise ValueError('random split requires test_shares_units true and test_shares_units_statement')
    if policy.get('preprocessing_inside_folds') is not True:
        raise ValueError('preprocessing_inside_folds must be declared true')
    if type(policy.get('augmentation_used')) is not bool:
        raise ValueError('augmentation_used must be declared boolean')
    if policy['augmentation_used']:
        if policy.get('augmentation_train_only') is not True or policy.get('augmentation_label_preserving') is not True:
            raise ValueError('augmentation requires augmentation_train_only and augmentation_label_preserving true')
        transforms = policy.get('augmentation_transforms')
        if not isinstance(transforms, list) or not transforms or not all(nonempty(x) for x in transforms):
            raise ValueError('augmentation_transforms must name label-preserving transforms')
    if policy.get('final_fit') not in ('fold_ensemble', 'fixed_steps', 'retrain_epochs'):
        raise ValueError('final_fit must be fold_ensemble|fixed_steps|retrain_epochs')
    if policy['final_fit'] == 'retrain_epochs':
        backtest = policy.get('retrain_backtest')
        if not isinstance(backtest, dict):
            raise ValueError('retrain_epochs requires retrain_backtest')
        before, after = backtest.get('fold_scores'), backtest.get('retrain_scores')
        if not isinstance(before, list) or not isinstance(after, list) or len(before) < 2 or len(before) != len(after):
            raise ValueError('retrain_backtest requires paired fold_scores and retrain_scores on >= 2 splits')
        differences = [finite(a, 'retrain score') - finite(b, 'fold score') for a, b in zip(after, before)]
        # Conservative non-inferiority: no observed split may deteriorate.
        if min(differences) < 0:
            raise ValueError('retrain_backtest shows retraining hurt a split')
    minimum = integer(policy.get('min_measurements', 10), 2, 'min_measurements')
    waiver = policy.get('measurement_waiver')
    if waiver is not None and (not isinstance(waiver, dict) or not nonempty(waiver.get('reason'))):
        raise ValueError('measurement_waiver requires a reason')
    if minimum < 10 and waiver is None:
        raise ValueError('fewer than 10 measurements requires measurement_waiver with reason')
    integer(policy.get('n_trials_total'), 0, 'n_trials_total')
    trials = trial_records(policy.get('trials', []))
    if len(trials) != policy['n_trials_total']:
        raise ValueError('n_trials_total must count all frozen trials, pruned included')
    integer(policy.get('sealed_budget'), 1, 'sealed_budget')
    if not nonempty(policy.get('sealed_set_id')):
        raise ValueError('sealed_set_id must identify the fixed hidden set')
    integer(policy.get('submissions', 1), 1, 'submissions')
    if finite(policy.get('backtest_margin', 0), 'backtest_margin') < 0:
        raise ValueError('backtest_margin must be nonnegative')
    return policy


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _read(stream):
    rows, previous = [], ''
    for line in stream:
        if not line.strip():
            raise ValueError('ledger contains a blank record')
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError('ledger record must be an object')
        payload = {k: v for k, v in row.items() if k != 'sha256'}
        digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
        if row.get('previous_sha256') != previous or row.get('sha256') != digest:
            raise ValueError('ledger hash chain mismatch')
        previous = digest
        rows.append(row)
    return rows


def read_ledger(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return _read(stream)


def append_ledger(path, entry, validate=None):
    """One locked fsynced append; never replaces prior evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        rows = _read(stream)
        if validate:
            validate(rows)
        payload = dict(entry, previous_sha256=rows[-1]['sha256'] if rows else '',
                       at=dt.datetime.now(dt.timezone.utc).isoformat())
        payload['sha256'] = hashlib.sha256(canonical(payload).encode()).hexdigest()
        stream.write(canonical(payload) + '\n')
        stream.flush()
        import os
        os.fsync(stream.fileno())
    return payload


def consume_scoring(path, policy, competition, scoring_id):
    validate_policy(policy)
    if not nonempty(competition) or not nonempty(scoring_id):
        raise ValueError('competition and scoring_id are required')
    count = [0]
    def check(rows):
        if any(r.get('scoring_id') == scoring_id for r in rows):
            raise ValueError('scoring_id already consumed; every scoring requires a new id')
        if any(r.get('competition') == competition and r.get('sealed_set_id') != policy['sealed_set_id'] for r in rows):
            raise ValueError('hidden set changed within competition')
        if any(r.get('budget') != policy['sealed_budget'] for r in rows):
            raise ValueError('sealed scoring budget changed within lane ledger')
        count[0] = len(rows) + 1
    entry = append_ledger(path, dict(kind='sealed_scoring', competition=competition,
        scoring_id=scoring_id, sealed_set_id=policy['sealed_set_id'], budget=policy['sealed_budget']), check)
    if count[0] > policy['sealed_budget']:
        raise ValueError('sealed scoring budget exceeded: {} > {}'.format(count[0], policy['sealed_budget']))
    return entry


def scoring_ledger_error(path, budget):
    try:
        integer(budget, 1, 'sealed_budget')
        rows = read_ledger(path)
        if len(rows) > budget:
            return 'sealed scoring budget exceeded: {} > {}'.format(len(rows), budget)
        ids, sets = set(), {}
        for row in rows:
            if row.get('kind') != 'sealed_scoring' or not nonempty(row.get('scoring_id')) or row['scoring_id'] in ids:
                return 'invalid or repeated sealed scoring record'
            if row.get('budget') != budget:
                return 'sealed scoring budget changed within lane ledger'
            ids.add(row['scoring_id'])
            key = row.get('competition')
            if not nonempty(key) or not nonempty(row.get('sealed_set_id')):
                return 'sealed scoring record lacks competition or fixed hidden set'
            if key in sets and sets[key] != row['sealed_set_id']:
                return 'hidden set changed within competition'
            sets[key] = row['sealed_set_id']
        return ''
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return 'sealed scoring ledger invalid: ' + str(exc)


def _optimism_from_rows(rows, competition):
    groups = {}
    for row in rows:
        if row.get('kind') != 'optimism' or not nonempty(row.get('competition')):
            raise ValueError('invalid optimism record')
        drops = row.get('drops')
        if not isinstance(drops, list) or not drops:
            raise ValueError('optimism record requires per-finalist drops')
        values = [finite(x, 'optimism drop') for x in drops]
        drop = finite(row.get('drop'), 'optimism drop')
        if drop != sum(values)/len(values):
            raise ValueError('optimism drop disagrees with per-finalist drops')
        if row['competition'] != competition:
            groups.setdefault(row['competition'], []).append(drop)
    means = [sum(values)/len(values) for values in groups.values()]
    return max(0.0, sum(means)/len(means)) if means else 0.0


def optimism_prior(path, competition, *, before_sha256=None, through_sha256=None):
    """Equal weight per previous competition, optionally at a frozen prefix."""
    rows = read_ledger(path)
    if before_sha256 is not None and through_sha256 is not None:
        raise ValueError('choose before or through optimism anchor')
    if through_sha256 == '':
        rows = []
    elif before_sha256 is not None or through_sha256 is not None:
        anchor = before_sha256 if before_sha256 is not None else through_sha256
        indices = [i for i, row in enumerate(rows) if row['sha256'] == anchor]
        if not indices:
            raise ValueError('optimism prior anchor missing')
        rows = rows[:indices[0] + (1 if through_sha256 is not None else 0)]
    return _optimism_from_rows(rows, competition)


def optimism_snapshot(path, competition):
    """Read a consistent measured prior and its head before doing numeric work."""
    rows = read_ledger(path)
    return _optimism_from_rows(rows, competition), rows[-1]['sha256'] if rows else ''


def record_optimism(path, competition, scoring_id, drops, *, prior_sha256=None):
    if not drops:
        raise ValueError('optimism requires tuned-to-fresh drops')
    values = [finite(x, 'optimism drop') for x in drops]
    def check(rows):
        if any(r.get('scoring_id') == scoring_id for r in rows):
            raise ValueError('optimism scoring_id already recorded')
        if prior_sha256 is not None and prior_sha256 != '' and not any(r['sha256'] == prior_sha256 for r in rows):
            raise ValueError('optimism prior anchor missing')
    return append_ledger(path, dict(kind='optimism', competition=competition,
        scoring_id=scoring_id, drops=values, drop=sum(values)/len(values), prior_sha256=prior_sha256), check)
