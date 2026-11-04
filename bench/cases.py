"""Frozen benchmark v1 inputs and budgets. Changes require a new benchmark version."""
import hashlib
import json

VERSION = 'mock-1104-v1'
CASES = {
    'concrete': dict(
        competition='playground-series-s3e9', target='Strength', metric='rmse',
        rows=5407, max_groups=1600, bytes=277194,
        url='https://raw.githubusercontent.com/khushdeeppsingh/personal/'
            '363caf6fe5848923c11e34d8e81fbd0988c9c555/playground-series-s3e9/train.csv',
        sha256='6ce513646729e57c4e1eeedaa0310a2ca5e3ffef905dc09f3839b391fc393bb7'),
    'crab': dict(
        competition='playground-series-s3e16', target='Age', metric='mae',
        rows=74051, max_groups=1600, bytes=5207706,
        url='https://raw.githubusercontent.com/srikresna/playground-series-s3e16/'
            '6c92088508075bacd712049f75d70a96301a5b8b/train.csv',
        sha256='f22feaa2e7d667cf3ec95c897bb8d7f8018a8dab19c9f21e740911820d1efd69'),
}
SETUPS = {'v1-default': 1, 'v2-selection': 2}
FAMILIES = ('ridge', 'extra_trees', 'hist_gradient_boosting')
FRESH_SEEDS = (101, 102)
TUNED_SEED = 7
CPU_SECONDS = 180
PROCESS_SECONDS = 240
MODEL_SECONDS = 180


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def policy(case):
    return dict(
        policy_version=2, split='group',
        split_reason='Feature-identical rows stay together; fixed hash groups from public synthetic training data.',
        preprocessing_inside_folds=True, augmentation_used=False, final_fit='fixed_steps',
        n_trials_total=0, trials=[], sealed_budget=1,
        sealed_set_id=VERSION + '/' + case + '/selection', submissions=1,
        min_measurements=2,
        measurement_waiver=dict(reason='Cheap regression smoke check: two fresh bootstrap-fit seeds, '
                                     'not ten; no scientific improvement or independence of source subjects claimed.'),
        backtest_margin=0)


def freeze_spec(case):
    return dict(version=VERSION, case=case, source=CASES[case], policy=policy(case),
                group_key='SHA256(canonical JSON of feature strings excluding id and target)',
                sample='first 1600 distinct feature groups ordered by SHA256(version + /sample/ + group)',
                partition='SHA256(version + /split/ + group) modulo 100: '
                          '0:49 train; 50:69 dev; 70:84 selection; 85:99 hidden',
                tuned_seed=TUNED_SEED, fresh_seeds=list(FRESH_SEEDS),
                families=list(FAMILIES), strength_choices=['small', 'medium'],
                backtests='two expanding windows over three hash-ordered development group blocks; not time forecasts',
                cpu_seconds=CPU_SECONDS, process_seconds=PROCESS_SECONDS,
                final_fit='average predictions from two fixed bootstrap training fits; no refit after selection',
                submission_limit=1, pass_bar='finite score; flags detect failures or >2x spend/time, not score deltas')


def stub_plan(cases):
    return dict(sequence=[i*i for i in range(10)], recipes={case: [
        dict(family=f, strength='small') for f in FAMILIES] for case in cases if case != 'toy'})


def validate_plan(plan, cases):
    if not isinstance(plan, dict) or set(plan) != {'sequence', 'recipes'}:
        raise ValueError('plan must contain sequence and recipes only')
    if plan['sequence'] != [i*i for i in range(10)]:
        raise ValueError('toy sequence failed exact arithmetic verification')
    if not isinstance(plan['recipes'], dict) or set(plan['recipes']) != set(cases) - {'toy'}:
        raise ValueError('one recipe list per real case required')
    for recipes in plan['recipes'].values():
        if not isinstance(recipes, list) or len(recipes) != 3:
            raise ValueError('exactly three finalists required')
        if {r.get('family') for r in recipes} != set(FAMILIES):
            raise ValueError('each supported model family must appear once')
        for recipe in recipes:
            if set(recipe) != {'family', 'strength'} or recipe['strength'] not in ('small', 'medium'):
                raise ValueError('invalid bounded recipe')
    return plan
