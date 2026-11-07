"""Opt-in selection with independent-unit uncertainty and fresh-search evidence."""
import math
import numpy as np
from .policy import (validate_policy, trial_records, finite, integer, nonempty,
                     consume_scoring, optimism_snapshot, record_optimism)


def _scores(values, label, minimum=2):
    try:
        scores = np.asarray(values, dtype=float)
    except (ValueError, TypeError):
        raise ValueError(label + ' requires finite scores')
    if scores.ndim != 1 or len(scores) < minimum or not np.isfinite(scores).all():
        raise ValueError(label + ' requires >= {} finite scores'.format(minimum))
    return scores


# Two-sided 95% Student t quantiles by degrees of freedom. 1.96 is the df = infinity case, so
# using it with ten measurements understates the interval by about 14%, and with three by more
# than twice. Hugo Fernandes, 2026-11-07: use the t quantile when seeds are few. Values beyond
# the table are interpolated; past df 120 the normal quantile is within 1%.
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
       17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
       25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 50: 2.009,
       60: 2.000, 80: 1.990, 100: 1.984, 120: 1.980}
NORMAL95 = 1.96


def t95(n):
    """The two-sided 95% t multiplier for n measurements (df = n-1). Never below 1.96."""
    n = integer(n, 2, 'measurements for a t interval')
    df = n - 1
    if df in T95:
        return T95[df]
    keys = sorted(T95)
    if df > keys[-1]:
        return NORMAL95
    low = max(k for k in keys if k < df)
    high = min(k for k in keys if k > df)
    return T95[low] + (T95[high]-T95[low]) * (df-low) / (high-low)


def paired_diff(candidate, reference, n=None):
    """Paired t interval on matched measurements: the same seeds, the same windows.

    Why paired (Hugo Fernandes, 2026-11-07): a seed or an evaluation window moves both
    candidates together, so differencing on matched ids cancels it. His own runs came out 3 to
    10 times narrower this way, and the noise that mattered most was the evaluation windows
    rather than the seeds. An unpaired comparison pays for that shared noise twice.
    """
    candidate = _scores(candidate, 'paired candidate scores')
    reference = _scores(reference, 'paired reference scores')
    if len(candidate) != len(reference):
        raise ValueError('a paired comparison requires the same measurements for both')
    deltas = candidate - reference
    k = len(deltas)
    sd = float(deltas.std(ddof=1))
    sem = sd / math.sqrt(k)
    q = t95(k)
    mean = float(deltas.mean())
    unpaired = math.sqrt(float(candidate.var(ddof=1))/k + float(reference.var(ddof=1))/k)
    return dict(difference=mean, sd=sd, sem=sem, quantile=q,
                ci_low=mean - q*sem, ci_high=mean + q*sem, n_measurements=k,
                unpaired_sem=unpaired,
                narrowing=float(unpaired/sem) if sem > 0 else None)


def bootstrap_diff(candidate, baseline, units, n=2000, *, seed=0):
    """Paired cluster bootstrap, equal weight per independent unit, never rows.

    Repeated unit labels are allowed: first average paired row differences within
    each unit, then resample those unit means. Higher scores are always better.
    """
    integer(n, 1, 'n')
    candidate = _scores(candidate, 'candidate')
    baseline = _scores(baseline, 'baseline')
    if not isinstance(units, (list, tuple, np.ndarray)) or len(candidate) != len(baseline) or len(candidate) != len(units):
        raise ValueError('candidate, baseline and units must be paired and equally long')
    groups = {}
    for unit, delta in zip(units, candidate-baseline):
        if isinstance(unit, (bool, np.bool_)):
            raise ValueError('units must name independent subjects, groups or stages')
        if isinstance(unit, (str, np.str_)):
            unit = str(unit)
        elif isinstance(unit, (int, np.integer)):
            unit = int(unit)
        else:
            raise ValueError('units must name independent subjects, groups or stages')
        if isinstance(unit, str) and not unit.strip():
            raise ValueError('units must name independent subjects, groups or stages')
        groups.setdefault((type(unit).__name__, unit), []).append(float(delta))
    if len(groups) < 2:
        raise ValueError('bootstrap requires at least two independent units')
    values = np.asarray([np.mean(rows) for rows in groups.values()])
    rng = np.random.default_rng(seed)
    # Bounded memory even for large unit sets.
    draws = np.empty(n)
    for start in range(0, n, 128):
        stop = min(n, start+128)
        draws[start:stop] = values[rng.integers(0, len(values), size=(stop-start, len(values)))].mean(axis=1)
    low, high = np.quantile(draws, [.025, .975])
    return dict(difference=float(values.mean()), ci_low=float(low), ci_high=float(high),
                confidence=.95, n_units=len(values), n_resamples=n)


