"""Opt-in bridge to the shared gate; legacy harness paths stay stdlib-only."""
import importlib
import json
import os
from pathlib import Path
import sys


def gate():
    location = Path(os.environ.get('RH_SELECTION_GATE', str(Path(__file__).resolve().parents[1]/'selection'))).expanduser()
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))
    try:
        return importlib.import_module('selection_gate')
    except ImportError as exc:
        raise ValueError('v2 requires the shared selection_gate package and numpy; set RH_SELECTION_GATE to its root') from exc


def contracts():
    gate()
    return importlib.import_module('selection_gate.policy')


def validate(policy):
    return contracts().validate_policy(policy)


def check_config(row, parameters):
    """Instrumented v2 configs must report augmentation, even when disabled."""
    if row.get('policy_version') != 2:
        return
    if type(parameters.get('augmentation_used')) is not bool:
        raise ValueError('v2 configuration requires augmentation_used boolean')
    used = parameters['augmentation_used'] or any(bool(parameters.get(k)) for k in
        ('augmentation', 'augmentations', 'augmentation_transforms'))
    if used and not row['selection_policy']['augmentation_used']:
        raise ValueError('augmentation used without the frozen training-only label-preserving declaration')


def add_trial(row, trial):
    if row.get('policy_version') != 2 or row.get('status') != 'testing':
        raise ValueError('selection trial requires a frozen v2 testing idea')
    from idea_policy import frozen_error
    error = frozen_error(row)
    if error:
        raise ValueError(error)
    records = list(row.get('trial_records', [])) + [trial]
    contracts().trial_records(records)
    row.update(trial_records=records, n_trials_total=len(records))
    return row


def score(row, lane, evidence_path, output, cfg):
    from idea_policy import file_hash, frozen_error
    if row.get('policy_version') != 2 or row.get('status') != 'testing':
        raise ValueError('selection score requires a frozen v2 testing idea')
    error = frozen_error(row)
    if error:
        raise ValueError(error)
    policy = row['selection_policy']
    lane_policy(policy, cfg)
    evidence_path, evidence_sha = file_hash(evidence_path)
    data = json.loads(Path(evidence_path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get('evidence'), dict):
        raise ValueError('scoring input requires an evidence object')
    if data.get('evidence', {}).get('trials') != row.get('trial_records') or data.get('n_candidates') != row.get('n_trials_total'):
        raise ValueError('scoring evidence must match every recorded trial, pruned included')
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError('decision output already exists; evidence cannot be replaced')
    # CLI prediction contributions are additive unit metrics; arbitrary metrics
    # use the Python ensemble_scorer API to score averaged predictions correctly.
    ensemble = data.get('simple_average_ensemble', False)
    if type(ensemble) is not bool:
        raise ValueError('simple_average_ensemble must be boolean; fitted weights forbidden')
    if any(k in data for k in ('weights', 'ensemble_weights')):
        raise ValueError('fitted ensemble weights forbidden')
    decision = gate().decide(data['finalists'], data['floor'], data['n_candidates'],
        data.get('margin_sd', 2), policy_version=2, policy=policy, evidence=data['evidence'],
        ledger_path=lane/'SEALED_SCORING.jsonl', optimism_path=lane/'OPTIMISM.jsonl',
        ensemble_scorer=(lambda prediction: prediction) if ensemble else None)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(decision, indent=2, allow_nan=False)+'\n')
    _, output_sha = file_hash(output)
    record = dict(input_path=evidence_path, input_sha256=evidence_sha,
        decision_path=str(output), decision_sha256=output_sha, n_trials_total=row['n_trials_total'],
        scoring_id=decision['scoring_id'], scoring_ledger_sha256=decision['scoring_ledger_sha256'],
        optimism_ledger_sha256=decision['optimism_ledger_sha256'])
    row['selection_records'] = list(row.get('selection_records', [])) + [record]
    return decision


def lane_policy(policy, cfg):
    validate(policy)
    for config_key, policy_key, default in (
        ('selection_min_measurements', 'min_measurements', 10),
        ('selection_sealed_budget', 'sealed_budget', None),
        ('selection_submissions', 'submissions', 1)):
        if config_key in cfg and policy.get(policy_key, default) != cfg[config_key]:
            raise ValueError('{} must match frozen {}'.format(config_key, policy_key))


