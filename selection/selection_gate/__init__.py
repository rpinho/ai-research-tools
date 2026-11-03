"""Challenge-independent, auditable sealed selection; numpy and stdlib only."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import numpy as np


def optimism(sd, n_candidates):
    sd = float(sd)
    if not math.isfinite(sd) or sd < 0 or isinstance(n_candidates, bool) or int(n_candidates) != n_candidates or n_candidates < 1:
        raise ValueError("finite nonnegative sd and positive integer candidate count required")
    return sd * math.sqrt(2 * math.log(n_candidates))


def decide(finalists, floor, n_candidates, margin_sd=2.0, *, policy_version=1,
           policy=None, evidence=None, ledger_path=None, optimism_path=None, ensemble_scorer=None):
    """Correct for sealed nominations, recording historical search optimism separately.

    Seed SD is sample SD; at least two finite seed scores are required. Search
    count is provenance, not the number of independent sealed comparisons.
    """
    if type(policy_version) is not int or policy_version not in (1, 2):
        raise ValueError('unsupported policy_version')
    if policy_version == 2:
        from .v2 import decide_v2
        return decide_v2(finalists, floor, n_candidates, margin_sd, policy=policy,
                         evidence=evidence, ledger_path=ledger_path,
                         optimism_path=optimism_path, ensemble_scorer=ensemble_scorer)
    if any(value is not None for value in (policy, evidence, ledger_path, optimism_path, ensemble_scorer)):
        raise ValueError('v2 arguments require explicit policy_version=2')
    optimism(0, n_candidates)
    if not math.isfinite(floor) or not math.isfinite(margin_sd) or margin_sd < 0:
        raise ValueError("invalid floor or margin")
    rows = []
    names = set()
    for candidate in finalists:
        name = candidate['name']
        if not isinstance(name, str) or not name or name in names or name == 'floor':
            raise ValueError("unique nonempty finalist names required; floor is reserved")
        names.add(name)
        scores = np.asarray(candidate['sealed_scores'], dtype=float)
        if scores.ndim != 1 or len(scores) < 2 or not np.isfinite(scores).all():
            raise ValueError("at least two finite seed scores required")
        sd = float(scores.std(ddof=1))
        rows.append(dict(name=name, sealed_scores=scores.tolist(), n_seeds=len(scores),
                         mean=float(scores.mean()), sd=sd, sem=sd/math.sqrt(len(scores))))
    result = dict(floor=float(floor), n_candidates=int(n_candidates), n_finalists=len(rows),
                  margin_sd=float(margin_sd), finalists=rows, selected='floor', accepted=False)
    if not rows:
        return dict(result, reason='no finalists')
    best = max(rows, key=lambda r: r['mean'])  # stable nomination-order tie break
    bias = optimism(best['sd'], len(rows))
    margin = margin_sd * best['sem']
    bound = best['mean'] - bias - margin
    accepted = bound > floor
    return dict(result, best=best['name'], mean=best['mean'], sd=best['sd'], sem=best['sem'],
                optimism=bias, search_optimism=optimism(best['sd'], n_candidates),
                margin=margin, lower_bound=bound, accepted=accepted,
                selected=best['name'] if accepted else 'floor',
                reason='lower bound strictly exceeds floor' if accepted else 'lower bound does not strictly exceed floor')


def write_receipt(path, decision, file_sha256, run_id, board):
    if len(file_sha256) != 64 or any(c not in '0123456789abcdef' for c in file_sha256):
        raise ValueError("invalid sha256")
    receipt = dict(version=1, decision=decision, file_sha256=file_sha256, run_id=run_id, board=board)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Receipts cannot silently replace previous sealed decisions.
    with path.open('x') as out:
        json.dump(receipt, out, indent=2, allow_nan=False)
        out.write('\n')
    return receipt


def verify_receipt(path, file_path):
    receipt = json.loads(Path(path).read_text())
    if receipt.get('version') != 1:
        raise ValueError('unsupported receipt version')
    digest = hashlib.sha256()
    with Path(file_path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != receipt['file_sha256']:
        raise ValueError('receipt SHA256 mismatch')
    return receipt


def trajectory_scan(paths, forbidden_patterns):
    """Literal substrings, not regexes; also inspect JSON-decoded escaped paths."""
    patterns = tuple(str(p) for p in forbidden_patterns if str(p))
    hits = []
    def strings(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for k, v in obj.items():
                yield from strings(k)
                yield from strings(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from strings(v)
    for path in paths:
        path = Path(path)
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', errors='replace') as stream:
            for line_no, line in enumerate(stream, 1):
                texts = [line]
                try:
                    texts.extend(strings(json.loads(line)))
                except ValueError:
                    pass
                for pattern in patterns:
                    source = next((s for s in texts if pattern in s), None)
                    if source is not None:
                        start = max(0, source.index(pattern)-60)
                        hits.append(dict(file=str(path), line_no=line_no, pattern=pattern,
                                         snippet=source[start:start+240].strip()))
    return hits


# New helpers are opt-in; the legacy arithmetic and receipt format above are unchanged.
from .v2 import bootstrap_diff, backtests, backtest_summary, simple_average_ensemble, score_and_decide