def backtest_summary(candidate, baseline, margin=0):
    candidate = _scores(candidate, 'backtest candidate')
    baseline = _scores(baseline, 'backtest baseline')
    margin = finite(margin, 'backtest margin')
    if margin < 0 or len(candidate) != len(baseline):
        raise ValueError('backtests require paired splits and a nonnegative margin')
    differences = candidate-baseline
    wins = int(np.sum(differences > margin))
    worst = int(np.argmin(differences))
    return dict(passed=wins > len(candidate)/2, wins=wins, n_splits=len(candidate),
                margin=margin, differences=differences.tolist(), worst_split=worst,
                worst_difference=float(differences[worst]),
                worst_score=float(candidate[worst]), worst_baseline=float(baseline[worst]))


def validate_backtest_splits(splits):
    """Reject overlapping tests, time leakage and sliding/shrinking training sets."""
    if not isinstance(splits, list) or len(splits) < 2:
        raise ValueError('backtest_splits requires >= 2 expanding-window splits')
    tested, previous = set(), None
    for split in splits:
        if not isinstance(split, dict):
            raise ValueError('backtest split must be an object')
        train, test = split.get('train_units'), split.get('test_units')
        if not isinstance(train, list) or not isinstance(test, list) or not train or not test:
            raise ValueError('backtest split requires train_units and test_units')
        for units in (train, test):
            if any(type(u) not in (str, int) or (isinstance(u, str) and not u.strip()) for u in units):
                raise ValueError('backtest units must be nonempty ids')
            if len(set(units)) != len(units):
                raise ValueError('backtest units must be unique within a split')
        train, test = set(train), set(test)
        if train & test or tested & test:
            raise ValueError('backtest training and held-out tests must be disjoint')
        if previous is not None and not previous.issubset(train):
            raise ValueError('backtest training must expand to include previous train and test units')
        # No future test unit may have appeared in any earlier training window.
        if previous is not None and previous & test:
            raise ValueError('backtest future test leaked into an earlier window')
        previous = train | test
        tested |= test
    return splits


def backtests(candidates, windows, score, *, baseline, min_train=1, test_size=1, margin=0):
    """Score expanding windows, fitting each candidate afresh inside `score`.

    `windows` is an ordered sequence of nonempty disjoint independent-unit lists.
    `score(candidate, train_units, test_units)` must fit preprocessing and training
    only on train_units and return a scalar metric on test_units. Tail windows
    must fill test_size; no silently discarded validation units.
    """
    integer(min_train, 1, 'min_train')
    integer(test_size, 1, 'test_size')
    if not isinstance(candidates, dict) or baseline not in candidates or len(candidates) < 2:
        raise ValueError('candidates must include a named baseline and a challenger')
    groups = [list(window) for window in windows]
    seen = set()
    for group in groups:
        if not group:
            raise ValueError('backtest windows must be nonempty')
        for unit in group:
            if type(unit) not in (str, int) or unit in seen:
                raise ValueError('backtest windows must have disjoint independent units')
            seen.add(unit)
    if len(groups)-min_train < 2*test_size or (len(groups)-min_train) % test_size:
        raise ValueError('backtests require >= 2 complete expanding-window test splits')
    splits, scores = [], {name: [] for name in candidates}
    for cut in range(min_train, len(groups), test_size):
        train = [u for group in groups[:cut] for u in group]
        test = [u for group in groups[cut:cut+test_size] for u in group]
        splits.append(dict(train_units=train, test_units=test))
        for name, candidate in candidates.items():
            scores[name].append(finite(score(candidate, list(train), list(test)), 'backtest score'))
    validate_backtest_splits(splits)
    summaries = {name: backtest_summary(values, scores[baseline], margin)
                 for name, values in scores.items() if name != baseline}
    return dict(splits=splits, scores=scores, baseline=baseline, summaries=summaries)