def audit(history, lane, cfg=None):
    """Check receipts, monotone search, artifacts, replay, and anchored ledgers."""
    cfg = cfg or {}
    v2 = [row for row in history if row.get('policy_version') == 2]
    if not v2:
        return []
    errors, previous, policies = [], {}, []
    from idea_policy import file_hash, frozen_error
    for row in history:
        key = row['id']
        before = previous.get(key)
        if before and before.get('policy_version') == 2 and row.get('policy_version') != 2:
            errors.append('idea {}: v2 policy removed or downgraded'.format(key))
        if row.get('policy_version') != 2:
            previous[key] = row
            continue
        label = 'idea {}: '.format(key)
        try:
            error = frozen_error(row)
            if error:
                raise ValueError(error)
            policy = row.get('selection_policy')
            lane_policy(policy, cfg)
            policies.append(policy)
            trials = contracts().trial_records(row.get('trial_records'))
            contracts().integer(row.get('n_trials_total'), 0, 'n_trials_total')
            if row.get('n_trials_total') != len(trials):
                raise ValueError('n_trials_total must count every recorded trial, pruned included')
            initial = policy['n_trials_total']
            if trials[:initial] != policy.get('trials', []):
                raise ValueError('original frozen trials replaced')
            if before and before.get('policy_version') == 2:
                old = before.get('trial_records', [])
                if trials[:len(old)] != old:
                    raise ValueError('trial history removed or replaced')
                old_records = before.get('selection_records', [])
                if row.get('selection_records', [])[:len(old_records)] != old_records:
                    raise ValueError('selection decision history removed or replaced')
            for attempt in row.get('attempt_records', []):
                check_config(row, json.loads(Path(attempt['config_path']).read_text()))
            records = row.get('selection_records', [])
            if row.get('status') == 'shipped' and not records:
                raise ValueError('v2 shipped idea requires an accepted selection decision')
            if not isinstance(records, list):
                raise ValueError('selection_records must be a list')
            scoring = {r['sha256']: r for r in contracts().read_ledger(lane/'SEALED_SCORING.jsonl')}
            optimism = {r['sha256']: r for r in contracts().read_ledger(lane/'OPTIMISM.jsonl')}
            for record in records:
                for field, sha in (('input_path', 'input_sha256'), ('decision_path', 'decision_sha256')):
                    if file_hash(record[field])[1] != record[sha]:
                        raise ValueError('selection {} hash mismatch'.format(field))
                data = json.loads(Path(record['input_path']).read_text())
                decision = json.loads(Path(record['decision_path']).read_text())
                n = record['n_trials_total']
                if data['evidence']['trials'] != trials[:n] or data['n_candidates'] != n:
                    raise ValueError('selection evidence differs from trial history')
                if decision['scoring_id'] != record['scoring_id'] or data['evidence']['scoring_id'] != record['scoring_id']:
                    raise ValueError('selection scoring_id mismatch')
                sealed = scoring.get(record['scoring_ledger_sha256'])
                opt = optimism.get(record['optimism_ledger_sha256'])
                if not sealed or not opt:
                    raise ValueError('selection ledger anchor missing (history truncated or replaced)')
                if any(r.get('scoring_id') != record['scoring_id'] for r in (sealed, opt)):
                    raise ValueError('selection ledger anchor belongs to another score')
                if sealed['sealed_set_id'] != policy['sealed_set_id'] or any(r.get('competition') != data['evidence']['competition'] for r in (sealed, opt)):
                    raise ValueError('selection hidden-set binding mismatch')
                anchor = opt.get('prior_sha256')
                if not isinstance(anchor, str) or decision.get('optimism_prior_sha256') != anchor:
                    raise ValueError('selection optimism prior anchor mismatch')
                prior = contracts().optimism_prior(lane/'OPTIMISM.jsonl', data['evidence']['competition'],
                                                    through_sha256=anchor)
                if decision.get('optimism_prior') != prior:
                    raise ValueError('selection optimism prior differs from measured history')
                from selection_gate.v2 import evaluate_v2
                replay = evaluate_v2(data['finalists'], data['floor'], n, data.get('margin_sd', 2),
                    policy, data['evidence'], prior=prior,
                    ensemble_scorer=(lambda prediction: prediction) if data.get('simple_average_ensemble') else None)
                if row.get('status') == 'shipped' and record is records[-1] and not replay['accepted']:
                    raise ValueError('v2 shipped idea requires an accepted selection decision')
                complete_replay = dict(replay, scoring_id=record['scoring_id'],
                    competition=data['evidence']['competition'], optimism_prior_sha256=anchor,
                    scoring_ledger_sha256=sealed['sha256'], optimism_ledger_sha256=opt['sha256'])
                if decision != complete_replay:
                    raise ValueError('selection decision replay mismatch')
                expected_drops = [r['optimism_drop'] for r in replay['finalists'] if 'optimism_drop' in r]
                if opt.get('drops') != expected_drops or opt.get('drop') != replay['optimism_estimate']:
                    raise ValueError('optimism ledger differs from fresh-score drop')
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            errors.append(label + str(exc))
        previous[key] = row
    if policies:
        budgets = {p['sealed_budget'] for p in policies}
        if len(budgets) != 1:
            errors.append('v2 sealed_budget must be consistent across the lane')
        error = contracts().scoring_ledger_error(lane/'SEALED_SCORING.jsonl', min(budgets))
        if error:
            errors.append(error)
        try:
            contracts().optimism_prior(lane/'OPTIMISM.jsonl', '')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append('optimism ledger invalid: ' + str(exc))
    return list(dict.fromkeys(errors))