def forecast_diversity(members, labels=None):
    """Diversity from the forecasts themselves: error correlation, or plain disagreement.

    Why not subgroup performance (Hugo Fernandes, 2026-11-07): slicing by subgroup and
    comparing scores reuses the labels, so the diversity number inherits the same selection
    problem the gate exists to contain, and it is measured on exactly the data you are trying
    not to fit. Forecast disagreement needs no labels at all. With labels, the quantity that
    predicts ensemble gain is the correlation of the members' errors, not of their predictions.

    `members` maps a name to a flat sequence of forecasts, all the same length. Returns mean
    pairwise error correlation (with labels) and mean pairwise disagreement (always), where
    disagreement is the SD across members per item, averaged over items and scaled by the
    spread of the forecasts, so it is comparable between problems.
    """
    if not isinstance(members, dict) or len(members) < 2:
        raise ValueError('diversity needs the forecasts of at least two members')
    names = sorted(members)
    stacked = []
    for name in names:
        values = np.asarray(members[name], dtype=float).ravel()
        if len(values) < 2 or not np.isfinite(values).all():
            raise ValueError('member ' + str(name) + ' needs finite forecasts')
        if stacked and len(values) != len(stacked[0]):
            raise ValueError('members must forecast the same items in the same order')
        stacked.append(values)
    matrix = np.stack(stacked)
    spread = float(matrix.std())
    per_item = matrix.std(axis=0, ddof=1)
    result = dict(members=names, n_items=int(matrix.shape[1]),
                  disagreement=float(per_item.mean()),
                  disagreement_scaled=float(per_item.mean()/spread) if spread > 0 else 0.0,
                  error_correlation=None, source='forecasts')
    if labels is None:
        return result
    truth = np.asarray(labels, dtype=float).ravel()
    if len(truth) != matrix.shape[1] or not np.isfinite(truth).all():
        raise ValueError('labels must be finite and match the forecast items')
    errors = matrix - truth
    pairs = []
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            a, b = errors[i], errors[j]
            if a.std() == 0 or b.std() == 0:
                continue
            pairs.append(float(np.corrcoef(a, b)[0, 1]))
    result['error_correlation'] = float(np.mean(pairs)) if pairs else None
    result['error_correlation_pairs'] = len(pairs)
    return result


def simple_average_ensemble(finalists, scorer, labels=None):
    """Average predictions before scoring; no fitted weights or metric averaging.

    scorer(prediction) returns either additive per-row metric contributions or
    {"score": official_scalar, "unit_scores": paired_row_contributions}. The
    latter supports nonlinear official metrics. Backtest windows may have
    different sizes; candidates must align within each window.
    """
    if len(finalists) < 2 or not callable(scorer):
        raise ValueError('simple-average ensemble requires finalists and a scorer')
    result = dict(name='simple_average', measurement_ids=finalists[0]['measurement_ids'],
                  ensemble_method='simple_average', members=[c['name'] for c in finalists],
                  weights_fixed_before_scores=True)
    for source, destination in (('sealed_predictions', 'sealed_scores'), ('backtest_predictions', 'backtest_scores')):
        predictions = []
        for candidate in finalists:
            if 'weights' in candidate or 'ensemble_weights' in candidate:
                raise ValueError('fitted ensemble weights are forbidden')
            values = candidate.get(source)
            if not isinstance(values, (list, tuple, np.ndarray)) or len(values) < 2:
                raise ValueError('ensemble requires finite aligned ' + source)
            if predictions and len(values) != len(predictions[0]):
                raise ValueError('ensemble predictions have different shapes')
            predictions.append(values)
        metrics, contributions = [], []
        for index in range(len(predictions[0])):
            arrays = [np.asarray(values[index], dtype=float) for values in predictions]
            if any(a.ndim < 1 or not np.isfinite(a).all() for a in arrays):
                raise ValueError('ensemble predictions must be aligned by measurement and unit')
            if any(a.shape != arrays[0].shape for a in arrays):
                raise ValueError('ensemble predictions have different shapes')
            measured = scorer(np.mean(np.stack(arrays), axis=0))
            if isinstance(measured, dict):
                metric = finite(measured.get('score'), 'ensemble official score')
                units = _scores(measured.get('unit_scores'), 'ensemble per-unit scores', 1)
            else:
                units = _scores(measured, 'ensemble per-unit scores', 1)
                metric = float(units.mean())
            metrics.append(metric)
            contributions.append(units)
        result[destination] = metrics
        if source == 'sealed_predictions':
            if any(len(x) != len(contributions[0]) for x in contributions):
                raise ValueError('ensemble scorer returned unpaired sealed units')
            result['unit_scores'] = np.mean(contributions, axis=0).tolist()
    # Equal weights, and the diversity that justifies averaging measured on the members' own
    # forecasts. It is reported, not a gate: the honest use is to see whether an ensemble of
    # near-identical models was worth its compute, and refusing on a threshold would be a
    # number nobody has calibrated.
    result['diversity'] = forecast_diversity(
        {c['name']: np.concatenate([np.asarray(v, dtype=float).ravel()
                                    for v in c['sealed_predictions']]) for c in finalists},
        labels=labels)
    return result


def _ids(values, label):
    if not isinstance(values, list) or not values or not all(nonempty(x) for x in values) or len(set(values)) != len(values):
        raise ValueError(label + ' requires unique nonempty ids')
    return values


def evaluate_v2(finalists, floor, n_candidates, margin_sd, policy, evidence, *, prior=0, ensemble_scorer=None):
    """Pure evaluation, also used by offline lint to reproduce saved decisions."""
    from . import optimism
    validate_policy(policy)
    floor = finite(floor, 'floor')
    if finite(margin_sd, 'margin_sd') != 2:
        raise ValueError('v2 requires the existing 2 SEM margin')
    prior = finite(prior, 'optimism prior')
    if prior < 0:
        raise ValueError('optimism prior must be nonnegative')
    if not isinstance(evidence, dict):
        raise ValueError('v2 requires fresh rerun and independent-unit evidence')
    if any(key in evidence for key in ('weights', 'ensemble_weights', 'ensemble_method')):
        raise ValueError('fitted weights and caller-supplied ensembles are forbidden')
    # Diversity is measured here from the member forecasts, never declared, and never argued
    # from subgroup scores: slicing by subgroup to show models differ reuses the labels.
    if any(key in evidence for key in ('diversity', 'subgroup_scores', 'diversity_by_subgroup')):
        raise ValueError('diversity is measured from the member forecasts, not declared, and '
                         'not argued from subgroup performance, which reuses the labels')
    trials = trial_records(evidence.get('trials'))
    integer(n_candidates, 1, 'n_candidates')
    if n_candidates != len(trials) or n_candidates < policy['n_trials_total']:
        raise ValueError('n_candidates must equal n_trials_total including pruned trials')
    if trials[:policy['n_trials_total']] != policy.get('trials', []):
        raise ValueError('frozen trial history changed')
    if not isinstance(finalists, list) or any(not isinstance(c, dict) for c in finalists):
        raise ValueError('finalists must be a list of candidate objects')
    if not 3 <= len(finalists) <= 5:
        raise ValueError('v2 requires the best 3-5 finalists rerun fresh')
    tuned = sorted((t for t in trials if t['status'] == 'completed'), key=lambda t: -t['tuned_score'])
    names = [c.get('name') for c in finalists]
    if len(set(names)) != len(names) or set(names) != {t['name'] for t in tuned[:len(finalists)]}:
        raise ValueError('finalists must be the best 3-5 completed tuned trials')
    if 'floor' in names or 'simple_average' in names:
        raise ValueError('floor and simple_average are reserved candidate names')
    tuned_by_name = {t['name']: t['tuned_score'] for t in tuned}
    fresh = evidence.get('fresh')
    if not isinstance(fresh, dict) or fresh.get('nobody_tuned_against') is not True or fresh.get('independent_measurements') is not True:
        raise ValueError('fresh reruns require nobody_tuned_against and independent_measurements true')
    measurement_ids = _ids(fresh.get('measurement_ids'), 'fresh measurement_ids')
    seeds = _ids(fresh.get('seed_ids'), 'fresh seed_ids')
    splits = _ids(fresh.get('split_ids'), 'fresh split_ids')
    tuned_seeds = _ids(evidence.get('tuned_seed_ids'), 'tuned seed_ids')
    tuned_splits = _ids(evidence.get('tuned_split_ids'), 'tuned split_ids')
    if set(seeds) & set(tuned_seeds) or set(splits) & set(tuned_splits):
        raise ValueError('fresh seeds and splits must be disjoint from tuned seeds and splits')
    kind = fresh.get('measurement_kind')
    if kind not in ('seeds', 'backtests'):
        raise ValueError('measurement_kind must be seeds or backtests')
    if len(measurement_ids) != len(seeds if kind == 'seeds' else splits):
        raise ValueError('each measurement requires a distinct seed or independent backtest split')
    minimum = policy.get('min_measurements', 10)
    if len(measurement_ids) < minimum:
        raise ValueError('at least {} independent measurements per finalist required'.format(minimum))
    baseline = _scores(evidence.get('baseline_unit_scores'), 'baseline unit scores')
    units = evidence.get('units')
    if not isinstance(units, list) or len(units) != len(baseline):
        raise ValueError('units must identify the paired baseline observations')
    backtest_baseline = _scores(evidence.get('baseline_backtest_scores'), 'baseline backtests')
    backtest_ids = _ids(evidence.get('backtest_split_ids'), 'backtest split ids')
    split_manifest = validate_backtest_splits(evidence.get('backtest_splits'))
    if len(split_manifest) != len(backtest_ids):
        raise ValueError('backtest_splits must match the fresh split ids')
    if len(backtest_ids) != len(backtest_baseline) or not set(backtest_ids).issubset(splits):
        raise ValueError('backtests must use the same fresh split schedule')
    # Baseline must be evaluated on the identical fresh measurement schedule.
    if evidence.get('baseline_measurement_ids') != measurement_ids:
        raise ValueError('baseline must use the same sealed measurements')
    # Per-measurement baseline scores, when supplied, buy the paired comparison: every
    # candidate must beat the baseline on the matched seeds and windows, not only on average.
    baseline_sealed = evidence.get('baseline_sealed_scores')
    if baseline_sealed is not None:
        baseline_sealed = _scores(baseline_sealed, 'baseline sealed scores', minimum)
        if len(baseline_sealed) != len(measurement_ids):
            raise ValueError('baseline sealed scores must match the sealed measurement ids')
    all_finalists = list(finalists)
    for candidate in finalists:
        if any(k in candidate for k in ('weights', 'ensemble_weights', 'ensemble_method', 'members')):
            raise ValueError('fitted weights and caller-supplied ensembles are forbidden')
    if ensemble_scorer is not None:
        all_finalists.append(simple_average_ensemble(finalists, ensemble_scorer,
                                                     labels=evidence.get('sealed_labels')))
    k = len(all_finalists)
    rows = []
    for candidate in all_finalists:
        name = candidate['name']
        if candidate.get('measurement_ids') != measurement_ids:
            raise ValueError('all finalists must use the same sealed measurement ids')
        scores = _scores(candidate.get('sealed_scores'), 'sealed_scores', minimum)
        if len(scores) != len(measurement_ids):
            raise ValueError('sealed scores must match independent measurement ids')
        bt = backtest_summary(candidate.get('backtest_scores'), backtest_baseline, policy.get('backtest_margin', 0))
        if len(bt['differences']) != len(backtest_ids):
            raise ValueError('candidate backtests must match fresh split ids')
        bt['worst_split_id'] = backtest_ids[bt['worst_split']]
        if kind == 'backtests' and (backtest_ids != measurement_ids or not np.array_equal(scores, candidate['backtest_scores'])):
            raise ValueError('backtest measurements must be the declared per-split scores')
        ci = bootstrap_diff(candidate.get('unit_scores'), baseline, units)
        sd = float(scores.std(ddof=1))
        mean, sem = float(scores.mean()), sd/math.sqrt(len(scores))
        bias, search_bias = optimism(sd, k), optimism(sd, n_candidates)
        # The t quantile, not 1.96: with ten measurements 1.96 understates the interval, and a
        # measurement waiver can take a lane to three.
        quantile = t95(len(scores))
        lower = mean-bias-quantile*sem
        paired = paired_diff(scores, baseline_sealed) if baseline_sealed is not None else None
        accepted = (lower > floor and ci['ci_low'] > 0 and bt['passed']
                    and (paired is None or paired['ci_low'] > 0))
        row = dict(name=name, sealed_scores=scores.tolist(), n_measurements=len(scores), n_seeds=len(seeds), measurement_kind=kind, mean=mean, sd=sd, sem=sem,
                   optimism=bias, search_optimism=search_bias, margin=quantile*sem,
                   margin_quantile=quantile, lower_bound=lower,
                   expected=mean-max(search_bias, prior), bootstrap=ci, backtests=bt,
                   paired_baseline=paired,
                   submission_score=mean-quantile*sem, accepted=bool(accepted))
        if name in tuned_by_name:
            row.update(tuned_score=tuned_by_name[name], optimism_drop=tuned_by_name[name]-mean)
        else:
            row.update(ensemble_method='simple_average', members=candidate['members'],
                       weights_fixed_before_scores=True, diversity=candidate['diversity'])
        rows.append(row)
    eligible = sorted((r for r in rows if r['accepted']), key=lambda r: -r['submission_score'])
    selected = [r['name'] for r in eligible[:policy.get('submissions', 1)]]
    best = max(rows, key=lambda r: r['mean'])
    # Candidates are compared to the leader on the matched measurements they all share, which
    # is the comparison that answers "is this ranking real?". It needs no extra evidence,
    # because every finalist is already required to use the same measurement ids. Recorded,
    # not an acceptance condition: a ranking too close to call is still a real result, and the
    # honest report is the interval, not a refusal.
    leader = next(r for r in rows if r['name'] == best['name'])
    for row in rows:
        row['paired_vs_best'] = (None if row['name'] == leader['name'] else
                                 paired_diff(row['sealed_scores'], leader['sealed_scores']))
    runner_up = max((r for r in rows if r['name'] != leader['name']),
                    key=lambda r: r['mean'], default=None)
    separated = bool(runner_up and runner_up['paired_vs_best']['ci_high'] < 0)
    return dict(policy_version=2, floor=floor, n_candidates=n_candidates, n_trials_total=n_candidates,
                n_finalists=k, margin_sd=2.0, interval='t95', finalists=rows, best=best['name'],
                best_separated_from_runner_up=separated,
                accepted=bool(selected), selected=selected[0] if selected else 'floor',
                selected_submissions=selected, submissions=policy.get('submissions', 1),
                expected=best['expected'], search_optimism=best['search_optimism'], optimism_prior=prior,
                optimism_estimate=float(np.mean([r['optimism_drop'] for r in rows if 'optimism_drop' in r])),
                measurement_waiver=policy.get('measurement_waiver'),
                reason='lower bound, positive unit-bootstrap CI and majority backtests required')


def decide_v2(finalists, floor, n_candidates, margin_sd, *, policy, evidence,
              ledger_path, optimism_path, ensemble_scorer=None):
    validate_policy(policy)
    if ledger_path is None or optimism_path is None:
        raise ValueError('v2 requires sealed scoring ledger_path and per-lane optimism_path')
    if not isinstance(evidence, dict):
        raise ValueError('v2 requires evidence')
    competition, scoring_id = evidence.get('competition'), evidence.get('scoring_id')
    # Consume even if the subsequently scored candidates fail a statistical check.
    entry = consume_scoring(ledger_path, policy, competition, scoring_id)
    prior, prior_anchor = optimism_snapshot(optimism_path, competition)
    decision = evaluate_v2(finalists, floor, n_candidates, margin_sd, policy, evidence,
                           prior=prior, ensemble_scorer=ensemble_scorer)
    opt = record_optimism(optimism_path, competition, scoring_id,
                         [r['optimism_drop'] for r in decision['finalists'] if 'optimism_drop' in r],
                         prior_sha256=prior_anchor)
    decision.update(scoring_id=scoring_id, competition=competition, optimism_prior_sha256=prior_anchor,
                    scoring_ledger_sha256=entry['sha256'], optimism_ledger_sha256=opt['sha256'])
    return decision


def score_and_decide(scorer, floor, n_candidates, *, policy, evidence, ledger_path,
                     optimism_path, ensemble_scorer=None):
    """Reserve a sealed use before invoking a scorer, even if it fails.

    scorer() returns finalist objects. The producer must never get hidden targets;
    this wrapper belongs to the operator running the sealed metric. Every call
    consumes one evaluation of the complete finalist batch, including baseline.
    """
    validate_policy(policy)
    if not callable(scorer) or ledger_path is None or optimism_path is None or not isinstance(evidence, dict):
        raise ValueError('sealed scorer, evidence and durable lane ledgers required')
    competition, scoring_id = evidence.get('competition'), evidence.get('scoring_id')
    entry = consume_scoring(ledger_path, policy, competition, scoring_id)
    prior, prior_anchor = optimism_snapshot(optimism_path, competition)
    finalists = scorer()
    decision = evaluate_v2(finalists, floor, n_candidates, 2, policy, evidence,
                           prior=prior, ensemble_scorer=ensemble_scorer)
    opt = record_optimism(optimism_path, competition, scoring_id,
                         [r['optimism_drop'] for r in decision['finalists'] if 'optimism_drop' in r],
                         prior_sha256=prior_anchor)
    decision.update(scoring_id=scoring_id, competition=competition, optimism_prior_sha256=prior_anchor,
                    scoring_ledger_sha256=entry['sha256'], optimism_ledger_sha256=opt['sha256'])
    return decision


def best_of_choice(finalists, current_best=None, n_finalists=None, *, current_sealed_scores=None):
    """Choose the last attempt on a best-of-m board, where only the best submission counts.

    A last attempt cannot lower the board score, so the lower-bound rule in `decide` protects against a loss
    that cannot happen. The objective is E[max(current_best, X)]. X is one run of a finalist, modelled as
    Normal(mean - optimism, sd): the mean is shrunk by the max-of-k optimism the gate already uses, and the
    spread is the seed SD (a single submission, not a mean). Returns the finalist with the largest expected
    gain over current_best; ties keep nomination order. Purely advisory: it records, it does not submit.

    UNITS: current_best must be on the SAME scale as the sealed seed scores. Never pass a public board number:
    boards and sealed proxies need not be calibrated. Prefer
    current_sealed_scores, the already-submitted file scored on the same sealed panel; its mean is used.
    """
    import math
    import numpy as np
    if (current_best is None) == (current_sealed_scores is None):
        raise ValueError('give exactly one of current_sealed_scores (preferred) or current_best on the sealed scale')
    if current_sealed_scores is not None:
        cs = np.asarray(current_sealed_scores, dtype=float)
        if cs.ndim != 1 or len(cs) < 1 or not np.isfinite(cs).all():
            raise ValueError('current_sealed_scores must be finite sealed-panel scores')
        current_best = float(cs.mean())
    current = float(current_best)
    if not math.isfinite(current):
        raise ValueError('current_best must be finite')
    rows = []
    for candidate in finalists:
        scores = np.asarray(candidate['sealed_scores'], dtype=float)
        if scores.ndim != 1 or len(scores) < 2 or not np.isfinite(scores).all():
            raise ValueError('at least two finite seed scores required')
        rows.append((candidate['name'], float(scores.mean()), float(scores.std(ddof=1))))
    if not rows:
        return dict(current_best=current, selected=None, reason='no finalists', finalists=[])
    k = n_finalists or len(rows)
    out = []
    for name, mean, sd in rows:
        shrunk = mean - (sd * math.sqrt(2 * math.log(k)) if k > 1 else 0.0)
        if sd == 0:
            gain = max(shrunk - current, 0.0)
        else:
            z = (shrunk - current) / sd
            gain = (shrunk - current) * 0.5 * (1 + math.erf(z / math.sqrt(2))) + sd * math.exp(-z * z / 2) / math.sqrt(2 * math.pi)
        out.append(dict(name=name, mean=mean, sd=sd, shrunk_mean=shrunk, expected_gain=gain,
                        expected_score=current + gain))
    best = max(out, key=lambda r: r['expected_gain'])
    return dict(current_best=current, n_finalists=k, finalists=out, selected=best['name'],
                expected_score=best['expected_score'], objective='E[max(current_best, X)]')
